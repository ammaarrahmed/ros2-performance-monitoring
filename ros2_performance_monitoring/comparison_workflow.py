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

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import traceback

from .benchmark_image import benchmark_image_exists
from .benchmark_image import BenchmarkImageSpec
from .benchmark_image import build_benchmark_image
from .benchmark_image import VerifiedImage
from .benchmark_image import verify_benchmark_image
from .client_target import DEFAULT_RCLCPP_REPOSITORY
from .client_target import resolve_rclcpp_target
from .client_target import resolve_remote_rclcpp_target
from .comparison import CATEGORIES
from .comparison_report import validate_comparison_report
from .container_provider import get_default_container_repo
from .container_provider import resolve_container_repo_ref
from .container_provider import setup_container_repo
from .dataset import manifest_path_for
from .dataset import verify_dataset_bundle
from .experiment import build_experiment_plan
from .experiment import load_experiment_evidence
from .experiment import prepare_experiment
from .experiment import run_experiment
from .preflight import run_comparison_preflight
from .statistical_comparison import build_comparison_report
from .statistical_comparison import CANNOT_COMPARE
from .statistical_comparison import comparison_exit_code
from .statistical_comparison import DEFAULT_BOOTSTRAP_REPEATS
from .statistical_comparison import DEFAULT_CONFIDENCE_LEVEL
from .statistical_comparison import DEFAULT_SEED
from .statistical_comparison import EXIT_INVALID_COMPARISON
from .statistical_comparison import INCOMPLETE_RESULTS
from .statistical_comparison import MINIMUM_MEASURED_TRIALS
from .statistical_comparison import NOT_APPLICABLE
from .statistical_comparison import REPORT_SCHEMA_VERSION
from .writers.jsonl import write_json


WORKFLOW_STATUS_FILENAME = 'workflow.status.json'
WORKFLOW_LOG_FILENAME = 'workflow.log'
WORKFLOW_COMPLETE_FILENAME = 'comparison.complete.json'
REPORT_FILENAME = 'comparison-report.json'
OPERATIONAL_ERROR_EXIT = 4
TARGET_LABELS = ('reference', 'candidate')
INVALID_COMPARISON_STATUSES = frozenset({
    CANNOT_COMPARE,
    INCOMPLETE_RESULTS,
    NOT_APPLICABLE,
})


class ComparisonWorkflowError(RuntimeError):
    """Report an operational failure distinct from comparison evidence."""


@dataclass(frozen=True)
class ComparisonWorkflowOptions:
    """Describe one end-to-end local comparison invocation."""

    results_dir: str
    reference_ref: str
    candidate_ref: str
    ros_distro: str
    suite: str
    executor: str
    duration: int
    cpuset_cpus: str | None
    warmups: int
    repeats: int
    order: str
    schedule_seed: int
    cache_dir: str
    rclcpp_repository_url: str = DEFAULT_RCLCPP_REPOSITORY
    container_repository_url: str | None = None
    container_ref: str | None = None
    skip_build: bool = False
    dry_run: bool = False
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    bootstrap_repeats: int = DEFAULT_BOOTSTRAP_REPEATS
    bootstrap_seed: int = DEFAULT_SEED
    minimum_trials: int = MINIMUM_MEASURED_TRIALS
    start_dashboard: bool = False


@dataclass(frozen=True)
class ComparisonWorkflowResult:
    """Summarize planned or completed end-to-end comparison outputs."""

    dry_run: bool
    experiment_dir: Path
    plan: dict
    reference_commit: str
    candidate_commit: str
    reference_image_key: str
    candidate_image_key: str
    completed_trials: int
    failed_trials: int
    reused_trials: int
    dataset_path: Path
    report_path: Path
    overall_status: str | None
    exit_code: int | None
    dashboard_command: tuple[str, ...]


