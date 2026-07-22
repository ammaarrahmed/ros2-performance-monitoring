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

from dataclasses import dataclass
from pathlib import Path
import re
import warnings


BENCHMARK_ROOTS = ('benchmark',)
REQUIRED_FILES = ('metadata.txt', 'resources.txt', 'latency_all.txt', 'latency_total.txt')
SUPPORTED_PUBSUB_FAMILIES = ('pub-sub_single_process', 'pub-sub_multi_process')
SUPPORTED_SERVICE_FAMILIES = ('cli-srv_single_process', 'cli-srv_multi_process')
SUPPORTED_FAMILIES = SUPPORTED_PUBSUB_FAMILIES + SUPPORTED_SERVICE_FAMILIES
SUPPORTED_PAYLOADS = ('10b', '100kb', '1mb', '4mb')
PAYLOAD_RE = r'(?P<payload>\d+(?:b|kb|mb))'
PUBSUB_TOPOLOGY_RE = re.compile(
    rf'^pub_sub_\d+(?:\.\d+)?hz_{PAYLOAD_RE}$',
    re.IGNORECASE,
)
PUBSUB_MULTI_TOPOLOGY_RE = re.compile(rf'^{PAYLOAD_RE}$', re.IGNORECASE)
SERVICE_SINGLE_TOPOLOGY_RE = re.compile(rf'^cli_srv_{PAYLOAD_RE}$', re.IGNORECASE)
SERVICE_MULTI_TOPOLOGY_RE = re.compile(rf'^{PAYLOAD_RE}$', re.IGNORECASE)
RMW_RE = re.compile(r'^(cyclonedds|fastrtps|zenoh)_(ipc_on|ipc_off|loaned)$')


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkArtifact:
    directory: Path
    metadata: Path
    resources: Path
    latency_all: Path
    latency_total: Path


def discover_benchmark_artifacts(results_dir):
    results_dir = Path(results_dir).expanduser().resolve()
    if not results_dir.is_dir():
        raise ArtifactError(f'results directory does not exist: {results_dir}')

    roots = [results_dir / name for name in BENCHMARK_ROOTS if (results_dir / name).is_dir()]
    if not roots:
        names = ', '.join(BENCHMARK_ROOTS)
        raise ArtifactError(f'no benchmark artifact root found under {results_dir} ({names})')

    artifacts = []
    errors = []
    for root in roots:
        for distro in root.iterdir():
            if not distro.is_dir():
                continue
            for family_name in SUPPORTED_PUBSUB_FAMILIES:
                family = distro / family_name
                if not family.is_dir():
                    continue
                if family_name == 'pub-sub_multi_process':
                    leaves = family.glob('*/*/*')
                    nested = True
                else:
                    leaves = family.glob('pub_sub_*/*')
                    nested = False
                for leaf in leaves:
                    if leaf.is_dir():
                        topology_re = PUBSUB_MULTI_TOPOLOGY_RE if nested else PUBSUB_TOPOLOGY_RE
                        _collect_leaf(leaf, topology_re, artifacts, errors, nested)

            family = distro / 'cli-srv_single_process'
            if family.is_dir():
                for leaf in family.glob('cli_srv_*/*'):
                    if leaf.is_dir():
                        _collect_leaf(leaf, SERVICE_SINGLE_TOPOLOGY_RE, artifacts, errors)

            family = distro / 'cli-srv_multi_process'
            if family.is_dir():
                for leaf in family.glob('*/*/*'):
                    if leaf.is_dir():
                        _collect_leaf(
                            leaf,
                            SERVICE_MULTI_TOPOLOGY_RE,
                            artifacts,
                            errors,
                            True,
                        )

    if errors:
        raise ArtifactError('incomplete benchmark artifacts:\n' + '\n'.join(errors))
    if not artifacts:
        names = ', '.join(SUPPORTED_FAMILIES)
        raise ArtifactError(
            f'no supported pub/sub or service artifacts found under {results_dir} ({names})'
        )
    return tuple(sorted(artifacts, key=lambda item: str(item.directory)))


def _collect_leaf(leaf, topology_re, artifacts, errors, nested=False):
    topology_name = leaf.parent.parent.name if nested else leaf.parent.name
    rmw_name = leaf.parent.name if nested else leaf.name
    match = topology_re.match(topology_name)
    if not match:
        errors.append(f'{leaf}: malformed topology directory')
        return
    payload = match.group('payload').lower()
    if payload not in SUPPORTED_PAYLOADS:
        warnings.warn(f'{leaf}: skipping unsupported payload {payload}', stacklevel=2)
        return
    if not RMW_RE.match(rmw_name):
        errors.append(f'{leaf}: malformed RMW directory')
        return

    missing = [name for name in REQUIRED_FILES if not (leaf / name).is_file()]
    if missing:
        errors.append(f"{leaf}: missing {', '.join(missing)}")
        return

    artifacts.append(BenchmarkArtifact(
        directory=leaf,
        metadata=leaf / 'metadata.txt',
        resources=leaf / 'resources.txt',
        latency_all=leaf / 'latency_all.txt',
        latency_total=leaf / 'latency_total.txt',
    ))
