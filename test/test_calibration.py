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

import pytest
from ros2_performance_monitoring.calibration import build_calibration_report
from ros2_performance_monitoring.calibration import CALIBRATION_METHOD
from ros2_performance_monitoring.calibration import CalibrationError
from ros2_performance_monitoring.calibration import validate_calibration_report
from ros2_performance_monitoring.comparison_report import ComparisonReportError
from ros2_performance_monitoring.comparison_report import validate_comparison_report
from ros2_performance_monitoring.statistical_comparison import build_comparison_report
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE


def test_fixed_calibration_fixture_reports_paired_noise_and_threshold_counts():
    plan, records, environments = _calibration_fixture()

    report = _report(plan, records, environments)

    assert report['report_type'] == 'calibration'
    assert report['analysis'] == {
        'method': CALIBRATION_METHOD,
        'confidence_level': 0.95,
        'bootstrap_repeats': 200,
        'seed': 17,
        'measured_trial_pairs': 3,
        'pairing': 'recorded balanced execution blocks',
        'point_estimator': 'median of measured trials',
        'classification_unit': 'individual paired adverse effect',
    }
    assert report['target']['identity']['benchmark_repository'][
        'resolved_commit'
    ] == 'b' * 40
    assert report['configuration']['cpuset_cpus'] == '0-1'
    assert report['environment']['identity']['docker_version'] == '27.0.0'
    assert len(report['environment']['measured_trial_observations']) == 6

    latency = next(
        kpi for kpi in report['kpis']
        if kpi['metric_name'] == 'service_client_latency'
        and kpi['aggregation'] == 'mean'
    )
    assert [item['adverse_effect'] for item in latency['paired_effects']] == [
        0.0,
        0.6,
        3.0,
    ]
    assert latency['observed_classifications'] == {
        'total': 3,
        'no_regression': 1,
        'possible_regression': 1,
        'regression': 1,
        'possible_or_regression': 2,
        'possible_or_regression_rate': pytest.approx(2 / 3),
    }
    assert latency['variability']['sample_standard_deviation'] > 0.0
    assert report['summary']['observed_classifications'][
        'possible_or_regression'
    ] == 4
    assert report['policy']['threshold_recommendations']['status'] == 'not_generated'
    validate_calibration_report(
        report,
        plan,
        records,
        environments,
        _measured_environment(),
        confidence_level=0.95,
        bootstrap_repeats=200,
        seed=17,
        dataset_sha256='d' * 64,
    )


def test_calibration_excludes_warmups_and_aggregate_records():
    plan, records, environments = _calibration_fixture()
    warmup = {
        'trial_id': 'reference-warmup-001-key',
        'kind': 'warmup',
        'target': 'reference',
        'target_key': 'target-key',
        'sequence': 1,
        'planned_order': 1,
    }
    shifted = deepcopy(plan)
    shifted['schedule']['warmup_count'] = 1
    shifted['schedule']['trials'].insert(0, warmup)
    records[warmup['trial_id']] = _records(warmup['trial_id'], 999.0)
    first_measured = next(iter(records))
    aggregate = deepcopy(records[first_measured][0])
    aggregate['run_kind'] = 'median'
    aggregate['numeric_value'] = 999.0
    records[first_measured] = (*records[first_measured], aggregate)

    report = _report(shifted, records, environments)

    assert report['analysis']['measured_trial_pairs'] == 3
    assert all(item['variability']['count'] == 3 for item in report['kpis'])


def test_calibration_report_is_deterministic_and_not_a_comparison_report():
    plan, records, environments = _calibration_fixture()

    first = _report(plan, records, environments)
    second = _report(plan, records, environments)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    with pytest.raises(ComparisonReportError):
        validate_comparison_report(first)
    comparison = build_comparison_report(plan, records)
    assert comparison['overall']['status'] == CANNOT_COMPARE


def test_calibration_requires_explicit_same_target_plan():
    plan, records, environments = _calibration_fixture()
    plan.pop('purpose')

    with pytest.raises(CalibrationError, match='explicit calibration plan'):
        _report(plan, records, environments)


