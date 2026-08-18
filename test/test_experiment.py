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

from copy import deepcopy
import json
from pathlib import Path

import pytest
from ros2_performance_monitoring import experiment as experiment_module
from ros2_performance_monitoring.benchmark_image import BenchmarkImageSpec
from ros2_performance_monitoring.benchmark_image import VerifiedImage
from ros2_performance_monitoring.client_target import ClientLibraryTarget
from ros2_performance_monitoring.dataset import verify_dataset_bundle
from ros2_performance_monitoring.experiment import build_experiment_plan
from ros2_performance_monitoring.experiment import ExperimentError
from ros2_performance_monitoring.experiment import run_experiment
from ros2_performance_monitoring.writers.jsonl import write_json


def test_same_plan_and_seed_produce_same_balanced_order():
    specs, images = _targets()

    first = _plan(specs, images, warmups=2, repeats=3, seed=73)
    second = _plan(specs, images, warmups=2, repeats=3, seed=73)

    assert first['schedule']['trials'] == second['schedule']['trials']
    measured = [
        trial['target'] for trial in first['schedule']['trials']
        if trial['kind'] == 'measured'
    ]
    assert measured[0:2] == list(reversed(measured[2:4]))
    assert measured[2:4] == list(reversed(measured[4:6]))


def test_different_targets_have_unique_trial_ids_images_and_directories(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=1)

    result = run_experiment(
        tmp_path / 'experiment',
        plan,
        specs,
        images,
        trial_executor=_successful_trial,
        environment_collector=_environment,
    )

    trials = plan['schedule']['trials']
    assert len({trial['trial_id'] for trial in trials}) == 2
    assert len({trial['target_key'] for trial in trials}) == 2
    assert images['reference'].image_id != images['candidate'].image_id
    assert all(
        (result.experiment_dir / 'trials' / trial['trial_id']).is_dir()
        for trial in trials
    )


def test_warmups_complete_but_never_enter_dataset_or_aggregate_lineage(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=1, repeats=2)
    calls = []

    def execute(stage, current_plan, trial, image_spec, verified_image):
        calls.append(trial['trial_id'])
        _successful_trial(stage, current_plan, trial, image_spec, verified_image)

    result = run_experiment(
        tmp_path / 'experiment',
        plan,
        specs,
        images,
        trial_executor=execute,
        environment_collector=_environment,
    )

    assert calls == [trial['trial_id'] for trial in plan['schedule']['trials']]
    records = _read_jsonl(result.dataset_path)
    measured_ids = {
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'measured'
    }
    warmup_ids = {
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'warmup'
    }
    dataset_ids = {record['run_id'] for record in records}
    assert measured_ids <= dataset_ids
    assert not warmup_ids & dataset_ids
    manifest = verify_dataset_bundle(result.dataset_path)
    aggregate_sources = {
        source
        for aggregate in manifest['aggregates']
        for source in aggregate['source_run_ids']
    }
    assert aggregate_sources == measured_ids
    assert (result.experiment_dir / 'experiment.complete.json').is_file()


def test_resume_reuses_only_checksum_valid_trials(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=2)
    root = tmp_path / 'experiment'
    first = run_experiment(
        root,
        plan,
        specs,
        images,
        trial_executor=_successful_trial,
        environment_collector=_environment,
    )
    damaged_trial = plan['schedule']['trials'][1]
    completion = json.loads(
        (root / 'trials' / damaged_trial['trial_id'] / 'complete.json').read_text()
    )
    damaged_output = (
        root / 'trials' / damaged_trial['trial_id']
        / completion['attempt_path'] / 'normalized_metrics.jsonl'
    )
    damaged_output.write_text(damaged_output.read_text() + 'not-json\n')
    rerun = []

    def execute(stage, current_plan, trial, image_spec, verified_image):
        rerun.append(trial['trial_id'])
        _successful_trial(stage, current_plan, trial, image_spec, verified_image)

    second = run_experiment(
        root,
        plan,
        specs,
        images,
        trial_executor=execute,
        environment_collector=_environment,
    )

    assert first.completed_trials == 4
    assert rerun == [damaged_trial['trial_id']]
    assert second.reused_trials == 3
    attempts = root / 'trials' / damaged_trial['trial_id'] / 'attempts'
    assert (attempts / '0001').is_dir()
    assert (attempts / '0002').is_dir()


