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
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from ros2_performance_monitoring.benchmark_image import VerifiedImage
from ros2_performance_monitoring.client_target import ClientLibraryTarget
import ros2_performance_monitoring.comparison_workflow as workflow
from ros2_performance_monitoring.config import RunDefaults
from ros2_performance_monitoring.experiment import prepare_experiment
from ros2_performance_monitoring.source_dependencies import SourceDependency
from ros2_performance_monitoring.source_dependencies import SourceDependencySnapshot
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE


RCLCPP_REPOSITORY = 'https://github.com/ros2/rclcpp.git'
CONTAINER_REPOSITORY = 'https://github.com/ros2/ros2-benchmark-container'
BENCHMARK_COMMIT = 'a' * 40
REFERENCE_COMMIT = 'b' * 40
CANDIDATE_COMMIT = 'c' * 40
DATASET_SHA = 'd' * 64


def test_workflow_rejects_interleaved_schedule_before_preflight(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / 'comparison'
    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: pytest.fail('preflight must not start'),
    )

    with pytest.raises(
        workflow.ComparisonWorkflowError,
        match='requires balanced scheduling',
    ):
        workflow.run_comparison_workflow(_options(root, order='interleaved'))

    assert not root.exists()


def test_normal_comparison_still_rejects_identical_resolved_targets(tmp_path):
    options = _options(tmp_path)
    target = _target(REFERENCE_COMMIT)

    with pytest.raises(
        workflow.ComparisonWorkflowError,
        match='resolve to the same target',
    ):
        workflow._image_specs(
            options,
            'amd64',
            {'reference': target, 'candidate': target},
            CONTAINER_REPOSITORY,
            'rolling',
            BENCHMARK_COMMIT,
        )