def _report(plan, records, environments):
    return build_calibration_report(
        plan,
        records,
        environments,
        _measured_environment(),
        confidence_level=0.95,
        bootstrap_repeats=200,
        seed=17,
        dataset_sha256='d' * 64,
    )


def _calibration_fixture():
    target = {
        'label': 'reference',
        'target_key': 'target-key',
        'identity': {
            'ros_distro': 'rolling',
            'architecture': 'amd64',
            'benchmark_repository': {
                'url': 'https://github.com/ros2/ros2-benchmark-container',
                'requested_ref': 'rolling',
                'resolved_commit': 'b' * 40,
            },
            'client_library': {
                'name': 'rclcpp',
                'source': 'build',
                'repository_url': 'https://github.com/ros2/rclcpp.git',
                'requested_ref': 'a' * 40,
                'resolved_commit': 'a' * 40,
            },
        },
        'verified_image': {
            'name': 'calibration-image',
            'id': 'sha256:' + 'c' * 64,
            'digest': 'sha256:' + 'c' * 64,
            'target_key': 'target-key',
        },
    }
    candidate = deepcopy(target)
    candidate['label'] = 'candidate'
    trials = []
    records = {}
    environments = {}
    candidate_latency = (100.0, 100.6, 103.0)
    candidate_cpu = (100.0, 102.0, 106.0)
    for sequence in range(1, 4):
        labels = ('reference', 'candidate') if sequence % 2 else (
            'candidate', 'reference'
        )
        for label in labels:
            trial_id = f'{label}-measured-{sequence:03d}-target-key'
            trial = {
                'trial_id': trial_id,
                'kind': 'measured',
                'target': label,
                'target_key': 'target-key',
                'sequence': sequence,
                'planned_order': len(trials) + 1,
            }
            trials.append(trial)
            latency = 100.0 if label == 'reference' else candidate_latency[sequence - 1]
            cpu = 100.0 if label == 'reference' else candidate_cpu[sequence - 1]
            records[trial_id] = _records(trial_id, latency, cpu)
            environments[trial_id] = {
                'captured_at': f'2026-08-20T00:00:0{len(trials)}+00:00',
                'observations': {
                    'load_average': {
                        'one_minute': 0.25,
                        'five_minutes': 0.2,
                        'fifteen_minutes': 0.1,
                    },
                    'cpu_temperature_celsius': {'thermal_zone0:cpu': 45.0},
                },
            }
    return ({
        'schema_version': 1,
        'purpose': 'calibration',
        'experiment_id': 'experiment-calibration-test',
        'configuration': {
            'ros_distro': 'rolling',
            'suite': 'service-rclcpp-minimal',
            'executor': 'EventsExecutor',
            'duration': 10,
            'cpuset_cpus': '0-1',
        },
        'targets': [target, candidate],
        'schedule': {
            'order': 'balanced',
            'seed': 7,
            'warmup_count': 0,
            'measured_repeat_count': 3,
            'trials': trials,
        },
    }, records, environments)


def _records(trial_id, latency, cpu=100.0):
    common = {
        'schema_version': 5,
        'run_id': trial_id,
        'run_kind': 'measured',
        'benchmark_ref': 'rolling',
        'benchmark_commit': 'b' * 40,
        'client_library': 'rclcpp',
        'client_library_source': 'build',
        'client_library_ref': 'a' * 40,
        'client_library_commit': 'a' * 40,
        'platform': 'x86_64',
        'ros_distro': 'rolling',
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': 'service',
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': 10,
        'frequency': 0.0,
        'node_role': '',
    }
    return tuple({
        **common,
        'metric_name': metric_name,
        'aggregation': aggregation,
        'unit': unit,
        'numeric_value': value,
    } for metric_name, aggregation, unit, value in (
        ('service_client_latency', 'mean', 'us', latency),
        ('service_client_latency', 'p95', 'us', 100.0),
        ('resource_cpu', 'max', 'percent', cpu),
        ('resource_memory_rss', 'max', 'bytes', 100.0),
    ))


def _measured_environment():
    return {
        'architecture': 'x86_64',
        'cpu_model': 'Test CPU',
        'kernel': 'test-kernel',
        'docker_version': '27.0.0',
        'cpuset_cpus': '0-1',
        'cpu_governors': {'0': 'performance', '1': 'performance'},
    }