def test_failed_trial_keeps_status_and_log_without_completion_marker(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=1)
    root = tmp_path / 'experiment'

    def fail(*args):
        raise RuntimeError('benchmark process failed')

    with pytest.raises(ExperimentError, match='diagnostics kept'):
        run_experiment(
            root,
            plan,
            specs,
            images,
            trial_executor=fail,
            environment_collector=_environment,
        )

    trial = plan['schedule']['trials'][0]
    trial_root = root / 'trials' / trial['trial_id']
    status = json.loads((trial_root / 'status.json').read_text())
    failed = trial_root / 'attempts' / '0001-failed'
    assert status['outcome'] == 'failed'
    assert 'benchmark process failed' in (failed / 'trial.log').read_text()
    assert not (trial_root / 'complete.json').exists()
    assert not (root / 'experiment.complete.json').exists()


def test_resume_preserves_interrupted_staging_attempt_and_retries(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=1)
    root = tmp_path / 'experiment'
    write_json(plan, root / 'plan.json')
    trial = plan['schedule']['trials'][0]
    staging = root / 'trials' / trial['trial_id'] / 'attempts' / '.0001.staging'
    staging.mkdir(parents=True)
    (staging / 'partial.log').write_text('interrupted')

    run_experiment(
        root,
        plan,
        specs,
        images,
        trial_executor=_successful_trial,
        environment_collector=_environment,
    )

    attempts = staging.parent
    assert (attempts / '0001-incomplete' / 'partial.log').read_text() == 'interrupted'
    assert (attempts / '0002').is_dir()


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('ros_distro', 'jazzy'),
        ('suite', 'pubsub-rclcpp-minimal'),
        ('executor', 'DifferentExecutor'),
        ('duration', 99),
        ('cpuset_cpus', '4-5'),
    ),
)
def test_changed_immutable_configuration_prevents_resume(tmp_path, field, value):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=1)
    root = tmp_path / 'experiment'
    write_json(plan, root / 'plan.json')
    changed = deepcopy(plan)
    changed['configuration'][field] = value

    with pytest.raises(ExperimentError, match='immutable configuration'):
        run_experiment(root, changed, specs, images)


def test_changed_target_or_benchmark_commit_prevents_resume(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=1)
    root = tmp_path / 'experiment'
    write_json(plan, root / 'plan.json')
    changed = deepcopy(plan)
    changed['targets'][0]['identity']['benchmark_repository']['resolved_commit'] = 'f' * 40

    with pytest.raises(ExperimentError, match='immutable configuration'):
        run_experiment(root, changed, specs, images)


def test_environment_mismatch_stops_before_next_measured_trial(tmp_path):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=2)
    collected = 0
    executed = []

    def changing_environment(current_plan, trial, image_spec, verified_image):
        nonlocal collected
        collected += 1
        evidence = _environment(current_plan, trial, image_spec, verified_image)
        if collected == 2:
            evidence['host']['kernel'] = 'changed-kernel'
        return evidence

    def execute(stage, current_plan, trial, image_spec, verified_image):
        executed.append(trial['trial_id'])
        _successful_trial(stage, current_plan, trial, image_spec, verified_image)

    with pytest.raises(ExperimentError, match='kernel'):
        run_experiment(
            tmp_path / 'experiment',
            plan,
            specs,
            images,
            trial_executor=execute,
            environment_collector=changing_environment,
        )

    assert len(executed) == 1


