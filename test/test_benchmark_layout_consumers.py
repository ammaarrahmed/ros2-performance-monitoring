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

from types import MappingProxyType

from ros2_performance_monitoring import benchmark_layout
from ros2_performance_monitoring.artifacts import discover_benchmark_artifacts
from ros2_performance_monitoring.benchmark_runner import _benchmark_config
from ros2_performance_monitoring.parsers.ros2_benchmark_container import infer_topology


def test_added_payload_definition_reaches_all_layout_consumers(tmp_path, monkeypatch):
    payloads = {
        **benchmark_layout.PAYLOADS,
        '8mb': benchmark_layout.PayloadDefinition('8mb', 8 * 1024 * 1024, '8 MiB'),
    }
    monkeypatch.setattr(benchmark_layout, 'PAYLOADS', MappingProxyType(payloads))
    leaf = (
        tmp_path
        / 'benchmark'
        / 'lyrical'
        / 'pub-sub_single_process'
        / 'pub_sub_200hz_8mb'
        / 'fastrtps_ipc_on'
    )
    leaf.mkdir(parents=True)
    for name in ('metadata.txt', 'resources.txt', 'latency_all.txt', 'latency_total.txt'):
        (leaf / name).write_text('\n')

    artifacts = discover_benchmark_artifacts(tmp_path)
    topology = infer_topology(leaf)
    runner_config = _benchmark_config('pub-sub_single_process')

    assert tuple(artifact.directory for artifact in artifacts) == (leaf,)
    assert topology['payload_size'] == 8 * 1024 * 1024
    assert '"pub_sub_200hz_8mb"' in runner_config
