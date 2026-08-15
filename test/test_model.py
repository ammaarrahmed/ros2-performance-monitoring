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

from ros2_performance_monitoring.model import MetricRecord
from ros2_performance_monitoring.model import SCHEMA_VERSION
from ros2_performance_monitoring.model import SUPPORTED_SCHEMA_VERSIONS


def test_current_schema_serializes_run_aggregation_metadata():
    """Test current records include dashboard-selectable run metadata."""
    item = _record(SCHEMA_VERSION).to_dict()

    assert item['run_kind'] == 'measured'
    assert item['aggregation_method'] == 'none'
    assert item['repeat_count'] == 1


def test_legacy_schema_serialization_preserves_the_v4_shape():
    """Test supported v4 records do not acquire fields from schema v5."""
    item = _record(4).to_dict()

    assert 4 in SUPPORTED_SCHEMA_VERSIONS
    assert 'run_kind' not in item
    assert 'aggregation_method' not in item
    assert 'repeat_count' not in item


def _record(schema_version):
    return MetricRecord(
        schema_version=schema_version,
        run_id='run-a',
        timestamp='2026-08-15T00:00:00Z',
        benchmark_ref='rolling',
        benchmark_commit='benchmark-commit',
        client_library_ref='client-ref',
        client_library_commit='client-commit',
        client_library='rclcpp',
        client_library_source='build',
        platform='x86_64',
        ros_distro='rolling',
        rmw_implementation='rmw_fastrtps_cpp',
        executor='EventsExecutor',
        topology='pub-sub',
        process_mode='single_process',
        communication_mode='ipc_off',
        payload_size=10,
        frequency=10.0,
        metric_name='subscription_latency',
        numeric_value=5.0,
        unit='us',
        aggregation='mean',
        source_file='latency_all.txt',
    )