def run_comparison_workflow(options: ComparisonWorkflowOptions):
    """Resolve, prepare, execute, analyse, and validate one comparison."""
    root = Path(options.results_dir).expanduser().resolve()
    stage = 'preflight'
    try:
        preflight = run_comparison_preflight(
            root,
            options.cpuset_cpus,
            dashboard_requested=options.start_dashboard,
        )
    except Exception as exc:
        raise ComparisonWorkflowError(f'preflight failed: {exc}') from exc
    if options.dry_run:
        try:
            return _plan_dry_run(options, root, preflight.architecture)
        except Exception as exc:
            if isinstance(exc, ComparisonWorkflowError):
                raise
            raise ComparisonWorkflowError(f'dry-run planning failed: {exc}') from exc

    _validate_initial_directory(root)
    root.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    _record_stage(root, stage, started_at)
    try:
        stage = 'target-resolution'
        _record_stage(root, stage, started_at)
        client_targets = {
            'reference': resolve_rclcpp_target(
                options.rclcpp_repository_url,
                options.reference_ref,
                options.cache_dir,
            ),
            'candidate': resolve_rclcpp_target(
                options.rclcpp_repository_url,
                options.candidate_ref,
                options.cache_dir,
            ),
        }
        container_url, container_ref = _container_inputs(options)
        benchmark_commit = setup_container_repo(
            container_repo_url=container_url,
            container_ref=container_ref,
            cache_dir=options.cache_dir,
        )
        image_specs = _image_specs(
            options,
            preflight.architecture,
            client_targets,
            container_url,
            container_ref,
            benchmark_commit,
        )

        stage = 'target-preparation'
        _record_stage(root, stage, started_at)
        verified_images = {
            label: _prepare_image(label, image_specs[label], options)
            for label in TARGET_LABELS
        }
        requested_plan = _build_plan(options, image_specs, verified_images)
        plan = prepare_experiment(
            root,
            requested_plan,
            image_specs,
            verified_images,
        )
        _write_target_manifests(root, plan, image_specs)
        _print_plan(root, plan, image_specs, dry_run=False)

        stage = 'experiment-execution'
        _record_stage(root, stage, started_at)
        experiment_result = run_experiment(
            root,
            plan,
            image_specs,
            verified_images,
        )

        stage = 'comparison-report'
        _record_stage(root, stage, started_at)
        completed = load_experiment_evidence(root)
        if not completed.experiment_complete or completed.dataset_path is None:
            raise ComparisonWorkflowError('experiment did not publish verified completion')
        report_path = root / REPORT_FILENAME
        dataset_path = completed.dataset_path
        dataset_manifest = verify_dataset_bundle(dataset_path)
        records = _load_dataset_records(dataset_path)
        report = _load_reusable_report(
            report_path,
            records,
            dataset_manifest['dataset_sha256'],
            options,
            completed.plan,
        )
        if report is None:
            trial_records = {
                trial.trial_id: trial.records
                for trial in completed.measured_trials
            }
            report = build_comparison_report(
                completed.plan,
                trial_records,
                reference='reference',
                candidate='candidate',
                confidence_level=options.confidence_level,
                bootstrap_repeats=options.bootstrap_repeats,
                seed=options.bootstrap_seed,
                minimum_trials=options.minimum_trials,
                dataset_sha256=completed.dataset_sha256,
            )
            write_json(report, report_path)

        stage = 'final-validation'
        _record_stage(root, stage, started_at)
        _validate_outputs(
            root,
            plan,
            image_specs,
            completed,
            dataset_path,
            dataset_manifest,
            records,
            report,
        )
        completion = _completion_manifest(
            root,
            plan,
            dataset_path,
            dataset_manifest,
            report_path,
            report,
        )
        _record_stage(root, 'complete', started_at, outcome='completed')
        _write_if_changed(root / WORKFLOW_COMPLETE_FILENAME, completion)
    except Exception as exc:
        _record_failure(root, stage, started_at, exc)
        if isinstance(exc, ComparisonWorkflowError):
            raise
        raise ComparisonWorkflowError(f'{stage} failed: {exc}') from exc

    dashboard_command = _dashboard_command(dataset_path, report_path)
    result = ComparisonWorkflowResult(
        dry_run=False,
        experiment_dir=root,
        plan=plan,
        reference_commit=image_specs['reference'].client_target.resolved_commit,
        candidate_commit=image_specs['candidate'].client_target.resolved_commit,
        reference_image_key=image_specs['reference'].target_key,
        candidate_image_key=image_specs['candidate'].target_key,
        completed_trials=experiment_result.completed_trials,
        failed_trials=0,
        reused_trials=experiment_result.reused_trials,
        dataset_path=dataset_path,
        report_path=report_path,
        overall_status=report['overall']['status'],
        exit_code=comparison_exit_code(report),
        dashboard_command=dashboard_command,
    )
    _print_summary(result)
    return result