def test_mocked_end_to_end_workflow_composes_stages_and_reuses_completed_work(
    tmp_path,
    monkeypatch,
    capsys,
):
    root = tmp_path / 'comparison'
    calls = []
    options = replace(
        _options(root),
        source_dependencies_file='/workspace/rolling-dependencies.repos',
    )
    source_dependencies = _source_dependencies()

    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: _call(
            calls,
            'preflight',
            argparse.Namespace(
                architecture='amd64',
                result_filesystem_free_bytes=20 * 1024 ** 3,
                docker_filesystem_free_bytes=20 * 1024 ** 3,
            ),
        ),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_rclcpp_target',
        lambda repository_url, requested_ref, cache_dir: _call(
            calls,
            f'resolve:{requested_ref}',
            _target(requested_ref),
        ),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_source_dependency_snapshot',
        lambda manifest_path, cache_dir: _call(
            calls,
            f'dependencies:{manifest_path}',
            source_dependencies,
        ),
    )
    monkeypatch.setattr(
        workflow,
        'get_default_container_repo',
        lambda: (CONTAINER_REPOSITORY, 'rolling'),
    )
    monkeypatch.setattr(
        workflow,
        'setup_container_repo',
        lambda **kwargs: _call(calls, 'benchmark-repository', BENCHMARK_COMMIT),
    )
    monkeypatch.setattr(workflow, 'benchmark_image_exists', lambda spec: True)
    monkeypatch.setattr(
        workflow,
        'verify_benchmark_image',
        lambda spec: _call(calls, f'verify:{spec.client_target.requested_ref}', _image(spec)),
    )
    monkeypatch.setattr(
        workflow,
        'build_benchmark_image',
        lambda *args: pytest.fail('verified images must not be rebuilt'),
    )

    def fake_prepare(experiment_dir, plan, image_specs, images):
        calls.append('prepare-plan')
        return prepare_experiment(experiment_dir, plan, image_specs, images)

    monkeypatch.setattr(workflow, 'prepare_experiment', fake_prepare)

    def fake_run(experiment_dir, plan, image_specs, images):
        calls.append('run-experiment')
        dataset_path = root / 'dataset' / 'dashboard-data.jsonl'
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path.write_text('{"run_id":"aggregate-reference"}\n')
        (root / 'dataset' / 'dashboard-data.manifest.json').write_text('{}\n')
        (root / 'experiment.complete.json').write_text('{}\n')
        return argparse.Namespace(
            experiment_id=plan['experiment_id'],
            experiment_dir=root,
            dataset_path=dataset_path,
            completed_trials=len(plan['schedule']['trials']),
            reused_trials=len(plan['schedule']['trials']),
        )

    monkeypatch.setattr(workflow, 'run_experiment', fake_run)

    def fake_load(experiment_dir):
        calls.append('load-evidence')
        plan = json.loads((root / 'plan.json').read_text())
        dataset_path = root / 'dataset' / 'dashboard-data.jsonl'
        trials = tuple(
            argparse.Namespace(
                trial_id=trial['trial_id'],
                records=({'run_id': trial['trial_id']},),
            )
            for trial in plan['schedule']['trials']
            if trial['kind'] == 'measured'
        )
        return argparse.Namespace(
            plan=plan,
            measured_trials=trials,
            experiment_complete=True,
            dataset_path=dataset_path,
            dataset_sha256=DATASET_SHA,
        )

    monkeypatch.setattr(workflow, 'load_experiment_evidence', fake_load)
    monkeypatch.setattr(
        workflow,
        'verify_dataset_bundle',
        lambda path: _call(calls, 'verify-dataset', {'dataset_sha256': DATASET_SHA}),
    )

    def fake_report(plan, trial_records, **kwargs):
        calls.append('build-report')
        targets = {target['label']: target for target in plan['targets']}
        return {
            'schema_version': 2,
            'experiment_id': plan['experiment_id'],
            'dataset': {'sha256': DATASET_SHA, 'experiment_id': plan['experiment_id']},
            'targets': {
                label: {
                    'label': label,
                    'target_key': targets[label]['target_key'],
                    'identity': targets[label]['identity'],
                }
                for label in ('reference', 'candidate')
            },
            'analysis': {
                'confidence_level': kwargs['confidence_level'],
                'bootstrap_repeats': kwargs['bootstrap_repeats'],
                'seed': kwargs['seed'],
                'minimum_measured_trials': kwargs['minimum_trials'],
            },
            'overall': {'status': 'No regression'},
        }

    monkeypatch.setattr(workflow, 'build_comparison_report', fake_report)
    monkeypatch.setattr(
        workflow,
        'validate_comparison_report',
        lambda report, records, checksum: _call(calls, 'validate-report', None),
    )

    result = workflow.run_comparison_workflow(options)

    assert result.exit_code == 0
    assert result.completed_trials == 6
    assert result.reused_trials == 6
    assert result.reference_commit == REFERENCE_COMMIT
    assert result.candidate_commit == CANDIDATE_COMMIT
    assert calls == [
        'preflight',
        f'resolve:{REFERENCE_COMMIT}',
        f'resolve:{CANDIDATE_COMMIT}',
        'dependencies:/workspace/rolling-dependencies.repos',
        'benchmark-repository',
        f'verify:{REFERENCE_COMMIT}',
        f'verify:{CANDIDATE_COMMIT}',
        'prepare-plan',
        'run-experiment',
        'load-evidence',
        'verify-dataset',
        'build-report',
        'validate-report',
    ]
    assert (root / 'targets' / 'reference.json').is_file()
    assert (root / 'targets' / 'candidate.json').is_file()
    completion = json.loads((root / workflow.WORKFLOW_COMPLETE_FILENAME).read_text())
    assert completion['dataset_sha256'] == DATASET_SHA
    assert completion['overall_status'] == 'No regression'
    assert json.loads((root / workflow.WORKFLOW_STATUS_FILENAME).read_text())[
        'outcome'
    ] == 'completed'
    output = capsys.readouterr().out
    assert 'Resolved comparison plan:' in output
    assert 'Comparison workflow complete:' in output
    assert '6 completed, 0 failed, 6 reused' in output
    assert 'ros2-performance-monitoring dashboard up' in output

    report_path = root / workflow.REPORT_FILENAME
    completion_path = root / workflow.WORKFLOW_COMPLETE_FILENAME
    original_report = report_path.read_text()
    original_completion = completion_path.read_text()
    calls.clear()

    resumed = workflow.run_comparison_workflow(options)

    assert resumed.reused_trials == 6
    assert 'build-report' not in calls
    assert calls.count('validate-report') == 2
    assert report_path.read_text() == original_report
    assert completion_path.read_text() == original_completion

    legacy_completion = json.loads(completion_path.read_text())
    legacy_completion['schema_version'] = 1
    workflow.write_json(legacy_completion, completion_path)
    calls.clear()

    migrated = workflow.run_comparison_workflow(options)

    assert migrated.reused_trials == 6
    assert calls.count('build-report') == 1
    assert json.loads(completion_path.read_text())['schema_version'] == 2

    report_path.write_text(original_report + '\n')
    calls.clear()

    recovered = workflow.run_comparison_workflow(options)

    assert recovered.reused_trials == 6
    assert calls.count('build-report') == 1
    assert report_path.read_text() == original_report
    assert json.loads(completion_path.read_text())['schema_version'] == 2

    report_path.write_text(original_report + '\n')
    original_write_json = workflow.write_json

    def fail_final_completion(item, path):
        if Path(path).name == workflow.WORKFLOW_COMPLETE_FILENAME:
            raise OSError('simulated final completion failure')
        return original_write_json(item, path)

    monkeypatch.setattr(workflow, 'write_json', fail_final_completion)

    with pytest.raises(
        workflow.ComparisonWorkflowError,
        match='simulated final completion failure',
    ):
        workflow.run_comparison_workflow(options)

    assert not completion_path.exists()


