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

from ros2_performance_monitoring.artifacts import ArtifactError
from ros2_performance_monitoring.artifacts import discover_benchmark_artifacts


REQUIRED_FILES = ('metadata.txt', 'resources.txt', 'latency_all.txt', 'latency_total.txt')


def test_discovers_single_process_10b_fastdds_artifact(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_single_process',
        'pub_sub_10hz_10b',
        'fastrtps_ipc_on',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf
    assert artifacts[0].metadata == leaf / 'metadata.txt'


def test_discovers_multi_process_100kb_cyclonedds_artifact(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benhcmark',
        'rolling',
        'pub-sub_multi_process',
        'pub_sub_10hz_100kb',
        'cyclonedds_ipc_off',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf
    assert artifacts[0].resources == leaf / 'resources.txt'


def test_discovers_supported_families_in_sorted_order(tmp_path):
    first = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_multi_process',
        'pub_sub_100hz_100kb',
        'fastrtps_ipc_off',
    )
    second = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_single_process',
        'pub_sub_10hz_10b',
        'cyclonedds_ipc_on',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert tuple(artifact.directory for artifact in artifacts) == tuple(sorted((first, second)))


def test_missing_required_artifact_file_raises_clear_error(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_single_process',
        'pub_sub_10hz_10b',
        'fastrtps_ipc_on',
        missing=('latency_total.txt',),
    )

    with pytest.raises(ArtifactError) as exc_info:
        discover_benchmark_artifacts(tmp_path)

    assert str(leaf) in str(exc_info.value)
    assert 'missing latency_total.txt' in str(exc_info.value)


def test_unsupported_payload_raises_clear_error(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_multi_process',
        'pub_sub_10hz_1mb',
        'cyclonedds_ipc_off',
    )

    with pytest.raises(ArtifactError) as exc_info:
        discover_benchmark_artifacts(tmp_path)

    assert str(leaf) in str(exc_info.value)
    assert 'malformed topology directory' in str(exc_info.value)


def _artifact_leaf(tmp_path, root, distro, family, topology, rmw, missing=()):
    leaf = tmp_path / root / distro / family / topology / rmw
    leaf.mkdir(parents=True)
    for name in REQUIRED_FILES:
        if name not in missing:
            (leaf / name).write_text('\n')
    return leaf