def _plan_dry_run(options, root, architecture):
    client_targets = {
        'reference': resolve_remote_rclcpp_target(
            options.rclcpp_repository_url,
            options.reference_ref,
        ),
        'candidate': resolve_remote_rclcpp_target(
            options.rclcpp_repository_url,
            options.candidate_ref,
        ),
    }
    container_url, container_ref = _container_inputs(options)
    benchmark_commit = resolve_container_repo_ref(container_url, container_ref)
    image_specs = _image_specs(
        options,
        architecture,
        client_targets,
        container_url,
        container_ref,
        benchmark_commit,
    )
    planned_images = {
        label: VerifiedImage(
            image_name=image_specs[label].image_name,
            image_id='not-built-dry-run',
            image_digest='not-built-dry-run',
            target_key=image_specs[label].target_key,
        )
        for label in TARGET_LABELS
    }
    plan = _build_plan(options, image_specs, planned_images)
    _print_plan(root, plan, image_specs, dry_run=True)
    dataset_path = root / 'dataset' / 'dashboard-data.jsonl'
    report_path = root / REPORT_FILENAME
    result = ComparisonWorkflowResult(
        dry_run=True,
        experiment_dir=root,
        plan=plan,
        reference_commit=client_targets['reference'].resolved_commit,
        candidate_commit=client_targets['candidate'].resolved_commit,
        reference_image_key=image_specs['reference'].target_key,
        candidate_image_key=image_specs['candidate'].target_key,
        completed_trials=0,
        failed_trials=0,
        reused_trials=0,
        dataset_path=dataset_path,
        report_path=report_path,
        overall_status=None,
        exit_code=None,
        dashboard_command=_dashboard_command(dataset_path, report_path),
    )
    print('Dry run complete; no repositories, images, containers, or artifacts were created.')
    return result


def _container_inputs(options):
    default_url, default_ref = get_default_container_repo()
    return (
        options.container_repository_url or default_url,
        options.container_ref or default_ref,
    )


def _image_specs(
    options,
    architecture,
    client_targets,
    container_url,
    container_ref,
    benchmark_commit,
):
    specs = {
        label: BenchmarkImageSpec(
            ros_distro=options.ros_distro,
            architecture=architecture,
            benchmark_repository_url=container_url,
            benchmark_requested_ref=container_ref,
            benchmark_resolved_commit=benchmark_commit,
            client_target=client_targets[label],
        )
        for label in TARGET_LABELS
    }
    if specs['reference'].target_key == specs['candidate'].target_key:
        raise ComparisonWorkflowError(
            'reference and candidate refs resolve to the same target; '
            'choose two different rclcpp commits'
        )
    return specs


def _prepare_image(label, spec, options):
    if benchmark_image_exists(spec):
        image = verify_benchmark_image(spec)
        print(f'Using verified {label} image: {spec.image_name}')
        return image
    if options.skip_build:
        raise ComparisonWorkflowError(
            f'Cannot skip build: exact {label} target image {spec.image_name} does not exist.'
        )
    image = build_benchmark_image(spec, options.cache_dir)
    print(f'Built verified {label} image: {spec.image_name}')
    return image


