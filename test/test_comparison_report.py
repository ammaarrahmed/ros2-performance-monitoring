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
from ros2_performance_monitoring.comparison import CATEGORY_THRESHOLDS
from ros2_performance_monitoring.comparison_report import ComparisonReportError
from ros2_performance_monitoring.comparison_report import validate_comparison_report
from ros2_performance_monitoring.exporters.prometheus import records_to_prometheus
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE
from ros2_performance_monitoring.statistical_comparison import INCOMPLETE_RESULTS
from ros2_performance_monitoring.statistical_comparison import INSUFFICIENT_EVIDENCE
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import NO_REGRESSION
from ros2_performance_monitoring.statistical_comparison import NOT_APPLICABLE
from ros2_performance_monitoring.statistical_comparison import POSSIBLE_REGRESSION
from ros2_performance_monitoring.statistical_comparison import REGRESSION
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
        report['categories']['latency']['responsible_scenario']['payload_size'] = 100
        report['overall']['responsible_scenario']['payload_size'] = 100
        scoped = report['topologies']['pub-sub']
        scoped['categories']['latency']['responsible_scenario']['payload_size'] = 100
        scoped['overall']['responsible_scenario']['payload_size'] = 100
    elif field == 'method':
        report['analysis']['method'] = 'different-method'

    with pytest.raises(ComparisonReportError, match=message):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_report_validation_rejects_malformed_evidence():
    report, records = _fixture()
    report['categories']['latency']['confidence_interval']['upper'] = 'wide'

    with pytest.raises(ComparisonReportError, match='interval is invalid'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


@pytest.mark.parametrize(
    ('status', 'point', 'lower', 'upper', 'overall_lower', 'overall_upper'),
    (
        (NO_REGRESSION, 0.0, 0.0, 0.0, 0.0, 0.0),
        (POSSIBLE_REGRESSION, 1.0, 0.0, 1.0, 0.0, 0.5),
        (REGRESSION, 3.0, 2.1, 4.0, 1.05, 2.0),
    ),
)
def test_each_statistical_verdict_validates_and_exports(
    status,
    point,
    lower,
    upper,
    overall_lower,
    overall_upper,
):
    report, records = _fixture()
    category = report['categories']['latency']
    scenario = report['scenarios'][0]['categories']['latency']
    metric = scenario['metrics'][0]
    for evidence in (category, scenario, metric):
        evidence['status'] = status
        evidence['point_estimate'] = point
        evidence['confidence_interval'] = {'lower': lower, 'upper': upper}
    report['overall'].update({
        'status': status,
        'point_estimate': point / CATEGORY_THRESHOLDS['latency'].regression,
        'confidence_interval': {
            'lower': overall_lower,
            'upper': overall_upper,
        },
    })
    report['topologies']['pub-sub'] = {
        'overall': deepcopy(report['overall']),
        'categories': deepcopy(report['categories']),
    }

    validated = validate_comparison_report(report, records, DATASET_CHECKSUM)
    output = records_to_prometheus(records, validated)

    assert f'evidence_state="{status}"' in output


@pytest.mark.parametrize('measured_pairs', (1, 2))
def test_insufficient_evidence_validates_and_exports_available_coverage(measured_pairs):
    report, records = _fixture()
    reason = f'{measured_pairs} measured pairs are below the required minimum'
    report['analysis']['measured_trial_pairs'] = measured_pairs
    report['overall'] = _empty_evidence(
        INSUFFICIENT_EVIDENCE,
        threshold=None,
        reason=reason,
    )
    report['categories'] = {
        category: _empty_evidence(
            INSUFFICIENT_EVIDENCE if category == 'latency' else NOT_APPLICABLE,
            threshold=_threshold(category),
            reason=reason if category == 'latency' else None,
        )
        for category in CATEGORIES
    }
    report['scenarios'][0]['categories']['latency'] = {
        'status': INSUFFICIENT_EVIDENCE,
        'practical_threshold': _threshold('latency'),
        'point_estimate': None,
        'confidence_interval': None,
        'responsible_metric': None,
        'metrics': [],
        'reason': reason,
    }
    report['topologies']['pub-sub'] = {
        'overall': deepcopy(report['overall']),
        'categories': deepcopy(report['categories']),
    }
    for record in records:
        record['repeat_count'] = measured_pairs
        if measured_pairs == 1:
            record['run_kind'] = 'measured'
            record.pop('aggregation_method')

    validated = validate_comparison_report(report, records, DATASET_CHECKSUM)
    output = records_to_prometheus(records, validated)

    assert 'evidence_state="Insufficient evidence"' in output
    assert 'ros2_perf_comparison_scenario_status{' in output


@pytest.mark.parametrize('status', (INCOMPLETE_RESULTS, CANNOT_COMPARE))
def test_invalid_outcomes_require_reasons_and_export_without_estimates(status):
    report, records = _fixture()
    reason = 'comparison evidence is incomplete'
    report['overall'] = _empty_evidence(status, threshold=None, reason=reason)
    report['categories'] = {
        category: _empty_evidence(
            status,
            threshold=_threshold(category),
            reason=reason,
        )
        for category in CATEGORIES
    }
    report['topologies'] = {}
    report['scenarios'] = []

    validated = validate_comparison_report(report, records, DATASET_CHECKSUM)
    output = records_to_prometheus(records, validated)

    assert f'evidence_state="{status}"' in output
    assert 'statistic="point_estimate"' not in output
    assert 'statistic="interval_lower"' not in output

    report['overall']['reason'] = ''
    with pytest.raises(ComparisonReportError, match='reason is missing'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('possible', float('nan')),
        ('possible', 3.0),
        ('regression', float('inf')),
        ('regression', 3.0),
        ('unit', 'milliseconds'),
    ),
)
def test_report_validation_rejects_invalid_or_non_policy_thresholds(field, value):
    report, records = _fixture()
    report['categories']['latency']['practical_threshold'][field] = value

    with pytest.raises(ComparisonReportError, match='practical threshold is invalid'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_report_validation_rejects_missing_threshold_fields():
    report, records = _fixture()
    del report['categories']['latency']['practical_threshold']['possible']

    with pytest.raises(ComparisonReportError, match='practical threshold is invalid'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_report_validation_rejects_responsible_scenario_not_in_coverage():
    report, records = _fixture()
    report['categories']['latency']['responsible_scenario']['payload_size'] = 100

    with pytest.raises(ComparisonReportError, match='responsible evidence is inconsistent'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_report_validation_rejects_responsible_metric_from_wrong_category():
    report, records = _fixture()
    report['scenarios'][0]['categories']['latency']['responsible_metric'] = {
        'metric_name': 'resource_cpu',
        'aggregation': 'max',
        'source_unit': 'percent',
    }

    with pytest.raises(ComparisonReportError, match='responsible metric is inconsistent'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_decisive_report_requires_minimum_pairs_and_scenario_coverage():
    report, records = _fixture()
    report['analysis']['measured_trial_pairs'] = 2

    with pytest.raises(ComparisonReportError, match='too few measured trial pairs'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)

    report, records = _fixture()
    report['scenarios'] = []
    with pytest.raises(ComparisonReportError, match='no scenario coverage'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_not_applicable_category_must_be_absent_from_scenario_coverage():
    report, records = _fixture()
    report['categories']['latency'] = _empty_evidence(
        NOT_APPLICABLE,
        threshold=_threshold('latency'),
    )

    with pytest.raises(ComparisonReportError, match='category latency status is inconsistent'):
        validate_comparison_report(report, records, DATASET_CHECKSUM)


def test_topology_summary_coverage_must_match_reported_scenarios():
    report, _records = _fixture()
    report['topologies'] = {}

    with pytest.raises(ComparisonReportError, match='summary coverage is inconsistent'):
        validate_comparison_report(report)


def test_topology_summary_responsible_scenario_must_stay_in_its_scope():
    report, _records = _fixture()
    summary = report['topologies']['pub-sub']
    summary['overall']['responsible_scenario']['topology'] = 'service'
    summary['categories']['latency']['responsible_scenario']['topology'] = 'service'

    with pytest.raises(ComparisonReportError, match='responsible evidence is inconsistent'):
        validate_comparison_report(report)


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
        'overall': _overall_evidence(scenario),
        'categories': {
            category: (
                _category_evidence(scenario)
                if category == 'latency'
                else _empty_evidence(
                    NOT_APPLICABLE,
                    threshold=_threshold(category),
                )
            )
            for category in CATEGORIES
        },
        'scenarios': [{
            'identity': scenario,
            'categories': {'latency': _scenario_evidence()},
        }],
    }
    report['topologies'] = {
        'pub-sub': {
            'overall': deepcopy(report['overall']),
            'categories': deepcopy(report['categories']),
        },
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


def _overall_evidence(scenario):
    return {
        'status': NO_REGRESSION,
        'practical_threshold': {
            'regression': 1.0,
            'unit': 'category_regression_threshold_multiple',
            'possible_by_category': {
                category: round(values.possible / values.regression, 12)
                for category, values in CATEGORY_THRESHOLDS.items()
            },
        },
        'point_estimate': 0.0,
        'confidence_interval': {'lower': 0.0, 'upper': 0.0},
        'responsible_category': 'latency',
        'responsible_scenario': deepcopy(scenario),
        'responsible_metric': _metric_reference(),
    }


def _category_evidence(scenario):
    return {
        'status': NO_REGRESSION,
        'practical_threshold': _threshold('latency'),
        'point_estimate': 0.0,
        'confidence_interval': {'lower': 0.0, 'upper': 0.0},
        'responsible_scenario': deepcopy(scenario),
        'responsible_metric': _metric_reference(),
    }


def _scenario_evidence():
    return {
        'status': NO_REGRESSION,
        'practical_threshold': _threshold('latency'),
        'point_estimate': 0.0,
        'confidence_interval': {'lower': 0.0, 'upper': 0.0},
        'responsible_metric': _metric_reference(),
        'metrics': [{
            **_metric_reference(),
            'adverse_direction': 'increase',
            'effect_unit': 'percent',
            'practical_threshold': _threshold('latency'),
            'point_estimate': 0.0,
            'confidence_interval': {'lower': 0.0, 'upper': 0.0},
            'status': NO_REGRESSION,
        }],
    }


def _empty_evidence(status, *, threshold, reason=None):
    evidence = {
        'status': status,
        'practical_threshold': threshold,
        'point_estimate': None,
        'confidence_interval': None,
        'responsible_scenario': None,
        'responsible_metric': None,
    }
    if reason is not None:
        evidence['reason'] = reason
    return evidence


def _threshold(category):
    values = CATEGORY_THRESHOLDS[category]
    return {
        'possible': values.possible,
        'regression': values.regression,
        'unit': 'percentage_points' if category == 'reliability' else 'percent',
    }


def _metric_reference():
    return {
        'metric_name': 'subscription_latency',
        'aggregation': 'mean',
        'source_unit': 'us',
    }


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
