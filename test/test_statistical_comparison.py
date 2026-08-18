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
from ros2_performance_monitoring.statistical_comparison import build_comparison_report
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE
from ros2_performance_monitoring.statistical_comparison import comparison_exit_code
from ros2_performance_monitoring.statistical_comparison import INCOMPLETE_RESULTS
from ros2_performance_monitoring.statistical_comparison import INSUFFICIENT_EVIDENCE
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import NO_REGRESSION
from ros2_performance_monitoring.statistical_comparison import POSSIBLE_REGRESSION
from ros2_performance_monitoring.statistical_comparison import REGRESSION
from ros2_performance_monitoring.statistical_comparison import REPORT_SCHEMA_VERSION


def test_identical_stable_repeats_produce_no_regression_with_zero_width_intervals():
    plan, trials = _experiment(5)

    report = _report(plan, trials)

    assert report['overall']['status'] == NO_REGRESSION
    assert report['categories']['latency']['confidence_interval'] == {
        'lower': 0.0,
        'upper': 0.0,
    }
    assert {
        evidence['status'] for evidence in report['categories'].values()
    } == {NO_REGRESSION}


@pytest.mark.parametrize(
    ('metric_name', 'aggregation', 'reference_value', 'candidate_value', 'category'),
    (
        ('subscription_latency', 'mean', 100.0, 103.0, 'latency'),
        ('subscription_throughput', 'observed', 100.0, 97.0, 'throughput'),
        ('resource_memory_rss', 'max', 100.0, 106.0, 'resources'),
        ('total_messages_lost', 'percent', 0.0, 0.2, 'reliability'),
    ),
)
def test_consistent_adverse_effects_use_each_kpi_direction_and_unit(
    metric_name,
    aggregation,
    reference_value,
    candidate_value,
    category,
):
    plan, trials = _experiment(5)
    _set_all(trials, 'reference', metric_name, aggregation, [reference_value] * 5)
    _set_all(trials, 'candidate', metric_name, aggregation, [candidate_value] * 5)

    report = _report(plan, trials)

    evidence = report['categories'][category]
    assert evidence['status'] == REGRESSION
    assert evidence['point_estimate'] > evidence['practical_threshold']['regression']
    assert evidence['confidence_interval']['lower'] > (
        evidence['practical_threshold']['regression']
    )
    expected_unit = 'percentage_points' if category == 'reliability' else 'percent'
    assert evidence['practical_threshold']['unit'] == expected_unit
    assert report['overall']['status'] == REGRESSION


def test_effect_above_threshold_with_high_variance_is_only_possible():
    plan, trials = _experiment(5)
    _set_all(
        trials,
        'candidate',
        'subscription_latency',
        'mean',
        [50.0, 50.0, 110.0, 110.0, 110.0],
    )

    report = _report(plan, trials)

    evidence = report['categories']['latency']
    assert evidence['point_estimate'] > evidence['practical_threshold']['regression']
    assert evidence['confidence_interval']['lower'] == 0.0
    assert evidence['status'] == POSSIBLE_REGRESSION
    assert report['overall']['status'] == POSSIBLE_REGRESSION


def test_too_few_measured_pairs_produce_insufficient_evidence_without_bootstrap():
    plan, trials = _experiment(2)

    report = _report(plan, trials)

    assert report['analysis']['measured_trial_pairs'] == 2
    assert report['overall']['status'] == INSUFFICIENT_EVIDENCE
    assert report['overall']['confidence_interval'] is None


def test_warmups_failed_trials_and_aggregate_records_are_not_samples():
    plan, trials = _experiment(3, warmups=1)
    warmup_ids = [
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'warmup'
    ]
    trials[warmup_ids[0]] = _records(warmup_ids[0], 'reference', 999.0)
    trials['failed-unplanned-trial'] = _records(
        'failed-unplanned-trial', 'candidate', 999.0
    )
    first_measured = next(
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'measured'
    )
    aggregate = deepcopy(trials[first_measured][0])
    aggregate.update({
        'run_id': 'aggregate-median-test',
        'run_kind': 'aggregate',
        'numeric_value': 999.0,
    })
    trials[first_measured].append(aggregate)

    report = _report(plan, trials)

    assert report['analysis']['measured_trial_pairs'] == 3
    assert report['overall']['status'] == NO_REGRESSION


def test_missing_pairing_information_is_reported_explicitly():
    plan, trials = _experiment(3)
    plan['schedule']['trials'] = [
        trial for trial in plan['schedule']['trials']
        if not (
            trial['kind'] == 'measured'
            and trial['target'] == 'candidate'
            and trial['sequence'] == 2
        )
    ]

    report = _report(plan, trials)

    assert report['overall']['status'] == CANNOT_COMPARE
    assert 'both targets' in report['overall']['reason']


