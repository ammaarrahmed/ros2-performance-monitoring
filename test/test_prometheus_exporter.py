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
import re

from ros2_performance_monitoring.comparison_report import ValidatedComparisonReport
from ros2_performance_monitoring.exporters.prometheus import records_to_prometheus
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import REPORT_SCHEMA_VERSION


def test_records_to_prometheus_converts_normalized_metrics():
    """Test normalized metrics are exposed as Prometheus samples."""
    records = [
        _record('subscription_latency', 25.0, 'us', 'mean'),
        _record('resource_cpu', 30.0, '%', 'max'),
        _record('resource_memory_rss', 2048.0, 'KB', 'mean'),
        _record('total_messages_lost', 2.0, 'count', 'total'),
        _record('total_messages_lost', 1.5, '%', 'percent'),
        _record('subscription_throughput', 100.0, 'Kb/s', 'observed'),
    ]

    output = records_to_prometheus(records)

    assert 'ros2_perf_run_info{' in output
    assert 'ros2_perf_latency_us{' in output
    assert 'ros2_perf_cpu_percent{' in output
    assert 'ros2_perf_memory_megabytes{' in output
    assert 'ros2_perf_memory_megabytes{' in output and '} 2' in output
    assert 'ros2_perf_messages_total{' in output
    assert 'ros2_perf_messages_percent{' in output
    assert 'ros2_perf_throughput_kb_per_second{' in output
    assert 'ros2_perf_resource_samples_total{' in output
    assert 'rmw="rmw_fastrtps_cpp"' in output
    assert 'benchmark_ref="benchmark-branch"' in output
    assert 'client_library_ref="client-branch"' in output
    assert 'client_library_commit="abc123"' in output
    assert 'client_library_version="abc123"' in output
    assert 'client_source="build"' in output
    assert 'comm="ipc_off"' in output
    assert 'payload_bytes="10"' in output
    assert 'platform="x86_64"' in output
    assert 'run_kind="measured"' in output
    assert 'aggregation_method="none"' in output
    assert 'repeat_count="1"' in output
    assert 'run_display="run-a (measured)"' in output
    assert 'source_file' not in output


def test_aggregate_metadata_is_exposed_only_on_run_info():
    record = _record('subscription_latency', 25.0, 'us', 'mean')
    record.update({
        'run_kind': 'aggregate',
        'aggregation_method': 'median',
        'repeat_count': 3,
    })

    output = records_to_prometheus([record])

    run_info = next(
        line for line in output.splitlines()
        if line.startswith('ros2_perf_run_info{')
    )
    metric = next(
        line for line in output.splitlines()
        if line.startswith('ros2_perf_latency_us{')
    )
    assert 'run_kind="aggregate"' in run_info
    assert 'aggregation_method="median"' in run_info
    assert 'repeat_count="3"' in run_info
    assert 'run_display="run-a (median, n=3)"' in run_info
    assert 'run_kind=' not in metric
    assert 'aggregation_method=' not in metric
    assert 'repeat_count=' not in metric


def test_ros_distro_label_uses_record_value():
    output = records_to_prometheus([
        _record('subscription_latency', 25.0, 'us', 'mean', ros_distro='rolling'),
    ])

    assert 'ros_distro="rolling"' in output


def test_packaged_client_uses_packaged_version_label():
    record = _record('subscription_latency', 25.0, 'us', 'mean')
    record.update({
        'client_library_commit': 'unknown',
        'client_library_ref': 'ros-lyrical-packages',
        'client_library_source': 'ros_distro_package',
    })

    output = records_to_prometheus([record])

    assert 'client_library_version="packaged"' in output
    assert 'client_source="packaged"' in output


def test_records_to_prometheus_reuses_generic_families_for_service_metrics():
    """Test service records flow through existing Prometheus families."""
    records = [
        _record('service_client_latency', 40.0, 'us', 'p95', topology='service'),
        _record('service_server_latency', 50.0, 'us', 'p95', topology='service'),
        _record('resource_cpu', 30.0, '%', 'max', topology='service'),
        _record('resource_memory_rss', 2048.0, 'KB', 'max', topology='service'),
    ]

    output = records_to_prometheus(records)

    assert 'ros2_perf_latency_us{' in output
    assert 'metric="service_client_latency"' in output
    assert 'metric="service_server_latency"' in output
    assert 'ros2_perf_cpu_percent{' in output
    assert 'ros2_perf_memory_megabytes{' in output
    assert 'topology="service"' in output


