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

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class BenchmarkLayoutError(ValueError):
    """Report an unsupported value in the shared benchmark layout."""


@dataclass(frozen=True)
class PayloadDefinition:
    """Describe one payload token used by benchmark artifacts and commands."""

    token: str
    size_bytes: int
    display_label: str


@dataclass(frozen=True)
class RmwDefinition:
    """Describe an RMW short name and its normalized ROS implementation name."""

    short_name: str
    implementation_name: str


@dataclass(frozen=True)
class BenchmarkFamily:
    """Describe a benchmark family and its supported RMW communication modes."""

    name: str
    topology: str
    process_mode: str
    communication_modes: Mapping[str, tuple[str, ...]]


PAYLOADS = MappingProxyType({
    '10b': PayloadDefinition('10b', 10, '10 B'),
    '100kb': PayloadDefinition('100kb', 100 * 1024, '100 KiB'),
    '1mb': PayloadDefinition('1mb', 1024 * 1024, '1 MiB'),
    '4mb': PayloadDefinition('4mb', 4 * 1024 * 1024, '4 MiB'),
})

RMW_IMPLEMENTATIONS = MappingProxyType({
    'fastrtps': RmwDefinition('fastrtps', 'rmw_fastrtps_cpp'),
    'cyclonedds': RmwDefinition('cyclonedds', 'rmw_cyclonedds_cpp'),
    'zenoh': RmwDefinition('zenoh', 'rmw_zenoh_cpp'),
})

BENCHMARK_FAMILIES = MappingProxyType({
    'pub-sub_single_process': BenchmarkFamily(
        name='pub-sub_single_process',
        topology='pub-sub',
        process_mode='single_process',
        communication_modes=MappingProxyType({
            'fastrtps': ('ipc_on', 'ipc_off', 'loaned'),
            'cyclonedds': ('ipc_off',),
            'zenoh': ('ipc_on', 'ipc_off'),
        }),
    ),
    'pub-sub_multi_process': BenchmarkFamily(
        name='pub-sub_multi_process',
        topology='pub-sub',
        process_mode='multi_process',
        communication_modes=MappingProxyType({
            'fastrtps': ('ipc_off', 'loaned'),
            'cyclonedds': ('ipc_off',),
            'zenoh': ('ipc_off',),
        }),
    ),
    'cli-srv_single_process': BenchmarkFamily(
        name='cli-srv_single_process',
        topology='service',
        process_mode='single_process',
        communication_modes=MappingProxyType({
            'fastrtps': ('ipc_on', 'ipc_off'),
            'cyclonedds': ('ipc_off',),
            'zenoh': ('ipc_on', 'ipc_off'),
        }),
    ),
    'cli-srv_multi_process': BenchmarkFamily(
        name='cli-srv_multi_process',
        topology='service',
        process_mode='multi_process',
        communication_modes=MappingProxyType({
            'fastrtps': ('ipc_off',),
            'cyclonedds': ('ipc_off',),
            'zenoh': ('ipc_off',),
        }),
    ),
})


def get_payload(token):
    """Return the canonical definition for a payload token."""
    normalized = str(token).lower()
    try:
        return PAYLOADS[normalized]
    except KeyError as exc:
        supported = ', '.join(PAYLOADS)
        raise BenchmarkLayoutError(
            f'unsupported payload {token} (supported: {supported})'
        ) from exc


def get_rmw(short_name):
    """Return the canonical definition for an RMW short name."""
    try:
        return RMW_IMPLEMENTATIONS[short_name]
    except KeyError as exc:
        supported = ', '.join(RMW_IMPLEMENTATIONS)
        raise BenchmarkLayoutError(
            f'unsupported RMW name {short_name} (supported: {supported})'
        ) from exc


def get_benchmark_family(name):
    """Return the canonical definition for a benchmark family."""
    try:
        return BENCHMARK_FAMILIES[name]
    except KeyError as exc:
        supported = ', '.join(BENCHMARK_FAMILIES)
        raise BenchmarkLayoutError(
            f'unsupported benchmark family {name} (supported: {supported})'
        ) from exc


def get_communication_modes(family_name, rmw_short_name):
    """Return the allowed communication modes for an RMW and family."""
    family = get_benchmark_family(family_name)
    get_rmw(rmw_short_name)
    return family.communication_modes[rmw_short_name]


def parse_rmw_directory(family_name, directory_name):
    """Validate and split an artifact RMW directory name."""
    if '_' not in directory_name:
        raise BenchmarkLayoutError(f'unsupported RMW directory {directory_name}')

    short_name, communication_mode = directory_name.split('_', 1)
    rmw = get_rmw(short_name)
    supported_modes = get_communication_modes(family_name, short_name)
    if communication_mode not in supported_modes:
        supported = ', '.join(supported_modes)
        raise BenchmarkLayoutError(
            f'unsupported communication mode {communication_mode} for {short_name} '
            f'in benchmark family {family_name} (supported: {supported})'
        )
    return rmw, communication_mode
