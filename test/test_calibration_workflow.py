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
import json
from pathlib import Path

import pytest
from ros2_performance_monitoring import comparison_workflow as workflow
from ros2_performance_monitoring.benchmark_image import VerifiedImage
from ros2_performance_monitoring.client_target import ClientLibraryTarget
from ros2_performance_monitoring.config import RunDefaults
from ros2_performance_monitoring.experiment import run_experiment
from ros2_performance_monitoring.writers.jsonl import write_json


RCLCPP_REPOSITORY = 'https://github.com/ros2/rclcpp.git'
CONTAINER_REPOSITORY = 'https://github.com/ros2/ros2-benchmark-container'
TARGET_COMMIT = 'a' * 40
BENCHMARK_COMMIT = 'b' * 40


def test_calibration_workflow_resumes_interruption_and_publishes_separate_evidence(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / 'calibration'
    options = _options(root)
    _mock_external_preparation(monkeypatch)
    interrupt = {'enabled': True}
    executed = []

    def execute_trial(stage, plan, trial, image_spec, verified_image):
        executed.append(trial['trial_id'])
        if interrupt['enabled'] and trial['planned_order'] == 3:
            raise RuntimeError('simulated calibration interruption')
        _successful_trial(stage, plan, trial, image_spec, verified_image)

    def execute_bundle(experiment_dir, plan, image_specs, images):
        return run_experiment(
            experiment_dir,
            plan,
            image_specs,
            images,
            trial_executor=execute_trial,
            environment_collector=_environment,
        )

    monkeypatch.setattr(workflow, 'run_experiment', execute_bundle)

    with pytest.raises(
        workflow.CalibrationWorkflowError,
        match='diagnostics kept',
    ):
        workflow.run_calibration_workflow(options)

    assert not (root / workflow.CALIBRATION_REPORT_FILENAME).exists()
    interrupted = json.loads((root / 'plan.json').read_text())['schedule']['trials'][2]
    attempts = root / 'trials' / interrupted['trial_id'] / 'attempts'
    assert (attempts / '0001-failed').is_dir()

    interrupt['enabled'] = False
    result = workflow.run_calibration_workflow(options)

    assert result.exit_code == 0
    assert result.overall_status is None
    assert result.dashboard_command == ()
    assert result.reference_commit == result.candidate_commit == TARGET_COMMIT
    assert result.reference_image_key == result.candidate_image_key
    assert result.reused_trials == 2
    assert (attempts / '0002').is_dir()
    assert not (root / workflow.REPORT_FILENAME).exists()
    assert not (root / workflow.WORKFLOW_COMPLETE_FILENAME).exists()
    report_path = root / workflow.CALIBRATION_REPORT_FILENAME
    completion_path = root / workflow.CALIBRATION_COMPLETE_FILENAME
    report = json.loads(report_path.read_text())
    completion = json.loads(completion_path.read_text())
    assert report['report_type'] == 'calibration'
    assert report['analysis']['measured_trial_pairs'] == 3
    assert report['streams']['reference']['target_key'] == (
        report['streams']['candidate']['target_key']
    )
    assert completion['evidence_type'] == 'calibration'
    assert completion['calibration_exit_code'] == 0

    stable_report = report_path.read_bytes()
    stable_completion = completion_path.read_bytes()
    executed.clear()
    resumed = workflow.run_calibration_workflow(options)

    assert resumed.reused_trials == resumed.completed_trials
    assert executed == []
    assert report_path.read_bytes() == stable_report
    assert completion_path.read_bytes() == stable_completion


def test_calibration_dry_run_resolves_target_once_and_writes_nothing(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / 'calibration'
    options = _options(root, dry_run=True)
    calls = []
    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: argparse.Namespace(
            architecture='amd64',
            result_filesystem_free_bytes=20 * 1024 ** 3,
            docker_filesystem_free_bytes=20 * 1024 ** 3,
        ),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_remote_rclcpp_target',
        lambda repository_url, requested_ref: (
            calls.append(requested_ref) or _target()
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
        lambda url, ref: BENCHMARK_COMMIT,
    )

    result = workflow.run_calibration_workflow(options)

    assert calls == [TARGET_COMMIT]
    assert result.plan['purpose'] == 'calibration'
    assert result.reference_image_key == result.candidate_image_key
    assert result.report_path.name == workflow.CALIBRATION_REPORT_FILENAME
    assert not root.exists()


def _mock_external_preparation(monkeypatch):
    monkeypatch.setattr(
        workflow,
        'run_comparison_preflight',
        lambda *args, **kwargs: argparse.Namespace(
            architecture='amd64',
            result_filesystem_free_bytes=20 * 1024 ** 3,
            docker_filesystem_free_bytes=20 * 1024 ** 3,
        ),
    )
    monkeypatch.setattr(
        workflow,
        'resolve_rclcpp_target',
        lambda repository_url, requested_ref, cache_dir: _target(),
    )
    monkeypatch.setattr(
        workflow,
        'get_default_container_repo',
        lambda: (CONTAINER_REPOSITORY, 'rolling'),
    )
    monkeypatch.setattr(
        workflow,
        'setup_container_repo',
        lambda **kwargs: BENCHMARK_COMMIT,
    )
    monkeypatch.setattr(workflow, 'benchmark_image_exists', lambda spec: True)
    monkeypatch.setattr(
        workflow,
        'verify_benchmark_image',
        lambda spec: VerifiedImage(
            image_name=spec.image_name,
            image_id='sha256:' + 'c' * 64,
            image_digest='sha256:' + 'c' * 64,
            target_key=spec.target_key,
        ),
    )


def _options(root, dry_run=False):
    defaults = RunDefaults()
    return workflow.CalibrationWorkflowOptions(
        results_dir=str(root),
        target_ref=TARGET_COMMIT,
        ros_distro='rolling',
        suite='service-rclcpp-minimal',
        executor=defaults.executor,
        duration=1,
        cpuset_cpus='0-1',
        warmups=1,
        repeats=3,
        schedule_seed=7,
        cache_dir=str(root.parent / 'cache'),
        rclcpp_repository_url=RCLCPP_REPOSITORY,
        dry_run=dry_run,
        bootstrap_repeats=100,
    )


def _target():
    return ClientLibraryTarget(
        name='rclcpp',
        source='build',
        repository_url=RCLCPP_REPOSITORY,
        requested_ref=TARGET_COMMIT,
        resolved_commit=TARGET_COMMIT,
        checkout_path=Path('/cache') / TARGET_COMMIT,
    )


def _successful_trial(stage, plan, trial, image_spec, verified_image):
    write_json({'run_id': trial['trial_id']}, stage / 'metadata.json')
    raw = stage / 'benchmark' / plan['configuration']['ros_distro'] / 'result.txt'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('raw benchmark output\n')
    common = {
        'schema_version': 5,
        'run_id': trial['trial_id'],
        'timestamp': '2026-08-20T00:00:00Z',
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
        'node_role': '',
        'run_kind': 'measured',
        'aggregation_method': 'none',
        'repeat_count': 1,
    }
    records = ({
        **common,
        'metric_name': metric_name,
        'numeric_value': value,
        'unit': unit,
        'aggregation': aggregation,
        'source_file': f'{metric_name}.txt',
    } for metric_name, aggregation, unit, value in (
        ('service_client_latency', 'mean', 'us', float(trial['planned_order'])),
        ('service_client_latency', 'p95', 'us', float(trial['planned_order'])),
        ('resource_cpu', 'max', 'percent', 10.0),
        ('resource_memory_rss', 'max', 'bytes', 1000.0),
    ))
    (stage / 'normalized_metrics.jsonl').write_text(
        ''.join(json.dumps(record) + '\n' for record in records)
    )


def _environment(plan, trial, image_spec, verified_image):
    return {
        'captured_at': f'2026-08-20T00:00:{trial["planned_order"]:02d}+00:00',
        'host': {
            'architecture': 'x86_64',
            'cpu_model': 'Test CPU',
            'kernel': 'test-kernel',
            'docker_version': '27.0.0',
            'cpuset_cpus': plan['configuration']['cpuset_cpus'],
            'cpu_governors': {'0': 'performance', '1': 'performance'},
        },
        'observations': {
            'load_average': {
                'one_minute': 0.2,
                'five_minutes': 0.1,
                'fifteen_minutes': 0.1,
            },
            'cpu_temperature_celsius': {'thermal_zone0:cpu': 45.0},
        },
        'configuration': dict(plan['configuration']),
        'trial': {'trial_id': trial['trial_id']},
        'target': {
            'target_key': image_spec.target_key,
            'benchmark_commit': image_spec.benchmark_resolved_commit,
            'image_id': verified_image.image_id,
        },
    }