def _build_plan(options, image_specs, verified_images):
    return build_experiment_plan(
        image_specs,
        verified_images,
        suite=options.suite,
        executor=options.executor,
        duration=options.duration,
        cpuset_cpus=options.cpuset_cpus,
        warmup_count=options.warmups,
        measured_repeat_count=options.repeats,
        order=options.order,
        seed=options.schedule_seed,
    )


def _validate_initial_directory(root):
    if not root.exists():
        return
    allowed = {'plan.json', WORKFLOW_STATUS_FILENAME, WORKFLOW_LOG_FILENAME}
    if not (root / 'plan.json').exists():
        unexpected = sorted(path.name for path in root.iterdir() if path.name not in allowed)
        if unexpected:
            raise ComparisonWorkflowError(
                f'cannot create an experiment in non-empty directory without plan.json: '
                f'{unexpected[0]}'
            )


def _write_target_manifests(root, plan, image_specs):
    planned_targets = {target['label']: target for target in plan['targets']}
    target_root = root / 'targets'
    for label in TARGET_LABELS:
        planned = planned_targets[label]
        manifest = {
            'schema_version': 1,
            'experiment_id': plan['experiment_id'],
            'label': label,
            'target_key': planned['target_key'],
            'image_manifest': image_specs[label].manifest(),
            'verified_image': planned['verified_image'],
        }
        _write_if_changed(target_root / f'{label}.json', manifest)


def _load_reusable_report(path, records, checksum, options, plan):
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        _validate_report_identity(report, plan, checksum)
        if comparison_exit_code(report) != EXIT_INVALID_COMPARISON:
            validate_comparison_report(report, records, checksum)
        else:
            _validate_invalid_report_structure(report)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    analysis = report.get('analysis', {})
    expected = {
        'confidence_level': options.confidence_level,
        'bootstrap_repeats': options.bootstrap_repeats,
        'seed': options.bootstrap_seed,
        'minimum_measured_trials': options.minimum_trials,
    }
    if any(analysis.get(key) != value for key, value in expected.items()):
        return None
    return report


