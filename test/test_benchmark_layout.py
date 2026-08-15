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
from ros2_performance_monitoring import benchmark_layout


@pytest.mark.parametrize(
    ('token', 'size_bytes', 'display_label'),
    (
        ('10b', 10, '10 B'),
        ('100kb', 102400, '100 KiB'),
        ('1mb', 1048576, '1 MiB'),
        ('4mb', 4194304, '4 MiB'),
    ),
)
def test_payloads_have_canonical_sizes_and_labels(token, size_bytes, display_label):
    payload = benchmark_layout.get_payload(token)

    assert payload.token == token
    assert payload.size_bytes == size_bytes
    assert payload.display_label == display_label


@pytest.mark.parametrize(
    ('short_name', 'implementation_name'),
    (
        ('fastrtps', 'rmw_fastrtps_cpp'),
        ('cyclonedds', 'rmw_cyclonedds_cpp'),
        ('zenoh', 'rmw_zenoh_cpp'),
    ),
)
def test_rmw_names_have_canonical_ros_implementations(short_name, implementation_name):
    rmw = benchmark_layout.get_rmw(short_name)

    assert rmw.short_name == short_name
    assert rmw.implementation_name == implementation_name


@pytest.mark.parametrize(
    ('family_name', 'topology', 'process_mode', 'communication_modes'),
    (
        (
            'pub-sub_single_process',
            'pub-sub',
            'single_process',
            {
                'fastrtps': ('ipc_on', 'ipc_off', 'loaned'),
                'cyclonedds': ('ipc_off',),
                'zenoh': ('ipc_on', 'ipc_off'),
            },
        ),
        (
            'pub-sub_multi_process',
            'pub-sub',
            'multi_process',
            {
                'fastrtps': ('ipc_off', 'loaned'),
                'cyclonedds': ('ipc_off',),
                'zenoh': ('ipc_off',),
            },
        ),
        (
            'cli-srv_single_process',
            'service',
            'single_process',
            {
                'fastrtps': ('ipc_on', 'ipc_off'),
                'cyclonedds': ('ipc_off',),
                'zenoh': ('ipc_on', 'ipc_off'),
            },
        ),
        (
            'cli-srv_multi_process',
            'service',
            'multi_process',
            {
                'fastrtps': ('ipc_off',),
                'cyclonedds': ('ipc_off',),
                'zenoh': ('ipc_off',),
            },
        ),
    ),
)
def test_families_expose_topology_process_and_rmw_modes(
    family_name,
    topology,
    process_mode,
    communication_modes,
):
    family = benchmark_layout.get_benchmark_family(family_name)

    assert family.name == family_name
    assert family.topology == topology
    assert family.process_mode == process_mode
    assert family.communication_modes == communication_modes


def test_layout_mappings_are_immutable():
    with pytest.raises(TypeError):
        benchmark_layout.PAYLOADS['5mb'] = benchmark_layout.PayloadDefinition(
            '5mb',
            5 * 1024 * 1024,
            '5 MiB',
        )

    family = benchmark_layout.get_benchmark_family('pub-sub_single_process')
    with pytest.raises(TypeError):
        family.communication_modes['cyclonedds'] = ('ipc_on',)


@pytest.mark.parametrize(
    ('lookup', 'args', 'message'),
    (
        (benchmark_layout.get_payload, ('5mb',), 'unsupported payload 5mb'),
        (benchmark_layout.get_rmw, ('unknown',), 'unsupported RMW name unknown'),
        (
            benchmark_layout.get_benchmark_family,
            ('pub-sub_unknown',),
            'unsupported benchmark family pub-sub_unknown',
        ),
        (
            benchmark_layout.parse_rmw_directory,
            ('pub-sub_single_process', 'cyclonedds_ipc_on'),
            'unsupported communication mode ipc_on for cyclonedds',
        ),
    ),
)
def test_unknown_layout_values_raise_clear_errors(lookup, args, message):
    with pytest.raises(benchmark_layout.BenchmarkLayoutError, match=message):
        lookup(*args)