def test_dry_run_resolves_remote_refs_and_writes_nothing(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'comparison'
    calls = []
    options = _options(root, dry_run=True)
    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: _call(
            calls,
            'preflight',
            argparse.Namespace(
                architecture='amd64',
                result_filesystem_free_bytes=5 * 1024 ** 3,
                docker_filesystem_free_bytes=5 * 1024 ** 3,
            ),
        ),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_remote_rclcpp_target',
        lambda repository_url, requested_ref: _call(
            calls,
            f'remote:{requested_ref}',
            _target(requested_ref),
        ),
    )
    monkeypatch.setattr(
        workflow,
        'get_default_container_repo',
        lambda: (CONTAINER_REPOSITORY, 'rolling'),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_container_repo_ref',
        lambda url, ref: _call(calls, 'remote-benchmark', BENCHMARK_COMMIT),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_rclcpp_target',
        lambda *args: pytest.fail('dry run must not create target checkouts'),
    )
    monkeypatch.setattr(
        workflow,
        'setup_container_repo',
        lambda **kwargs: pytest.fail('dry run must not create a benchmark checkout'),
    )
    monkeypatch.setattr(
        workflow,
        'build_benchmark_image',
        lambda *args: pytest.fail('dry run must not build images'),
    )
    monkeypatch.setattr(
        workflow,
        'run_experiment',
        lambda *args: pytest.fail('dry run must not execute trials'),
    )

    result = workflow.run_comparison_workflow(options)

    assert result.dry_run is True
    assert result.exit_code is None
    assert calls == [
        'preflight',
        f'remote:{REFERENCE_COMMIT}',
        f'remote:{CANDIDATE_COMMIT}',
        'remote-benchmark',
    ]
    assert not root.exists()
    output = capsys.readouterr().out
    assert 'real build would fail the 10 GiB free-space' in output
    assert 'Dry-run comparison plan:' in output
    assert 'no repositories, images, containers, or artifacts were created' in output


def test_operational_failure_preserves_status_and_log(tmp_path, monkeypatch):
    root = tmp_path / 'comparison'
    options = _options(root)
    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: argparse.Namespace(architecture='amd64'),
    )

    def fail_resolve(*args):
        raise RuntimeError('requested ref was not found')

    monkeypatch.setattr(workflow, 'resolve_rclcpp_target', fail_resolve)

    with pytest.raises(workflow.ComparisonWorkflowError, match='target-resolution failed'):
        workflow.run_comparison_workflow(options)

    status = json.loads((root / workflow.WORKFLOW_STATUS_FILENAME).read_text())
    assert status['stage'] == 'target-resolution'
    assert status['outcome'] == 'failed'
    assert 'requested ref was not found' in status['error']
    assert 'requested ref was not found' in (root / workflow.WORKFLOW_LOG_FILENAME).read_text()
    assert not (root / 'plan.json').exists()


