# Copyright 2026 Ammaar Ahmed
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import traceback
import uuid

from .artifacts import discover_benchmark_artifacts
from .benchmark_runner import benchmark_runner
from .dataset import build_dataset
from .dataset import DatasetError
from .dataset import manifest_path_for
from .dataset import validate_normalized_inputs
from .dataset import verify_dataset_bundle
from .parsers.ros2_benchmark_container import latest_run_metadata
from .parsers.ros2_benchmark_container import parse_artifact
from .run_metadata import generation_rundata
from .writers.jsonl import write_json
from .writers.jsonl import write_jsonl


PLAN_FILENAME = 'plan.json'
EXPERIMENT_COMPLETE_FILENAME = 'experiment.complete.json'
ENVIRONMENT_FILENAME = 'measured_environment.json'
TARGET_LABELS = ('reference', 'candidate')


class ExperimentError(RuntimeError):
    """Report invalid plans, unsafe resume state, or failed trials."""


@dataclass(frozen=True)
class ExperimentResult:
    """Summarize a completed experiment bundle."""

    experiment_id: str
    experiment_dir: Path
    dataset_path: Path
    completed_trials: int
    reused_trials: int


def build_experiment_plan(
    image_specs,
    verified_images,
    *,
    suite,
    executor,
    duration,
    cpuset_cpus,
    warmup_count,
    measured_repeat_count,
    order,
    seed,
    experiment_id=None,
    created_at=None,
):
    """Build an immutable experiment plan with a deterministic trial schedule."""
    _validate_targets(image_specs, verified_images)
    if type(duration) is not int or duration < 1:
        raise ExperimentError('experiment duration must be a positive integer')
    if type(warmup_count) is not int or warmup_count < 0:
        raise ExperimentError('warm-up count must be a non-negative integer')
    if type(measured_repeat_count) is not int or measured_repeat_count < 1:
        raise ExperimentError('measured repeat count must be a positive integer')
    if order not in ('balanced', 'interleaved'):
        raise ExperimentError(f'unsupported trial order: {order!r}')
    if type(seed) is not int:
        raise ExperimentError('scheduling seed must be an integer')
    _selected_cpus(cpuset_cpus)

    targets = []
    for label in TARGET_LABELS:
        spec = image_specs[label]
        verified = verified_images[label]
        targets.append({
            'label': label,
            'target_key': spec.target_key,
            'identity': spec.identity_payload(),
            'verified_image': _verified_image_dict(verified),
        })
    if targets[0]['target_key'] == targets[1]['target_key']:
        raise ExperimentError('reference and candidate targets must be different')

    schedule = _trial_schedule(
        targets,
        warmup_count,
        measured_repeat_count,
        order,
        seed,
    )
    return {
        'schema_version': 1,
        'experiment_id': experiment_id or f'experiment-{uuid.uuid4().hex}',
        'created_at': created_at or _utc_now(),
        'configuration': {
            'ros_distro': image_specs['reference'].ros_distro,
            'suite': suite,
            'executor': executor,
            'duration': duration,
            'cpuset_cpus': cpuset_cpus,
        },
        'targets': targets,
        'schedule': {
            'order': order,
            'seed': seed,
            'warmup_count': warmup_count,
            'measured_repeat_count': measured_repeat_count,
            'trials': schedule,
        },
    }


