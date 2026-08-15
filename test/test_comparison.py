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

import pytest
from ros2_performance_monitoring.comparison import CANNOT_COMPARE
from ros2_performance_monitoring.comparison import evaluate_comparison
from ros2_performance_monitoring.comparison import INCOMPLETE_RESULTS
from ros2_performance_monitoring.comparison import NO_REGRESSION
from ros2_performance_monitoring.comparison import NOT_APPLICABLE
from ros2_performance_monitoring.comparison import POSSIBLE_REGRESSION
from ros2_performance_monitoring.comparison import REGRESSION
from ros2_performance_monitoring.comparison import run_display_name


@pytest.mark.parametrize(
    ('metric_name', 'aggregation', 'candidate_value', 'category'),
    (
        ('subscription_latency', 'mean', 102.0, 'latency'),
        ('subscription_latency', 'p95', 204.0, 'latency'),
        ('subscription_throughput', 'observed', 98.0, 'throughput'),
        ('resource_cpu', 'max', 52.5, 'resources'),
        ('resource_memory_rss', 'max', 105.0, 'resources'),
        ('total_messages_lost', 'percent', 0.2, 'reliability'),
        ('total_messages_late', 'percent', 0.2, 'reliability'),
        ('total_messages_too_late', 'percent', 0.2, 'reliability'),
    ),
)
def test_each_kpi_can_raise_its_category_and_overall_status(
    metric_name,
    aggregation,
    candidate_value,
    category,
):
    baseline = _pubsub_records('baseline')
    candidate = _pubsub_records('candidate')
    _set_metric(candidate, metric_name, aggregation, candidate_value)

    statuses = evaluate_comparison(baseline, candidate, 'pub-sub')

    assert statuses[category] == REGRESSION
    assert statuses['overall'] == REGRESSION


def test_improvement_does_not_cancel_a_worse_metric():
    baseline = _pubsub_records('baseline')
    candidate = _pubsub_records('candidate')
    _set_metric(candidate, 'subscription_latency', 'mean', 102.0)
    _set_metric(candidate, 'subscription_latency', 'p95', 50.0)

    statuses = evaluate_comparison(baseline, candidate, 'pub-sub')

    assert statuses['latency'] == REGRESSION


@pytest.mark.parametrize(
    ('mean', 'p95', 'expected'),
    (
        (100.0, 200.0, NO_REGRESSION),
        (100.5, 200.0, POSSIBLE_REGRESSION),
        (100.5, 204.0, REGRESSION),
    ),
)
def test_highest_latency_severity_wins(mean, p95, expected):
    baseline = _pubsub_records('baseline')
    candidate = _pubsub_records('candidate')
    _set_metric(candidate, 'subscription_latency', 'mean', mean)
    _set_metric(candidate, 'subscription_latency', 'p95', p95)

    statuses = evaluate_comparison(baseline, candidate, 'pub-sub')

    assert statuses['latency'] == expected
    assert statuses['overall'] == expected


@pytest.mark.parametrize(
    ('metric_name', 'aggregation', 'category'),
    (
        ('subscription_latency', 'mean', 'latency'),
        ('subscription_throughput', 'observed', 'throughput'),
        ('resource_cpu', 'max', 'resources'),
        ('resource_memory_rss', 'max', 'resources'),
        ('total_messages_lost', 'percent', 'reliability'),
    ),
)
def test_missing_expected_metric_is_incomplete(metric_name, aggregation, category):
    baseline = _pubsub_records('baseline')
    candidate = _pubsub_records('candidate')
    candidate = [
        record for record in candidate
        if not (
            record['metric_name'] == metric_name
            and record['aggregation'] == aggregation
        )
    ]

    statuses = evaluate_comparison(baseline, candidate, 'pub-sub')

    assert statuses[category] == INCOMPLETE_RESULTS
    assert statuses['overall'] == INCOMPLETE_RESULTS


def test_missing_scenario_makes_every_status_not_comparable():
    baseline = _pubsub_records('baseline') + _pubsub_records(
        'baseline',
        payload_size=102400,
    )
    candidate = _pubsub_records('candidate')

    statuses = evaluate_comparison(baseline, candidate, 'pub-sub')

    assert set(statuses.values()) == {CANNOT_COMPARE}


def test_service_excludes_non_applicable_categories_from_overall():
    baseline = _service_records('baseline')
    candidate = _service_records('candidate')

    statuses = evaluate_comparison(baseline, candidate, 'service')

    assert statuses == {
        'overall': NO_REGRESSION,
        'latency': NO_REGRESSION,
        'throughput': NOT_APPLICABLE,
        'resources': NO_REGRESSION,
        'reliability': NOT_APPLICABLE,
    }


def test_aggregate_records_follow_the_same_evaluation_path():
    baseline = _pubsub_records('baseline')
    candidate = _pubsub_records('aggregate-median-candidate')
    for record in candidate:
        record.update({
            'run_kind': 'aggregate',
            'aggregation_method': 'median',
            'repeat_count': 3,
        })
    _set_metric(candidate, 'resource_memory_rss', 'max', 105.0)

    statuses = evaluate_comparison(baseline, candidate, 'pub-sub')

    assert statuses['resources'] == REGRESSION
    assert statuses['overall'] == REGRESSION


def test_run_display_name_labels_measured_and_aggregate_runs():
    assert run_display_name({'run_id': 'run-a'}) == 'run-a (measured)'
    assert run_display_name({
        'run_id': 'aggregate-id',
        'run_kind': 'aggregate',
        'aggregation_method': 'median',
        'repeat_count': 4,
    }) == 'aggregate-id (median, n=4)'


def _pubsub_records(run_id, payload_size=10):
    return [
        _record(run_id, 'subscription_latency', 100.0, 'mean', 'pub-sub', payload_size),
        _record(run_id, 'subscription_latency', 200.0, 'p95', 'pub-sub', payload_size),
        _record(run_id, 'subscription_throughput', 100.0, 'observed', 'pub-sub', payload_size),
        _record(run_id, 'resource_cpu', 50.0, 'max', 'pub-sub', payload_size),
        _record(run_id, 'resource_memory_rss', 100.0, 'max', 'pub-sub', payload_size),
        _record(run_id, 'total_messages_lost', 0.0, 'percent', 'pub-sub', payload_size),
        _record(run_id, 'total_messages_late', 0.0, 'percent', 'pub-sub', payload_size),
        _record(run_id, 'total_messages_too_late', 0.0, 'percent', 'pub-sub', payload_size),
    ]


def _service_records(run_id):
    return [
        _record(run_id, 'service_client_latency', 100.0, 'mean', 'service'),
        _record(run_id, 'service_client_latency', 200.0, 'p95', 'service'),
        _record(run_id, 'resource_cpu', 50.0, 'max', 'service'),
        _record(run_id, 'resource_memory_rss', 100.0, 'max', 'service'),
    ]


def _record(run_id, metric_name, value, aggregation, topology, payload_size=10):
    return {
        'run_id': run_id,
        'ros_distro': 'rolling',
        'client_library': 'rclcpp',
        'client_library_source': 'build',
        'client_library_ref': 'main',
        'client_library_commit': 'abc123',
        'platform': 'x86_64',
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': topology,
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': payload_size,
        'node_role': '',
        'metric_name': metric_name,
        'numeric_value': value,
        'aggregation': aggregation,
    }


def _set_metric(records, metric_name, aggregation, value):
    record = next(
        record for record in records
        if record['metric_name'] == metric_name
        and record['aggregation'] == aggregation
    )
    record['numeric_value'] = value