def test_workflow_refuses_unplanned_nonempty_result_directory(tmp_path, monkeypatch):
    root = tmp_path / 'comparison'
    root.mkdir()
    (root / 'unrelated.txt').write_text('keep me')
    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: argparse.Namespace(architecture='amd64'),
    )

    with pytest.raises(workflow.ComparisonWorkflowError, match='unrelated.txt'):
        workflow.run_comparison_workflow(_options(root))

    assert (root / 'unrelated.txt').read_text() == 'keep me'
    assert not (root / workflow.WORKFLOW_STATUS_FILENAME).exists()


def test_completion_manifest_uses_checksums_of_every_final_artifact(tmp_path):
    root = tmp_path
    (root / 'targets').mkdir()
    (root / 'dataset').mkdir()
    files = {
        'plan.json': b'plan',
        'targets/reference.json': b'reference',
        'targets/candidate.json': b'candidate',
        'experiment.complete.json': b'experiment',
        'dataset/dashboard-data.jsonl': b'dataset',
        'dataset/dashboard-data.manifest.json': b'manifest',
        'comparison-report.json': b'report',
    }
    for name, contents in files.items():
        (root / name).write_bytes(contents)
    plan = {'experiment_id': 'experiment-test'}
    report = {'overall': {'status': 'No regression'}}

    completion = workflow._completion_manifest(
        root,
        plan,
        root / 'dataset' / 'dashboard-data.jsonl',
        {'dataset_sha256': DATASET_SHA},
        root / 'comparison-report.json',
        report,
    )

    assert completion['plan_sha256'] == hashlib.sha256(b'plan').hexdigest()
    assert completion['target_manifest_sha256']['reference'] == hashlib.sha256(
        b'reference'
    ).hexdigest()
    assert completion['report_sha256'] == hashlib.sha256(b'report').hexdigest()
    assert completion['schema_version'] == 2


@pytest.mark.parametrize(
    'field',
    (
        'schema_version',
        'experiment_id',
        'plan_sha256',
        'target_manifest_sha256',
        'experiment_completion_sha256',
        'dataset',
        'dataset_sha256',
        'dataset_manifest_sha256',
        'report',
        'report_sha256',
        'overall_status',
        'comparison_exit_code',
    ),
)
def test_workflow_completion_rejects_every_changed_stable_field(tmp_path, field):
    plan = {'experiment_id': 'experiment-test'}
    report = {'overall': {'status': 'No regression'}}
    dataset_path, dataset_manifest, report_path = _write_completion_bundle(
        tmp_path,
        plan,
        report,
    )
    completion_path = tmp_path / workflow.WORKFLOW_COMPLETE_FILENAME
    completion = json.loads(completion_path.read_text())
    if field == 'target_manifest_sha256':
        completion[field]['reference'] = '0' * 64
    elif field == 'comparison_exit_code':
        completion[field] = 3
    elif field == 'schema_version':
        completion[field] = 1
    else:
        completion[field] = f'changed-{field}'
    workflow.write_json(completion, completion_path)

    assert not workflow._verify_workflow_completion(
        tmp_path,
        plan,
        dataset_path,
        dataset_manifest,
        report_path,
        report,
    )


def test_workflow_completion_allows_only_timestamp_change(tmp_path):
    plan = {'experiment_id': 'experiment-test'}
    report = {'overall': {'status': 'No regression'}}
    dataset_path, dataset_manifest, report_path = _write_completion_bundle(
        tmp_path,
        plan,
        report,
    )
    completion_path = tmp_path / workflow.WORKFLOW_COMPLETE_FILENAME
    completion = json.loads(completion_path.read_text())
    completion['completed_at'] = '2026-08-19T00:00:00+00:00'
    workflow.write_json(completion, completion_path)

    assert workflow._verify_workflow_completion(
        tmp_path,
        plan,
        dataset_path,
        dataset_manifest,
        report_path,
        report,
    )