def run_experiment(
    experiment_dir,
    requested_plan,
    image_specs,
    verified_images,
    trial_executor=None,
    environment_collector=None,
):
    """Execute or safely resume an immutable local experiment bundle."""
    root = Path(experiment_dir).expanduser().resolve()
    plan = _publish_or_validate_plan(root, requested_plan)
    _validate_runtime_targets(plan, image_specs, verified_images)
    if _verify_experiment_completion(root, plan):
        return ExperimentResult(
            experiment_id=plan['experiment_id'],
            experiment_dir=root,
            dataset_path=root / 'dataset' / 'dashboard-data.jsonl',
            completed_trials=len(plan['schedule']['trials']),
            reused_trials=len(plan['schedule']['trials']),
        )
    _remove_file(root / EXPERIMENT_COMPLETE_FILENAME)
    _recover_interrupted_attempts(root, plan)

    execute = trial_executor or _execute_trial
    collect_environment = environment_collector or collect_environment_evidence
    completed = []
    reused = 0
    for trial in plan['schedule']['trials']:
        completed_trial = _verified_trial(root, trial)
        if completed_trial is not None:
            completed.append(completed_trial)
            reused += 1
            continue
        evidence = collect_environment(
            plan,
            trial,
            image_specs[trial['target']],
            verified_images[trial['target']],
        )
        if trial['kind'] == 'measured':
            _validate_measured_environment(root, evidence)
        completed.append(_run_trial_attempt(
            root,
            plan,
            trial,
            image_specs[trial['target']],
            verified_images[trial['target']],
            evidence,
            execute,
        ))

    measured_inputs = [
        item['normalized_path']
        for trial, item in zip(plan['schedule']['trials'], completed)
        if trial['kind'] == 'measured'
    ]
    dataset_path = root / 'dataset' / 'dashboard-data.jsonl'
    aggregate = 'median' if plan['schedule']['measured_repeat_count'] > 1 else None
    build_dataset(measured_inputs, dataset_path, aggregate=aggregate)
    dataset_manifest = verify_dataset_bundle(dataset_path)
    completion = {
        'schema_version': 1,
        'experiment_id': plan['experiment_id'],
        'completed_at': _utc_now(),
        'plan_sha256': _file_sha256(root / PLAN_FILENAME),
        'dataset': str(dataset_path.relative_to(root)),
        'dataset_sha256': dataset_manifest['dataset_sha256'],
        'dataset_manifest_sha256': _file_sha256(manifest_path_for(dataset_path)),
        'trial_completion_sha256': {
            trial['trial_id']: _file_sha256(root / 'trials' / trial['trial_id'] / 'complete.json')
            for trial in plan['schedule']['trials']
        },
    }
    write_json(completion, root / EXPERIMENT_COMPLETE_FILENAME)
    return ExperimentResult(
        experiment_id=plan['experiment_id'],
        experiment_dir=root,
        dataset_path=dataset_path,
        completed_trials=len(completed),
        reused_trials=reused,
    )