def test_incomplete_comparison_statuses_are_exported_for_dashboard_queries():
    baseline = _complete_pubsub_run('baseline')
    candidate = _complete_pubsub_run('candidate')

    output = records_to_prometheus([*baseline, *candidate])

    comparison_lines = [
        line for line in output.splitlines()
        if line.startswith('ros2_perf_comparison_status{')
        and 'baseline_run="baseline"' in line
        and 'candidate_run="candidate"' in line
    ]
    assert len(comparison_lines) == 5
    assert all(line.endswith(' 3') for line in comparison_lines)
    assert all('method="threshold-only-v1"' in line for line in comparison_lines)


def test_statistical_report_exports_only_report_pair_without_recalculating_status():
    reference = _record('subscription_latency', 100.0, 'us', 'mean')
    reference.update({
        'run_id': 'reference-median',
        'run_kind': 'aggregate',
        'aggregation_method': 'median',
        'repeat_count': 3,
    })
    candidate = deepcopy(reference)
    candidate.update({
        'run_id': 'candidate-median',
        'numeric_value': 1000.0,
    })
    unrelated = deepcopy(reference)
    unrelated['run_id'] = 'unrelated-run'
    validated = ValidatedComparisonReport(
        report=_statistical_report(),
        reference_run='reference-median',
        candidate_run='candidate-median',
    )

    output = records_to_prometheus(
        [reference, candidate, unrelated],
        validated,
    )

    status_lines = [
        line for line in output.splitlines()
        if line.startswith('ros2_perf_comparison_status{')
    ]
    assert len(status_lines) == 5
    assert all('baseline_run="reference-median"' in line for line in status_lines)
    assert all('candidate_run="candidate-median"' in line for line in status_lines)
    assert all('unrelated-run' not in line for line in status_lines)
    latency = next(line for line in status_lines if 'category="latency"' in line)
    assert 'evidence_state="No regression"' in latency
    assert f'method="{METHOD}"' in latency
    assert latency.endswith(' 0')

    assert (
        'ros2_perf_comparison_analysis{' in output
        and 'confidence_level="0.95"' in output
        and 'repeat_count="3"' in output
    )
    assert _evidence_value(output, 'latency', 'point_estimate') == 3.0
    assert _evidence_value(output, 'latency', 'interval_lower') == 1.0
    assert _evidence_value(output, 'latency', 'interval_upper') == 5.0
    assert _evidence_value(output, 'latency', 'possible_threshold') == 0.5
    assert _evidence_value(output, 'latency', 'regression_threshold') == 2.0
    assert 'ros2_perf_comparison_scenario_status{' in output
    assert 'responsible_payload_bytes="10"' in latency
    assert all('topology="pub-sub"' in line for line in status_lines)
    assert all('topology="all"' not in line for line in status_lines)


def test_mixed_statistical_report_exports_each_topology_and_report_wide_summary():
    reference = _record('subscription_latency', 100.0, 'us', 'mean')
    reference.update({
        'run_id': 'reference-median',
        'run_kind': 'aggregate',
        'aggregation_method': 'median',
        'repeat_count': 3,
    })
    candidate = deepcopy(reference)
    candidate['run_id'] = 'candidate-median'
    report = _statistical_report()
    service_overall = deepcopy(report['overall'])
    service_overall['responsible_scenario']['topology'] = 'service'
    service_latency = deepcopy(report['categories']['latency'])
    service_latency['responsible_scenario']['topology'] = 'service'
    report['topologies']['service'] = {
        'overall': service_overall,
        'categories': {
            **{
                category: deepcopy(evidence)
                for category, evidence in report['categories'].items()
            },
            'latency': service_latency,
        },
    }

    output = records_to_prometheus(
        [reference, candidate],
        ValidatedComparisonReport(
            report=report,
            reference_run='reference-median',
            candidate_run='candidate-median',
        ),
    )

    status_lines = [
        line for line in output.splitlines()
        if line.startswith('ros2_perf_comparison_status{')
    ]
    assert len(status_lines) == 15
    assert {
        topology
        for topology in ('all', 'pub-sub', 'service')
        if any(f'topology="{topology}"' in line for line in status_lines)
    } == {'all', 'pub-sub', 'service'}
    for category in ('throughput', 'reliability'):
        service = next(
            line for line in status_lines
            if 'topology="service"' in line and f'category="{category}"' in line
        )
        assert 'evidence_state="N/A"' in service
        assert service.endswith(' 5')

    query_values = {
        'library': 'rclcpp',
        'platform': 'x86_64',
        'client_source': 'build',
        'baseline_distro': 'lyrical',
        'candidate_distro': 'lyrical',
        'baseline_run': 'reference-median',
        'candidate_run': 'candidate-median',
        'evidence_category': 'latency',
    }
    dashboard_directory = (
        Path(__file__).resolve().parents[1] / 'config' / 'grafana' / 'dashboards'
    )
    for filename in ('regression_overview.json', 'rclcpp_pubsub_overview.json'):
        dashboard = json.loads((dashboard_directory / filename).read_text())
        for title in ('Overall status', 'Effect estimate'):
            panel = next(
                panel for panel in dashboard['panels'] if panel['title'] == title
            )
            expression = panel['targets'][0]['expr']
            for topology in ('pub-sub', 'service'):
                _assert_query_matches_one_series(
                    output,
                    expression,
                    {**query_values, 'topology': topology},
                )