def test_missing_completed_trial_is_incomplete_not_a_smaller_sample():
    plan, trials = _experiment(3)
    missing = next(
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'measured' and trial['target'] == 'reference'
    )
    del trials[missing]

    report = _report(plan, trials)

    assert report['overall']['status'] == INCOMPLETE_RESULTS
    assert 'not complete' in report['overall']['reason']


def test_improvement_in_one_metric_cannot_cancel_another_metric_regression():
    plan, trials = _experiment(5)
    _set_all(trials, 'candidate', 'subscription_latency', 'mean', [50.0] * 5)
    _set_all(trials, 'candidate', 'subscription_latency', 'p95', [206.0] * 5)

    report = _report(plan, trials)

    evidence = report['categories']['latency']
    assert evidence['status'] == REGRESSION
    assert evidence['responsible_metric']['aggregation'] == 'p95'


def test_noisy_scenario_uses_the_category_level_worst_scenario_distribution():
    plan, trials = _experiment(5, payload_sizes=(10, 100))
    _set_all(
        trials,
        'candidate',
        'subscription_latency',
        'mean',
        [50.0, 50.0, 110.0, 110.0, 110.0],
        payload_size=100,
    )

    report = _report(plan, trials)

    evidence = report['categories']['latency']
    assert evidence['responsible_scenario']['payload_size'] == 100
    assert evidence['status'] == POSSIBLE_REGRESSION
    assert evidence['confidence_interval']['lower'] == 0.0


def test_changed_scenario_coverage_and_incompatible_provenance_prevent_analysis():
    plan, trials = _experiment(3)
    candidate_id = next(
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'measured' and trial['target'] == 'candidate'
    )
    trials[candidate_id] = trials[candidate_id][:-1]

    coverage_report = _report(plan, trials)

    assert coverage_report['overall']['status'] == CANNOT_COMPARE
    assert 'coverage differs' in coverage_report['overall']['reason']

    plan, trials = _experiment(3)
    candidate_id = next(
        trial['trial_id'] for trial in plan['schedule']['trials']
        if trial['kind'] == 'measured' and trial['target'] == 'candidate'
    )
    trials[candidate_id][0]['benchmark_commit'] = 'f' * 40

    provenance_report = _report(plan, trials)

    assert provenance_report['overall']['status'] == CANNOT_COMPARE
    assert 'provenance is incompatible' in provenance_report['overall']['reason']


def test_incompatible_target_identity_prevents_analysis_before_statistics():
    plan, trials = _experiment(3)
    plan['targets'][1]['identity']['architecture'] = 'arm64'

    report = _report(plan, trials)

    assert report['overall']['status'] == CANNOT_COMPARE
    assert 'incompatible benchmark provenance' in report['overall']['reason']


def test_reversing_targets_reverses_effect_sign_and_directional_verdict():
    plan, trials = _experiment(5)
    _set_all(trials, 'candidate', 'subscription_latency', 'mean', [103.0] * 5)

    forward = _report(plan, trials)
    reverse = _report(plan, trials, reference='candidate', candidate='reference')

    forward_metric = _metric_evidence(forward, 'subscription_latency', 'mean')
    reverse_metric = _metric_evidence(reverse, 'subscription_latency', 'mean')
    assert forward_metric['point_estimate'] > 0.0
    assert reverse_metric['point_estimate'] < 0.0
    assert forward['categories']['latency']['status'] == REGRESSION
    assert reverse['categories']['latency']['status'] == NO_REGRESSION


def test_fixed_seed_produces_byte_identical_versioned_reports():
    plan, trials = _experiment(5)
    _set_all(
        trials,
        'candidate',
        'resource_cpu',
        'max',
        [98.0, 101.0, 103.0, 99.0, 102.0],
    )

    first = _report(plan, trials)
    second = _report(plan, trials)

    assert json.dumps(first, sort_keys=True, separators=(',', ':')) == json.dumps(
        second, sort_keys=True, separators=(',', ':')
    )
    assert first['schema_version'] == REPORT_SCHEMA_VERSION
    assert first['analysis'] == {
        'method': METHOD,
        'confidence_level': 0.95,
        'seed': 19,
        'bootstrap_repeats': 2000,
        'minimum_measured_trials': 3,
        'measured_trial_pairs': 5,
        'pairing': 'recorded balanced execution blocks',
        'point_estimator': 'median of measured trials',
    }
    assert first['scenarios'][0]['categories']['latency']['metrics']