def _validate_outputs(
    root,
    plan,
    image_specs,
    completed,
    dataset_path,
    dataset_manifest,
    records,
    report,
):
    if completed.plan != plan:
        raise ComparisonWorkflowError('completed experiment plan does not match requested plan')
    if completed.dataset_path != dataset_path:
        raise ComparisonWorkflowError('completed experiment dataset path is inconsistent')
    if completed.dataset_sha256 != dataset_manifest.get('dataset_sha256'):
        raise ComparisonWorkflowError('experiment and dataset checksums do not agree')
    _validate_report_identity(report, plan, completed.dataset_sha256)

    planned_targets = {target['label']: target for target in plan['targets']}
    for label in TARGET_LABELS:
        target_path = root / 'targets' / f'{label}.json'
        try:
            target_manifest = json.loads(target_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise ComparisonWorkflowError(
                f'verified {label} target manifest is missing or invalid'
            ) from exc
        planned = planned_targets[label]
        if (
            target_manifest.get('target_key') != planned['target_key']
            or target_manifest.get('image_manifest') != image_specs[label].manifest()
            or target_manifest.get('verified_image') != planned['verified_image']
        ):
            raise ComparisonWorkflowError(
                f'verified {label} target manifest does not agree with the experiment plan'
            )
    if comparison_exit_code(report) != EXIT_INVALID_COMPARISON:
        validate_comparison_report(report, records, dataset_manifest['dataset_sha256'])
    else:
        _validate_invalid_report_structure(report)


def _validate_report_identity(report, plan, dataset_sha256):
    if report.get('experiment_id') != plan.get('experiment_id'):
        raise ComparisonWorkflowError('comparison report experiment identity does not agree')
    if report.get('dataset', {}).get('sha256') != dataset_sha256:
        raise ComparisonWorkflowError('comparison report dataset checksum does not agree')
    planned_targets = {target['label']: target for target in plan['targets']}
    reported_targets = report.get('targets', {})
    for label in TARGET_LABELS:
        if reported_targets.get(label, {}).get('target_key') != (
            planned_targets[label]['target_key']
        ):
            raise ComparisonWorkflowError(
                f'comparison report {label} target does not agree with the experiment plan'
            )


def _validate_invalid_report_structure(report):
    required = {
        'schema_version',
        'experiment_id',
        'dataset',
        'targets',
        'analysis',
        'overall',
        'categories',
        'scenarios',
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ComparisonWorkflowError('invalid comparison report structure')
    if report.get('schema_version') != REPORT_SCHEMA_VERSION:
        raise ComparisonWorkflowError('invalid comparison report schema version')
    dataset = report.get('dataset')
    if not isinstance(dataset, dict) or set(dataset) != {'sha256', 'experiment_id'}:
        raise ComparisonWorkflowError('invalid comparison report dataset binding')
    if dataset.get('experiment_id') != report.get('experiment_id'):
        raise ComparisonWorkflowError('invalid comparison report experiment binding')
    targets = report.get('targets')
    if not isinstance(targets, dict) or set(targets) != set(TARGET_LABELS):
        raise ComparisonWorkflowError('invalid comparison report targets')
    for label in TARGET_LABELS:
        target = targets[label]
        if (
            not isinstance(target, dict)
            or set(target) != {'label', 'target_key', 'identity'}
            or target.get('label') != label
        ):
            raise ComparisonWorkflowError('invalid comparison report targets')
    if not isinstance(report.get('analysis'), dict):
        raise ComparisonWorkflowError('invalid comparison report analysis settings')
    overall = report.get('overall')
    if (
        not isinstance(overall, dict)
        or overall.get('status') not in INVALID_COMPARISON_STATUSES
    ):
        raise ComparisonWorkflowError('invalid comparison report evidence status')
    categories = report.get('categories')
    if not isinstance(categories, dict) or set(categories) != set(CATEGORIES):
        raise ComparisonWorkflowError('invalid comparison report category coverage')
    if any(
        not isinstance(evidence, dict)
        or evidence.get('status') not in INVALID_COMPARISON_STATUSES
        for evidence in categories.values()
    ):
        raise ComparisonWorkflowError('invalid comparison report category evidence')
    if not isinstance(report.get('scenarios'), list):
        raise ComparisonWorkflowError('invalid comparison report scenario coverage')


def _completion_manifest(
    root,
    plan,
    dataset_path,
    dataset_manifest,
    report_path,
    report,
):
    completion = {
        'schema_version': 1,
        'experiment_id': plan['experiment_id'],
        'completed_at': _utc_now(),
        'plan_sha256': _file_sha256(root / 'plan.json'),
        'target_manifest_sha256': {
            label: _file_sha256(root / 'targets' / f'{label}.json')
            for label in TARGET_LABELS
        },
        'experiment_completion_sha256': _file_sha256(root / 'experiment.complete.json'),
        'dataset': str(dataset_path.relative_to(root)),
        'dataset_sha256': dataset_manifest['dataset_sha256'],
        'dataset_manifest_sha256': _file_sha256(manifest_path_for(dataset_path)),
        'report': str(report_path.relative_to(root)),
        'report_sha256': _file_sha256(report_path),
        'overall_status': report['overall']['status'],
        'comparison_exit_code': comparison_exit_code(report),
    }
    try:
        existing = json.loads(
            (root / WORKFLOW_COMPLETE_FILENAME).read_text(encoding='utf-8')
        )
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return completion
    stable_fields = set(completion) - {'completed_at'}
    if all(existing.get(field) == completion[field] for field in stable_fields):
        completion['completed_at'] = existing.get('completed_at', completion['completed_at'])
    return completion


def _write_if_changed(path, value):
    path = Path(path)
    try:
        existing = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        existing = None
    if existing != value:
        write_json(value, path)


def _load_dataset_records(path):
    records = []
    try:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ComparisonWorkflowError(
                        'validated dataset contains a non-object JSON record'
                    )
                records.append(item)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ComparisonWorkflowError(f'cannot load validated dataset: {path}') from exc
    if not records:
        raise ComparisonWorkflowError(f'validated dataset contains no records: {path}')
    return records


def _record_stage(root, stage, started_at, outcome='running'):
    message = f'{_utc_now()} {stage}: {outcome}'
    _append_log(root, message)
    write_json({
        'schema_version': 1,
        'started_at': started_at,
        'updated_at': _utc_now(),
        'stage': stage,
        'outcome': outcome,
    }, root / WORKFLOW_STATUS_FILENAME)


def _record_failure(root, stage, started_at, error):
    error_text = f'{type(error).__name__}: {error}'
    _append_log(root, f'{_utc_now()} {stage}: failed: {error_text}')
    with (root / WORKFLOW_LOG_FILENAME).open('a', encoding='utf-8') as log:
        log.write(traceback.format_exc())
    try:
        write_json({
            'schema_version': 1,
            'started_at': started_at,
            'updated_at': _utc_now(),
            'stage': stage,
            'outcome': 'failed',
            'error': error_text,
        }, root / WORKFLOW_STATUS_FILENAME)
    except OSError:
        pass


def _append_log(root, message):
    with (root / WORKFLOW_LOG_FILENAME).open('a', encoding='utf-8') as log:
        log.write(f'{message}\n')


def _print_plan(root, plan, image_specs, dry_run):
    mode = 'Dry-run comparison plan' if dry_run else 'Resolved comparison plan'
    print(f'{mode}:')
    for label in TARGET_LABELS:
        spec = image_specs[label]
        print(f'  {label} commit: {spec.client_target.resolved_commit}')
        print(f'  {label} image key: {spec.target_key}')
    configuration = plan['configuration']
    schedule = plan['schedule']
    print(
        '  configuration: '
        f'ROS {configuration["ros_distro"]}, suite {configuration["suite"]}, '
        f'executor {configuration["executor"]}, duration {configuration["duration"]}s, '
        f'CPU set {configuration["cpuset_cpus"] or "unrestricted"}'
    )
    print(
        f'  trials: {schedule["warmup_count"]} warm-up and '
        f'{schedule["measured_repeat_count"]} measured per target '
        f'({schedule["order"]}, seed {schedule["seed"]})'
    )
    print('  trial order:')
    for trial in schedule['trials']:
        print(
            f'    {trial["planned_order"]}: {trial["target"]} '
            f'{trial["kind"]} {trial["sequence"]}'
        )
    print(f'  experiment plan: {root / "plan.json"}')
    print(f'  dataset: {root / "dataset" / "dashboard-data.jsonl"}')
    print(f'  report: {root / REPORT_FILENAME}')


def _print_summary(result):
    print('Comparison workflow complete:')
    print(f'  reference commit: {result.reference_commit}')
    print(f'  candidate commit: {result.candidate_commit}')
    print(
        f'  trials: {result.completed_trials} completed, '
        f'{result.failed_trials} failed, {result.reused_trials} reused'
    )
    print(f'  dataset: {result.dataset_path}')
    print(f'  report: {result.report_path}')
    print(f'  overall evidence: {result.overall_status}')
    print(f'  dashboard: {shlex.join(result.dashboard_command)}')


def _dashboard_command(dataset_path, report_path):
    return (
        'ros2-performance-monitoring',
        'dashboard',
        'up',
        '--input',
        str(dataset_path),
        '--comparison-report',
        str(report_path),
    )


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _utc_now():
    return datetime.now(timezone.utc).isoformat()
