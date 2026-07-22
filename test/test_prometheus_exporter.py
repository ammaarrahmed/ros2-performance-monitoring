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
    assert 'comm="ipc_off"' in output
    assert 'payload_bytes="10"' in output
    assert 'source_file' not in output


def test_ros_distro_label_uses_record_value():
    output = records_to_prometheus([
        _record('subscription_latency', 25.0, 'us', 'mean', ros_distro='rolling'),
    ])

    assert 'ros_distro="rolling"' in output


def _record(metric_name, value, unit, aggregation, ros_distro='lyrical'):
    return {
        'schema_version': 1,
        'run_id': 'run-a',
        'timestamp': '2026-06-29T00:00:00Z',
        'benchmark_ref': 'benchmark-branch',
        'benchmark_commit': 'def456',
        'client_library_ref': 'client-branch',
        'client_library_commit': 'abc123',
        'client_library': 'rclcpp',
        'ros_distro': ros_distro,
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': 'pub-sub',
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
