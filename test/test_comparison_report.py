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

from ros2_performance_monitoring.comparison import CATEGORIES
from ros2_performance_monitoring.comparison_report import ComparisonReportError
from ros2_performance_monitoring.comparison_report import validate_comparison_report
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import NO_REGRESSION
from ros2_performance_monitoring.statistical_comparison import NOT_APPLICABLE
from ros2_performance_monitoring.statistical_comparison import REPORT_SCHEMA_VERSION
from ros2_performance_monitoring.statistical_comparison import SCENARIO_FIELDS


DATASET_CHECKSUM = 'd' * 64


def test_valid_report_resolves_only_its_reference_and_candidate_aggregate_runs():
    report, records = _fixture()
    report = json.loads(json.dumps(report, sort_keys=True))
    records.append(_record('unrelated', 'e' * 40))

    validated = validate_comparison_report(report, records, DATASET_CHECKSUM)

    assert validated.report is report
    assert validated.reference_run == 'reference-median'
    assert validated.candidate_run == 'candidate-median'


@pytest.mark.parametrize(
    ('field', 'message'),
    (
        ('checksum', 'checksum does not match'),
        ('experiment', 'experiment identity is inconsistent'),
        ('target', 'reference target does not resolve'),
        ('schema', 'unsupported comparison report schema'),
        ('scenario', 'scenario coverage does not match'),
        ('method', 'unsupported comparison method'),
    ),
)
def test_report_validation_rejects_stale_or_incompatible_identity(field, message):
    report, records = _fixture()
    if field == 'checksum':
        report['dataset']['sha256'] = '0' * 64
    elif field == 'experiment':
        report['experiment_id'] = 'different-experiment'
    elif field == 'target':
        report['targets']['reference']['identity']['client_library'][
            'resolved_commit'
        ] = 'f' * 40
    elif field == 'schema':
        report['schema_version'] = REPORT_SCHEMA_VERSION + 1
    elif field == 'scenario':
        report['scenarios'][0]['identity']['payload_size'] = 100
    elif field == 'method':
        report['analysis']['method'] = 'different-method'

    with pytest.raises(ComparisonReportError, match=message):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_report_validation_rejects_malformed_evidence():
    report, records = _fixture()
    report['categories']['latency']['confidence_interval']['upper'] = 'wide'

    with pytest.raises(ComparisonReportError, match='interval is invalid'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def _fixture():
    scenario = dict(zip(
        SCENARIO_FIELDS,
        (
            'pub-sub',
            'single_process',
            10,
            200.0,
            'rmw_fastrtps_cpp',
            'ipc_off',
            'EventsExecutor',
            '',
        ),
    ))
    report = {
        'schema_version': REPORT_SCHEMA_VERSION,
        'experiment_id': 'experiment-report-test',
        'dataset': {
            'sha256': DATASET_CHECKSUM,
            'experiment_id': 'experiment-report-test',
        },
        'targets': {
            'reference': _target('reference', 'b' * 40),
            'candidate': _target('candidate', 'c' * 40),
        },
        'analysis': {
            'method': METHOD,
            'confidence_level': 0.95,
            'seed': 0,
            'bootstrap_repeats': 10000,
            'minimum_measured_trials': 3,
            'measured_trial_pairs': 3,
            'pairing': 'recorded balanced execution blocks',
            'point_estimator': 'median of measured trials',
        },
        'overall': _evidence(),
        'categories': {
            category: _evidence(
                status=NO_REGRESSION if category == 'latency' else NOT_APPLICABLE
            )
            for category in CATEGORIES
        },
        'scenarios': [{
            'identity': scenario,
            'categories': {'latency': _evidence()},
        }],
    }
    records = [
        _record('reference-median', 'b' * 40),
        _record('candidate-median', 'c' * 40),
    ]
    return report, records


def _target(label, commit):
    return {
        'label': label,
        'target_key': f'{label}-key',
        'identity': {
            'schema_version': 1,
            'ros_distro': 'rolling',
            'architecture': 'amd64',
            'benchmark_repository': {
                'url': 'https://github.com/ros2/ros2-benchmark-container.git',
                'requested_ref': 'rolling',
                'resolved_commit': 'a' * 40,
            },
            'client_library': {
                'name': 'rclcpp',
                'source': 'build',
                'repository_url': 'https://github.com/ros2/rclcpp.git',
                'requested_ref': commit,
                'resolved_commit': commit,
            },
            'build_configuration': {},
        },
    }


def _evidence(status=NO_REGRESSION):
    evidence = {
        'status': status,
        'practical_threshold': {'possible': 0.5, 'regression': 2.0, 'unit': 'percent'},
        'point_estimate': None,
        'confidence_interval': None,
        'responsible_scenario': None,
        'responsible_metric': None,
    }
    if status == NO_REGRESSION:
        evidence.update({
            'point_estimate': 0.0,
            'confidence_interval': {'lower': 0.0, 'upper': 0.0},
        })
    return deepcopy(evidence)


def _record(run_id, commit):
    return {
        'schema_version': 5,
        'run_id': run_id,
        'benchmark_ref': 'rolling',
        'benchmark_commit': 'a' * 40,
        'client_library_ref': commit,
        'client_library_commit': commit,
        'client_library': 'rclcpp',
        'client_library_source': 'build',
        'platform': 'x86_64',
        'ros_distro': 'rolling',
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': 'pub-sub',
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': 10,
        'frequency': 200.0,
        'node_role': '',
        'metric_name': 'subscription_latency',
        'numeric_value': 100.0,
        'unit': 'us',
        'aggregation': 'mean',
        'run_kind': 'aggregate',
        'aggregation_method': 'median',
        'repeat_count': 3,
    }
