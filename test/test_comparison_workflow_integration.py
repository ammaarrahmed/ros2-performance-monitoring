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

import hashlib
import json
import os
from pathlib import Path

import pytest
from ros2_performance_monitoring.comparison_workflow import ComparisonWorkflowOptions
from ros2_performance_monitoring.comparison_workflow import run_comparison_workflow


pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.skipif(
        os.environ.get('ROS2_PERFORMANCE_RUN_WORKFLOW_INTEGRATION') != '1',
        reason=(
            'set ROS2_PERFORMANCE_RUN_WORKFLOW_INTEGRATION=1 to run two '
            'source targets through a complete comparison'
        ),
    ),
]

ROLLING_REFERENCE_COMMIT = '20536064aac0d547e128d95337867b473c3efa85'


def test_two_source_targets_produce_a_complete_validated_bundle(tmp_path):
    cache_dir = os.environ.get(
        'ROS2_PERFORMANCE_INTEGRATION_CACHE',
        '~/.cache/ros2-performance-monitoring-integration',
    )
    reference_ref = os.environ.get(
        'ROS2_PERFORMANCE_INTEGRATION_REFERENCE_REF',
        ROLLING_REFERENCE_COMMIT,
    )
    candidate_ref = os.environ.get(
        'ROS2_PERFORMANCE_INTEGRATION_CANDIDATE_REF',
        'rolling',
    )
    root = tmp_path / 'comparison'
    result = run_comparison_workflow(ComparisonWorkflowOptions(
        results_dir=str(root),
        reference_ref=reference_ref,
        candidate_ref=candidate_ref,
        ros_distro=os.environ.get('ROS2_PERFORMANCE_INTEGRATION_DISTRO', 'rolling'),
        suite='service-rclcpp-minimal',
        executor='EventsCBGExecutor',
        duration=1,
        cpuset_cpus=os.environ.get('ROS2_PERFORMANCE_INTEGRATION_CPUSET'),
        warmups=0,
        repeats=3,
        order='balanced',
        schedule_seed=0,
        cache_dir=cache_dir,
        bootstrap_repeats=100,
    ))

    assert result.exit_code in (0, 1, 2)
    assert result.completed_trials == 6
    assert result.failed_trials == 0
    assert result.reference_commit != result.candidate_commit
    required = (
        'workflow.log',
        'workflow.status.json',
        'plan.json',
        'targets/reference.json',
        'targets/candidate.json',
        'measured_environment.json',
        'dataset/dashboard-data.jsonl',
        'dataset/dashboard-data.manifest.json',
        'experiment.complete.json',
        'comparison-report.json',
        'comparison.complete.json',
    )
    assert all((root / path).is_file() for path in required)

    plan = _read_json(root / 'plan.json')
    for trial in plan['schedule']['trials']:
        completion = _read_json(
            root / 'trials' / trial['trial_id'] / 'complete.json'
        )
        attempt = root / 'trials' / trial['trial_id'] / completion['attempt_path']
        assert (attempt / 'metadata.json').is_file()
        assert (attempt / 'normalized_metrics.jsonl').is_file()
        assert (attempt / 'trial.log').is_file()
        for filename, checksum in completion['files'].items():
            assert _sha256(attempt / filename) == checksum

    experiment_completion = _read_json(root / 'experiment.complete.json')
    assert experiment_completion['schema_version'] == 2
    assert experiment_completion['measured_environment'] == 'measured_environment.json'
    assert experiment_completion['measured_environment_sha256'] == _sha256(
        root / 'measured_environment.json'
    )

    completion = _read_json(root / 'comparison.complete.json')
    assert completion['schema_version'] == 2
    assert completion['plan_sha256'] == _sha256(root / 'plan.json')
    assert completion['target_manifest_sha256'] == {
        label: _sha256(root / 'targets' / f'{label}.json')
        for label in ('reference', 'candidate')
    }
    assert completion['experiment_completion_sha256'] == _sha256(
        root / 'experiment.complete.json'
    )
    assert completion['dataset_sha256'] == _sha256(
        root / 'dataset' / 'dashboard-data.jsonl'
    )
    assert completion['dataset_manifest_sha256'] == _sha256(
        root / 'dataset' / 'dashboard-data.manifest.json'
    )
    assert completion['report_sha256'] == _sha256(
        root / 'comparison-report.json'
    )
    assert completion['comparison_exit_code'] == result.exit_code


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