def test_trial_publication_failure_never_exposes_completion_marker(
    tmp_path,
    monkeypatch,
):
    specs, images = _targets()
    plan = _plan(specs, images, warmups=0, repeats=1)
    root = tmp_path / 'experiment'
    original_write_json = experiment_module.write_json

    def fail_trial_completion(item, path):
        path = Path(path)
        if path.name == 'complete.json':
            raise OSError('simulated completion publication failure')
        return original_write_json(item, path)

    monkeypatch.setattr(experiment_module, 'write_json', fail_trial_completion)

    with pytest.raises(ExperimentError, match='diagnostics kept'):
        run_experiment(
            root,
            plan,
            specs,
            images,
            trial_executor=_successful_trial,
            environment_collector=_environment,
        )

    trial = plan['schedule']['trials'][0]
    trial_root = root / 'trials' / trial['trial_id']
    assert not (trial_root / 'complete.json').exists()
    assert (trial_root / 'attempts' / '0001-incomplete').is_dir()
    assert not (root / 'experiment.complete.json').exists()


def _plan(specs, images, warmups=1, repeats=2, seed=42):
    return build_experiment_plan(
        specs,
        images,
        suite='service-rclcpp-minimal',
        executor='EventsExecutor',
        duration=1,
        cpuset_cpus='0-1',
        warmup_count=warmups,
        measured_repeat_count=repeats,
        order='balanced',
        seed=seed,
        experiment_id='experiment-test',
        created_at='2026-08-18T00:00:00+00:00',
    )


def _targets():
    specs = {}
    images = {}
    for label, commit, image_character in (
        ('reference', 'b' * 40, 'd'),
        ('candidate', 'c' * 40, 'e'),
    ):
        target = ClientLibraryTarget(
            name='rclcpp',
            source='build',
            repository_url='https://github.com/ros2/rclcpp.git',
            requested_ref=commit,
            resolved_commit=commit,
            checkout_path=Path(f'/cache/{commit}'),
        )
        spec = BenchmarkImageSpec(
            ros_distro='rolling',
            architecture='amd64',
            benchmark_repository_url='https://github.com/ros2/ros2-benchmark-container',
            benchmark_requested_ref='rolling',
            benchmark_resolved_commit='a' * 40,
            client_target=target,
        )
        specs[label] = spec
        images[label] = VerifiedImage(
            image_name=spec.image_name,
            image_id=f'sha256:{image_character * 64}',
            image_digest=f'sha256:{image_character * 64}',
            target_key=spec.target_key,
        )
    return specs, images


def _successful_trial(stage, plan, trial, image_spec, verified_image):
    write_json({'run_id': trial['trial_id']}, stage / 'metadata.json')
    raw = stage / 'benchmark' / plan['configuration']['ros_distro'] / 'result.txt'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('raw benchmark output\n')
    record = {
        'schema_version': 5,
        'run_id': trial['trial_id'],
        'timestamp': '2026-08-18T00:00:00Z',
        'benchmark_ref': image_spec.benchmark_requested_ref,
        'benchmark_commit': image_spec.benchmark_resolved_commit,
        'client_library_ref': image_spec.client_target.requested_ref,
        'client_library_commit': image_spec.client_target.resolved_commit,
        'client_library': 'rclcpp',
        'client_library_source': image_spec.client_target.source,
        'platform': 'x86_64',
        'ros_distro': image_spec.ros_distro,
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': plan['configuration']['executor'],
        'topology': 'service',
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': 10,
        'frequency': 0.0,
        'metric_name': 'service_client_latency',
        'numeric_value': float(trial['planned_order']),
        'unit': 'us',
        'aggregation': 'mean',
        'source_file': 'latency_all.txt',
        'node_role': '',
        'run_kind': 'measured',
        'aggregation_method': 'none',
        'repeat_count': 1,
    }
    (stage / 'normalized_metrics.jsonl').write_text(json.dumps(record) + '\n')


def _environment(plan, trial, image_spec, verified_image):
    return {
        'captured_at': '2026-08-18T00:00:00+00:00',
        'host': {
            'architecture': 'x86_64',
            'cpu_model': 'Test CPU',
            'kernel': 'test-kernel',
            'docker_version': 'test-docker',
            'cpuset_cpus': plan['configuration']['cpuset_cpus'],
            'cpu_governors': {'0': 'performance', '1': 'performance'},
        },
        'configuration': dict(plan['configuration']),
        'trial': {'trial_id': trial['trial_id']},
        'target': {
            'target_key': image_spec.target_key,
            'benchmark_commit': image_spec.benchmark_resolved_commit,
            'image_id': verified_image.image_id,
        },
    }


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]