def test_reuses_documented_invalid_comparison_without_dashboard_validation(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    plan = {
        'experiment_id': 'experiment-invalid',
        'targets': [
            {'label': 'reference', 'target_key': 'reference-key'},
            {'label': 'candidate', 'target_key': 'candidate-key'},
        ],
    }
    report = {
        'schema_version': workflow.REPORT_SCHEMA_VERSION,
        'experiment_id': plan['experiment_id'],
        'dataset': {
            'sha256': DATASET_SHA,
            'experiment_id': plan['experiment_id'],
        },
        'targets': {
            'reference': {
                'label': 'reference',
                'target_key': 'reference-key',
                'identity': {},
            },
            'candidate': {
                'label': 'candidate',
                'target_key': 'candidate-key',
                'identity': {},
            },
        },
        'analysis': {
            'confidence_level': options.confidence_level,
            'bootstrap_repeats': options.bootstrap_repeats,
            'seed': options.bootstrap_seed,
            'minimum_measured_trials': options.minimum_trials,
        },
        'overall': {'status': CANNOT_COMPARE},
        'categories': {
            category: {'status': CANNOT_COMPARE}
            for category in workflow.CATEGORIES
        },
        'scenarios': [],
    }
    report_path = tmp_path / workflow.REPORT_FILENAME
    dataset_path, dataset_manifest, report_path = _write_completion_bundle(
        tmp_path,
        plan,
        report,
    )
    monkeypatch.setattr(
        workflow,
        'validate_comparison_report',
        lambda *args: pytest.fail('invalid evidence is not dashboard-exportable'),
    )

    assert workflow._load_reusable_report(
        tmp_path,
        report_path,
        [],
        dataset_path,
        dataset_manifest,
        options,
        plan,
    ) == report

    report_path.write_text(report_path.read_text() + '\n')

    assert workflow._load_reusable_report(
        tmp_path,
        report_path,
        [],
        dataset_path,
        dataset_manifest,
        options,
        plan,
    ) is None


def _options(root, dry_run=False, order='balanced'):
    defaults = RunDefaults()
    return workflow.ComparisonWorkflowOptions(
        results_dir=str(root),
        reference_ref=REFERENCE_COMMIT,
        candidate_ref=CANDIDATE_COMMIT,
        ros_distro='rolling',
        suite='service-rclcpp-minimal',
        executor=defaults.executor,
        duration=1,
        cpuset_cpus='0-1',
        warmups=0,
        repeats=3,
        order=order,
        schedule_seed=7,
        cache_dir=str(root.parent / 'cache'),
        rclcpp_repository_url=RCLCPP_REPOSITORY,
        dry_run=dry_run,
        bootstrap_repeats=100,
    )


def _target(commit):
    return ClientLibraryTarget(
        name='rclcpp',
        source='build',
        repository_url=RCLCPP_REPOSITORY,
        requested_ref=commit,
        resolved_commit=commit,
        checkout_path=Path('/cache') / commit,
    )


def _source_dependencies():
    commit = 'e' * 40
    return SourceDependencySnapshot(
        repositories=(SourceDependency(
            path='ros2/rcl',
            repository_url='https://github.com/ros2/rcl.git',
            requested_ref=commit,
            resolved_commit=commit,
            checkout_path=Path('/cache/dependencies/ros2/rcl'),
        ),),
        checkout_path=Path('/cache/dependencies'),
    )


def _image(spec):
    return VerifiedImage(
        image_name=spec.image_name,
        image_id=f'sha256:{spec.target_key}',
        image_digest=f'sha256:{spec.target_key}',
        target_key=spec.target_key,
    )


def _call(calls, name, result):
    calls.append(name)
    return result


def _write_completion_bundle(root, plan, report):
    root = Path(root)
    (root / 'targets').mkdir(parents=True, exist_ok=True)
    dataset_path = root / 'dataset' / 'dashboard-data.jsonl'
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    (root / 'plan.json').write_text(json.dumps(plan))
    (root / 'targets' / 'reference.json').write_text('reference\n')
    (root / 'targets' / 'candidate.json').write_text('candidate\n')
    (root / 'experiment.complete.json').write_text('experiment\n')
    dataset_path.write_text('dataset\n')
    (root / 'dataset' / 'dashboard-data.manifest.json').write_text('manifest\n')
    report_path = root / workflow.REPORT_FILENAME
    workflow.write_json(report, report_path)
    dataset_manifest = {'dataset_sha256': DATASET_SHA}
    completion = workflow._completion_manifest(
        root,
        plan,
        dataset_path,
        dataset_manifest,
        report_path,
        report,
    )
    workflow.write_json(completion, root / workflow.WORKFLOW_COMPLETE_FILENAME)
    return dataset_path, dataset_manifest, report_path