@pytest.mark.parametrize(
    ('status', 'expected'),
    (
        (NO_REGRESSION, 0),
        (REGRESSION, 1),
        (POSSIBLE_REGRESSION, 2),
        (INSUFFICIENT_EVIDENCE, 2),
        (INCOMPLETE_RESULTS, 3),
        (CANNOT_COMPARE, 3),
        ('N/A', 3),
    ),
)
def test_report_status_maps_to_stable_exit_outcome(status, expected):
    assert comparison_exit_code({'overall': {'status': status}}) == expected


def _report(plan, trials, **kwargs):
    return build_comparison_report(
        plan,
        trials,
        bootstrap_repeats=2000,
        seed=19,
        **kwargs,
    )


def _experiment(repeats, warmups=0, payload_sizes=(10,)):
    targets = []
    for label, commit in (('reference', 'b' * 40), ('candidate', 'c' * 40)):
        targets.append({
            'label': label,
            'target_key': f'{label}-key',
            'identity': {
                'schema_version': 1,
                'ros_distro': 'rolling',
                'architecture': 'amd64',
                'client_library': {
                    'name': 'rclcpp',
                    'source': 'build',
                    'repository_url': 'https://github.com/ros2/rclcpp.git',
                    'requested_ref': commit,
                    'resolved_commit': commit,
                },
            },
        })
    trials = []
    trial_records = {}
    planned_order = 1
    for kind, count in (('warmup', warmups), ('measured', repeats)):
        for sequence in range(1, count + 1):
            labels = ('reference', 'candidate')
            if sequence % 2 == 0:
                labels = tuple(reversed(labels))
            for label in labels:
                trial_id = f'{label}-{kind}-{sequence:03d}'
                trials.append({
                    'trial_id': trial_id,
                    'kind': kind,
                    'target': label,
                    'sequence': sequence,
                    'planned_order': planned_order,
                })
                planned_order += 1
                if kind == 'measured':
                    records = []
                    for payload_size in payload_sizes:
                        records.extend(_records(trial_id, label, payload_size=payload_size))
                    trial_records[trial_id] = records
    plan = {
        'schema_version': 1,
        'experiment_id': 'experiment-statistics-test',
        'targets': targets,
        'schedule': {
            'order': 'balanced',
            'seed': 7,
            'warmup_count': warmups,
            'measured_repeat_count': repeats,
            'trials': trials,
        },
    }
    return plan, trial_records


def _records(run_id, label, latency=100.0, payload_size=10):
    commit = 'b' * 40 if label == 'reference' else 'c' * 40
    metrics = (
        ('subscription_latency', 'mean', latency, 'us'),
        ('subscription_latency', 'p95', 200.0, 'us'),
        ('subscription_throughput', 'observed', 100.0, 'Kb/s'),
        ('resource_cpu', 'max', 100.0, 'percent'),
        ('resource_memory_rss', 'max', 100.0, 'KB'),
        ('total_messages_lost', 'percent', 0.0, 'percent'),
        ('total_messages_late', 'percent', 0.0, 'percent'),
        ('total_messages_too_late', 'percent', 0.0, 'percent'),
    )
    return [
        {
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
            'executor': 'EventsExecutor',
            'topology': 'pub-sub',
            'process_mode': 'single_process',
            'communication_mode': 'ipc_off',
            'payload_size': payload_size,
            'frequency': 0.0,
            'rmw_implementation': 'rmw_fastrtps_cpp',
            'node_role': '',
            'metric_name': metric_name,
            'numeric_value': value,
            'unit': unit,
            'aggregation': aggregation,
            'run_kind': 'measured',
        }
        for metric_name, aggregation, value, unit in metrics
    ]


def _set_all(
    trials,
    target,
    metric_name,
    aggregation,
    values,
    payload_size=10,
):
    run_ids = sorted(run_id for run_id in trials if run_id.startswith(f'{target}-measured'))
    assert len(run_ids) == len(values)
    for run_id, value in zip(run_ids, values):
        record = next(
            record for record in trials[run_id]
            if record['metric_name'] == metric_name
            and record['aggregation'] == aggregation
            and record['payload_size'] == payload_size
        )
        record['numeric_value'] = value


def _metric_evidence(report, metric_name, aggregation):
    return next(
        metric
        for scenario in report['scenarios']
        for evidence in scenario['categories'].values()
        for metric in evidence['metrics']
        if metric['metric_name'] == metric_name and metric['aggregation'] == aggregation
    )
