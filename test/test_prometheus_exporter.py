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

from ros2_performance_monitoring.exporters.prometheus import records_to_prometheus


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
