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

from ros2_performance_monitoring import benchmark_layout


BENCHMARK_ROOTS = ('benchmark',)
REQUIRED_FILES = ('metadata.txt', 'resources.txt', 'latency_all.txt', 'latency_total.txt')
PAYLOAD_RE = r'(?P<payload>\d+(?:b|kb|mb))'
PUBSUB_TOPOLOGY_RE = re.compile(
    rf'^pub_sub_\d+(?:\.\d+)?hz_{PAYLOAD_RE}$',
    re.IGNORECASE,
)
PUBSUB_MULTI_TOPOLOGY_RE = re.compile(rf'^{PAYLOAD_RE}$', re.IGNORECASE)
SERVICE_SINGLE_TOPOLOGY_RE = re.compile(rf'^cli_srv_{PAYLOAD_RE}$', re.IGNORECASE)
SERVICE_MULTI_TOPOLOGY_RE = re.compile(rf'^{PAYLOAD_RE}$', re.IGNORECASE)


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkArtifact:
    directory: Path
    metadata: Path
    resources: Path
    latency_all: Path
    latency_total: Path


def discover_benchmark_artifacts(results_dir, ros_distro=None):
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
        distro_dirs = (root / ros_distro,) if ros_distro else root.iterdir()
        for distro in distro_dirs:
            if not distro.is_dir():
                continue
            for family_name, definition in benchmark_layout.BENCHMARK_FAMILIES.items():
                family_dir = distro / family_name
                if not family_dir.is_dir():
                    continue

                nested = definition.process_mode == 'multi_process'
                if definition.topology == 'pub-sub':
                    if nested:
                        leaves = family_dir.glob('*/*/*')
                        topology_re = PUBSUB_MULTI_TOPOLOGY_RE
                    else:
                        leaves = family_dir.glob('pub_sub_*/*')
                        topology_re = PUBSUB_TOPOLOGY_RE
                else:
                    if nested:
                        leaves = family_dir.glob('*/*/*')
                        topology_re = SERVICE_MULTI_TOPOLOGY_RE
                    else:
                        leaves = family_dir.glob('cli_srv_*/*')
                        topology_re = SERVICE_SINGLE_TOPOLOGY_RE

                for leaf in leaves:
                    if leaf.is_dir():
                        _collect_leaf(
                            leaf,
                            family_name,
                            topology_re,
                            artifacts,
                            errors,
                            nested,
                        )

    if errors:
        raise ArtifactError('incomplete benchmark artifacts:\n' + '\n'.join(errors))
    if not artifacts:
        names = ', '.join(benchmark_layout.BENCHMARK_FAMILIES)
        raise ArtifactError(
            f'no supported pub/sub or service artifacts found under {results_dir} ({names})'
        )
    return tuple(sorted(artifacts, key=lambda item: str(item.directory)))


def _collect_leaf(leaf, family_name, topology_re, artifacts, errors, nested=False):
    topology_name = leaf.parent.parent.name if nested else leaf.parent.name
    rmw_name = leaf.parent.name if nested else leaf.name
    match = topology_re.match(topology_name)
    if not match:
        errors.append(f'{leaf}: malformed topology directory')
        return
    payload = match.group('payload').lower()
    try:
        benchmark_layout.get_payload(payload)
    except benchmark_layout.BenchmarkLayoutError as exc:
        warnings.warn(f'{leaf}: skipping {exc}', stacklevel=2)
        return
    try:
        benchmark_layout.parse_rmw_directory(family_name, rmw_name)
    except benchmark_layout.BenchmarkLayoutError as exc:
        errors.append(f'{leaf}: {exc}')
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