def _evidence_value(output, category, statistic):
    line = next(
        line for line in output.splitlines()
        if line.startswith('ros2_perf_comparison_evidence{')
        and f'category="{category}"' in line
        and f'statistic="{statistic}"' in line
    )
    return float(line.rsplit(' ', 1)[1])


def _assert_query_matches_one_series(output, expression, variables):
    rendered = expression
    for name, value in variables.items():
        rendered = rendered.replace(f'${name}', value)
    family, selector = rendered.split('{', 1)
    selector = selector.split('}', 1)[0]
    labels = re.findall(r'(\w+)="([^"]*)"', selector)
    matching = [
        line for line in output.splitlines()
        if line.startswith(f'{family}{{')
        and all(
            dict(re.findall(r'(\w+)="([^"]*)"', line)).get(name) == value
            for name, value in labels
        )
    ]
    assert len(matching) == 1, rendered


def _statistical_report():
    scenario = {
        'topology': 'pub-sub',
        'process_mode': 'single_process',
        'payload_size': 10,
        'frequency': 200.0,
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'communication_mode': 'ipc_off',
        'executor': 'EventsExecutor',
        'node_role': '',
    }
    latency = {
        'status': 'No regression',
        'practical_threshold': {
            'possible': 0.5,
            'regression': 2.0,
            'unit': 'percent',
        },
        'point_estimate': 3.0,
        'confidence_interval': {'lower': 1.0, 'upper': 5.0},
        'responsible_scenario': scenario,
        'responsible_metric': {
            'metric_name': 'subscription_latency',
            'aggregation': 'mean',
            'source_unit': 'us',
        },
    }
    not_applicable = {
        'status': 'N/A',
        'practical_threshold': None,
        'point_estimate': None,
        'confidence_interval': None,
        'responsible_scenario': None,
        'responsible_metric': None,
    }
    report = {
        'schema_version': REPORT_SCHEMA_VERSION,
        'experiment_id': 'experiment-export-test',
        'dataset': {
            'sha256': 'd' * 64,
            'experiment_id': 'experiment-export-test',
        },
        'targets': {},
        'analysis': {
            'method': METHOD,
            'confidence_level': 0.95,
            'measured_trial_pairs': 3,
        },
        'overall': deepcopy(latency),
        'categories': {
            'latency': deepcopy(latency),
            'throughput': deepcopy(not_applicable),
            'resources': deepcopy(not_applicable),
            'reliability': deepcopy(not_applicable),
        },
        'topologies': {},
        'scenarios': [{
            'identity': scenario,
            'categories': {'latency': deepcopy(latency)},
        }],
    }
    report['topologies']['pub-sub'] = {
        'overall': deepcopy(report['overall']),
        'categories': deepcopy(report['categories']),
    }
    return report


def _complete_pubsub_run(run_id):
    specifications = (
        ('subscription_latency', 100.0, 'us', 'mean'),
        ('subscription_latency', 200.0, 'us', 'p95'),
        ('subscription_throughput', 100.0, 'Kb/s', 'observed'),
        ('resource_cpu', 30.0, '%', 'max'),
        ('resource_memory_rss', 2000.0, 'KB', 'max'),
        ('total_messages_lost', 0.0, '%', 'percent'),
        ('total_messages_late', 0.0, '%', 'percent'),
        ('total_messages_too_late', 0.0, '%', 'percent'),
    )
    records = []
    for metric_name, value, unit, aggregation in specifications:
        record = deepcopy(_record(metric_name, value, unit, aggregation))
        record['run_id'] = run_id
        records.append(record)
    return records


def _record(
    metric_name,
    value,
    unit,
    aggregation,
    topology='pub-sub',
    ros_distro='lyrical',
):
    return {
        'schema_version': 1,
        'run_id': 'run-a',
        'timestamp': '2026-06-29T00:00:00Z',
        'benchmark_ref': 'benchmark-branch',
        'benchmark_commit': 'def456',
        'client_library_ref': 'client-branch',
        'client_library_commit': 'abc123',
        'client_library': 'rclcpp',
        'client_library_source': 'source_build',
        'platform': 'AMD64',
        'ros_distro': ros_distro,
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': topology,
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': 10,
        'frequency': 200.0,
        'metric_name': metric_name,
        'numeric_value': value,
        'unit': unit,
        'aggregation': aggregation,
        'source_file': '/tmp/results/resources.txt',
    }