def collect_environment_evidence(plan, trial, image_spec, verified_image):
    """Collect host, configuration, and verified-target evidence for one trial."""
    configuration = plan['configuration']
    docker_version = subprocess.run(
        ['docker', 'version', '--format', '{{.Server.Version}}'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        'captured_at': _utc_now(),
        'host': {
            'architecture': platform.machine(),
            'cpu_model': _cpu_model(),
            'kernel': platform.release(),
            'docker_version': docker_version,
            'cpuset_cpus': configuration['cpuset_cpus'],
            'cpu_governors': _cpu_governors(configuration['cpuset_cpus']),
        },
        'configuration': dict(configuration),
        'trial': {
            'trial_id': trial['trial_id'],
            'kind': trial['kind'],
            'target': trial['target'],
            'sequence': trial['sequence'],
            'planned_order': trial['planned_order'],
        },
        'target': {
            'target_key': image_spec.target_key,
            'benchmark_commit': image_spec.benchmark_resolved_commit,
            'image': _verified_image_dict(verified_image),
        },
    }


def _execute_trial(stage, plan, trial, image_spec, verified_image):
    configuration = plan['configuration']
    args = argparse.Namespace(
        ros_distro=configuration['ros_distro'],
        executor=configuration['executor'],
        duration=configuration['duration'],
        cpuset_cpus=configuration['cpuset_cpus'],
        suite=configuration['suite'],
    )
    generation_rundata(
        args,
        str(stage),
        image_spec,
        verified_image,
        metadata_filename='metadata.json',
        run_id=trial['trial_id'],
    )
    benchmark_runner(
        results_dir=str(stage),
        benchmark_option=configuration['suite'],
        duration=configuration['duration'],
        image_spec=image_spec,
        executor=configuration['executor'],
        keep_container=False,
        cpuset_cpus=configuration['cpuset_cpus'],
        log_path=stage / 'trial.log',
    )
    metadata = latest_run_metadata(stage)
    artifacts = discover_benchmark_artifacts(
        stage,
        ros_distro=configuration['ros_distro'],
    )
    records = []
    for artifact in artifacts:
        records.extend(parse_artifact(artifact, metadata))
    write_jsonl(records, stage / 'normalized_metrics.jsonl')


def _run_trial_attempt(
    root,
    plan,
    trial,
    image_spec,
    verified_image,
    evidence,
    execute,
):
    trial_root = root / 'trials' / trial['trial_id']
    attempts_dir = trial_root / 'attempts'
    attempts_dir.mkdir(parents=True, exist_ok=True)
    _remove_file(trial_root / 'complete.json')
    attempt_number = _next_attempt_number(attempts_dir)
    attempt_name = f'{attempt_number:04d}'
    stage = attempts_dir / f'.{attempt_name}.staging'
    stage.mkdir()
    started_at = _utc_now()
    status = _trial_status(trial, attempt_name, 'running', started_at)
    write_json(status, trial_root / 'status.json')
    write_json(status, stage / 'status.json')
    write_json(evidence, stage / 'environment.json')
    (stage / 'trial.log').write_text(f'Trial started at {started_at}\n', encoding='utf-8')

    try:
        execute(stage, plan, trial, image_spec, verified_image)
        completed_at = _utc_now()
        status = _trial_status(
            trial,
            attempt_name,
            'completed',
            started_at,
            completed_at,
        )
        write_json(status, stage / 'status.json')
        output_manifest = _validate_trial_outputs(stage, trial)
        attempt_complete = {
            'schema_version': 1,
            'trial_id': trial['trial_id'],
            'attempt': attempt_name,
            'completed_at': completed_at,
            'files': output_manifest,
        }
        write_json(attempt_complete, stage / 'attempt.complete.json')
        final_attempt = attempts_dir / attempt_name
        os.replace(stage, final_attempt)
        _fsync_directory(attempts_dir)
        completion = {
            **attempt_complete,
            'attempt_path': str(final_attempt.relative_to(trial_root)),
            'attempt_complete_sha256': _file_sha256(final_attempt / 'attempt.complete.json'),
        }
        write_json(status, trial_root / 'status.json')
        write_json(completion, trial_root / 'complete.json')
        return {
            'normalized_path': final_attempt / 'normalized_metrics.jsonl',
            'completion': completion,
        }
    except BaseException as exc:
        failed_at = _utc_now()
        failed_output = stage if stage.exists() else attempts_dir / attempt_name
        with (failed_output / 'trial.log').open('a', encoding='utf-8') as log:
            log.write(f'Trial failed at {failed_at}: {exc}\n')
            log.write(traceback.format_exc())
        status = _trial_status(
            trial,
            attempt_name,
            'failed',
            started_at,
            failed_at,
            str(exc),
        )
        write_json(status, failed_output / 'status.json')
        suffix = 'failed' if stage.exists() else 'incomplete'
        failed_attempt = attempts_dir / f'{attempt_name}-{suffix}'
        os.replace(failed_output, failed_attempt)
        _fsync_directory(attempts_dir)
        write_json(status, trial_root / 'status.json')
        raise ExperimentError(
            f'trial {trial["trial_id"]} failed; diagnostics kept in {failed_attempt}'
        ) from exc


def _validate_trial_outputs(stage, trial):
    metadata_path = stage / 'metadata.json'
    normalized_path = stage / 'normalized_metrics.jsonl'
    raw_root = stage / 'benchmark'
    for path in (metadata_path, normalized_path):
        if not path.is_file():
            raise ExperimentError(f'trial {trial["trial_id"]} did not produce {path.name}')
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ExperimentError(f'trial {trial["trial_id"]} metadata is invalid JSON') from exc
    if metadata.get('run_id') != trial['trial_id']:
        raise ExperimentError(f'trial {trial["trial_id"]} metadata has the wrong run ID')
    try:
        run_ids = validate_normalized_inputs((normalized_path,))
    except DatasetError as exc:
        raise ExperimentError(
            f'trial {trial["trial_id"]} normalized data is invalid: {exc}'
        ) from exc
    if run_ids != (trial['trial_id'],):
        raise ExperimentError(f'trial {trial["trial_id"]} normalized data has the wrong run ID')
    raw_files = [
        path for path in raw_root.rglob('*')
        if path.is_file() and '.ros2_performance_monitoring' not in path.parts
    ] if raw_root.is_dir() else []
    if not raw_files:
        raise ExperimentError(f'trial {trial["trial_id"]} has no raw benchmark artifacts')
    return {
        str(path.relative_to(stage)): _file_sha256(path)
        for path in sorted(path for path in stage.rglob('*') if path.is_file())
    }


def _verified_trial(root, trial):
    trial_root = root / 'trials' / trial['trial_id']
    complete_path = trial_root / 'complete.json'
    try:
        completion = json.loads(complete_path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if completion.get('trial_id') != trial['trial_id']:
        return None
    attempt_path = completion.get('attempt_path')
    files = completion.get('files')
    if not isinstance(attempt_path, str) or not isinstance(files, dict):
        return None
    attempt = trial_root / attempt_path
    attempt_complete = attempt / 'attempt.complete.json'
    if (
        not attempt_complete.is_file()
        or _file_sha256(attempt_complete) != completion.get('attempt_complete_sha256')
    ):
        return None
    for relative_path, checksum in files.items():
        if not isinstance(relative_path, str) or not isinstance(checksum, str):
            return None
        path = attempt / relative_path
        if not path.is_file() or _file_sha256(path) != checksum:
            return None
    try:
        run_ids = validate_normalized_inputs((attempt / 'normalized_metrics.jsonl',))
    except DatasetError:
        return None
    if run_ids != (trial['trial_id'],):
        return None
    return {
        'normalized_path': attempt / 'normalized_metrics.jsonl',
        'completion': completion,
    }


def _validate_measured_environment(root, evidence):
    baseline_path = root / ENVIRONMENT_FILENAME
    identity = _environment_identity(evidence)
    if not baseline_path.exists():
        write_json(identity, baseline_path)
        return
    try:
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ExperimentError(f'invalid measured environment evidence: {baseline_path}') from exc
    if identity == baseline:
        return
    mismatches = sorted(
        key for key in set(identity) | set(baseline)
        if identity.get(key) != baseline.get(key)
    )
    raise ExperimentError(
        'measured environment changed before trial: ' + ', '.join(mismatches)
    )


def _environment_identity(evidence):
    host = evidence['host']
    return {
        'architecture': host['architecture'],
        'cpu_model': host['cpu_model'],
        'kernel': host['kernel'],
        'docker_version': host['docker_version'],
        'cpuset_cpus': host['cpuset_cpus'],
        'cpu_governors': host['cpu_governors'],
    }


def _publish_or_validate_plan(root, requested):
    plan_path = root / PLAN_FILENAME
    root.mkdir(parents=True, exist_ok=True)
    if not plan_path.exists():
        unexpected = [path for path in root.iterdir() if path.name != PLAN_FILENAME]
        if unexpected:
            raise ExperimentError(
                f'cannot create an experiment in non-empty directory without {PLAN_FILENAME}'
            )
        write_json(requested, plan_path)
        return requested
    try:
        existing = json.loads(plan_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ExperimentError(f'invalid experiment plan: {plan_path}') from exc
    if _immutable_plan(existing) != _immutable_plan(requested):
        raise ExperimentError(
            'experiment plan does not match the existing immutable configuration; '
            'use a new experiment directory'
        )
    return existing


def _immutable_plan(plan):
    return {
        key: plan.get(key)
        for key in ('schema_version', 'configuration', 'targets', 'schedule')
    }


def _validate_targets(image_specs, verified_images):
    if set(image_specs) != set(TARGET_LABELS) or set(verified_images) != set(TARGET_LABELS):
        raise ExperimentError('experiments require reference and candidate targets')
    reference = image_specs['reference']
    candidate = image_specs['candidate']
    if reference.ros_distro != candidate.ros_distro:
        raise ExperimentError('experiment targets must use the same ROS distribution')
    for label in TARGET_LABELS:
        spec = image_specs[label]
        verified = verified_images[label]
        if spec.client_target.source not in ('build', 'packaged'):
            raise ExperimentError(f'{label} target source is not verified or packaged')
        if verified.target_key != spec.target_key:
            raise ExperimentError(f'{label} verified image does not match its target')


def _validate_runtime_targets(plan, image_specs, verified_images):
    _validate_targets(image_specs, verified_images)
    planned = {target['label']: target for target in plan['targets']}
    for label in TARGET_LABELS:
        expected = planned.get(label)
        actual_spec = image_specs[label]
        actual_image = verified_images[label]
        if expected is None or expected['identity'] != actual_spec.identity_payload():
            raise ExperimentError(f'{label} target identity changed; use a new experiment')
        if expected['verified_image'] != _verified_image_dict(actual_image):
            raise ExperimentError(f'{label} verified image identity changed; use a new experiment')


def _trial_schedule(targets, warmups, repeats, order, seed):
    randomizer = random.Random(seed)
    target_keys = {target['label']: target['target_key'] for target in targets}
    schedule = []
    for kind, count in (('warmup', warmups), ('measured', repeats)):
        starting_order = list(TARGET_LABELS)
        randomizer.shuffle(starting_order)
        for sequence in range(1, count + 1):
            labels = list(starting_order)
            if order == 'balanced' and sequence % 2 == 0:
                labels.reverse()
            elif order == 'interleaved':
                randomizer.shuffle(labels)
            for label in labels:
                key = target_keys[label]
                trial_id = f'{label}-{kind}-{sequence:03d}-{key[:12]}'
                schedule.append({
                    'trial_id': trial_id,
                    'kind': kind,
                    'target': label,
                    'target_key': key,
                    'sequence': sequence,
                    'planned_order': len(schedule) + 1,
                })
    return schedule


def _verify_experiment_completion(root, plan):
    path = root / EXPERIMENT_COMPLETE_FILENAME
    try:
        completion = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    if completion.get('experiment_id') != plan['experiment_id']:
        return False
    if completion.get('plan_sha256') != _file_sha256(root / PLAN_FILENAME):
        return False
    dataset = completion.get('dataset')
    if not isinstance(dataset, str):
        return False
    dataset_path = root / dataset
    try:
        dataset_manifest = verify_dataset_bundle(dataset_path)
    except DatasetError:
        return False
    if dataset_manifest['dataset_sha256'] != completion.get('dataset_sha256'):
        return False
    if _file_sha256(manifest_path_for(dataset_path)) != completion.get(
        'dataset_manifest_sha256'
    ):
        return False
    expected_trials = completion.get('trial_completion_sha256', {})
    for trial in plan['schedule']['trials']:
        complete_path = root / 'trials' / trial['trial_id'] / 'complete.json'
        if not complete_path.is_file():
            return False
        if _file_sha256(complete_path) != expected_trials.get(trial['trial_id']):
            return False
        if _verified_trial(root, trial) is None:
            return False
    return True


def _recover_interrupted_attempts(root, plan):
    for trial in plan['schedule']['trials']:
        attempts = root / 'trials' / trial['trial_id'] / 'attempts'
        if not attempts.is_dir():
            continue
        for stage in sorted(attempts.glob('.*.staging')):
            attempt = stage.name.removeprefix('.').removesuffix('.staging')
            destination = attempts / f'{attempt}-incomplete'
            if destination.exists():
                destination = attempts / f'{attempt}-incomplete-{uuid.uuid4().hex[:8]}'
            os.replace(stage, destination)
            _fsync_directory(attempts)


def _trial_status(trial, attempt, outcome, started_at, ended_at=None, error=None):
    status = {
        'schema_version': 1,
        'trial_id': trial['trial_id'],
        'kind': trial['kind'],
        'target': trial['target'],
        'sequence': trial['sequence'],
        'planned_order': trial['planned_order'],
        'attempt': attempt,
        'started_at': started_at,
        'ended_at': ended_at,
        'outcome': outcome,
    }
    if error is not None:
        status['error'] = error
    return status


def _next_attempt_number(attempts_dir):
    numbers = []
    for path in attempts_dir.iterdir():
        name = path.name.removeprefix('.')
        prefix = name.split('-', 1)[0].split('.', 1)[0]
        if prefix.isdigit():
            numbers.append(int(prefix))
    return max(numbers, default=0) + 1


def _verified_image_dict(image):
    return {
        'name': image.image_name,
        'id': image.image_id,
        'digest': image.image_digest,
        'target_key': image.target_key,
    }


def _cpu_model():
    try:
        lines = Path('/proc/cpuinfo').read_text(encoding='utf-8').splitlines()
    except OSError:
        return platform.processor() or 'unknown'
    for line in lines:
        key, separator, value = line.partition(':')
        if separator and key.strip() in ('model name', 'Hardware', 'Processor'):
            return value.strip()
    return platform.processor() or 'unknown'


def _cpu_governors(cpuset_cpus):
    selected = _selected_cpus(cpuset_cpus)
    governors = {}
    for path in sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_governor')):
        cpu = path.parent.parent.name.removeprefix('cpu')
        if selected is not None and int(cpu) not in selected:
            continue
        try:
            governors[cpu] = path.read_text(encoding='utf-8').strip()
        except OSError:
            governors[cpu] = 'unavailable'
    return governors


def _selected_cpus(expression):
    if not expression:
        return None
    selected = set()
    try:
        for item in expression.split(','):
            if '-' in item:
                start, end = (int(value) for value in item.split('-', 1))
                if start > end:
                    raise ValueError
                selected.update(range(start, end + 1))
            else:
                selected.add(int(item))
    except ValueError as exc:
        raise ExperimentError(f'invalid CPU-set expression: {expression!r}') from exc
    return selected


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _remove_file(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()
