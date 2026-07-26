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

from dataclasses import asdict
from dataclasses import dataclass


SCHEMA_VERSION = 4

PLATFORM_ALIASES = {
    'aarch64': 'arm64',
    'amd64': 'x86_64',
    'arm64': 'arm64',
    'armv7': 'armv7',
    'armv7l': 'armv7',
    'i386': 'x86',
    'i686': 'x86',
    'x86_64': 'x86_64',
}
PACKAGED_SOURCES = {
    'binary',
    'package',
    'packaged',
    'ros_distro_package',
    'ros_package',
}
BUILD_SOURCES = {
    'build',
    'built_from_source',
    'source',
    'source_build',
}


@dataclass(frozen=True)
class MetricRecord:
    schema_version: int
    run_id: str
    timestamp: str
    benchmark_ref: str
    benchmark_commit: str
    client_library_ref: str
    client_library_commit: str
    client_library: str
    client_library_source: str
    platform: str
    ros_distro: str
    rmw_implementation: str
    executor: str
    topology: str
    process_mode: str
    communication_mode: str
    payload_size: int
    frequency: float
    metric_name: str
    numeric_value: float
    unit: str
    aggregation: str
    source_file: str
    node_role: str = ''

    def to_dict(self):
        return asdict(self)


def normalize_platform(value):
    """Return a stable architecture label for dashboard filtering."""
    normalized = str(value or '').strip().lower()
    return PLATFORM_ALIASES.get(normalized, normalized or 'unknown')


def normalize_client_library_source(value, ref='', commit=''):
    """Return whether a client library came from a build or a package."""
    normalized = str(value or '').strip().lower().replace('-', '_')
    if normalized in BUILD_SOURCES:
        return 'build'
    if normalized in PACKAGED_SOURCES:
        return 'packaged'

    commit = str(commit or '').strip().lower()
    if commit and commit != 'unknown':
        return 'build'
    if 'package' in str(ref or '').lower():
        return 'packaged'
    return 'unknown'


def client_library_version(source, commit):
    """Return a commit for builds and a stable label for packaged clients."""
    if source == 'packaged':
        return 'packaged'
    return str(commit or '').strip() or 'unknown'
