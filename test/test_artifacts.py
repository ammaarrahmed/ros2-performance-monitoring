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
        'benchmark',
        'rolling',
        'pub-sub_multi_process',
        '100kb',
        'cyclonedds_ipc_off/sub_100kb',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf
    assert artifacts[0].resources == leaf / 'resources.txt'


def test_discovers_single_process_1mb_pubsub_artifact(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_single_process',
        'pub_sub_200hz_1mb',
        'fastrtps_ipc_off',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf


def test_discovers_single_process_service_artifact(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'cli-srv_single_process',
        'cli_srv_10b',
        'fastrtps_ipc_on',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf


def test_discovers_multi_process_service_artifact(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'cli-srv_multi_process',
        '100kb',
        'cyclonedds_ipc_off/cli_100kb',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf


def test_discovers_single_process_4mb_service_artifact(tmp_path):
    leaf = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'cli-srv_single_process',
        'cli_srv_4mb',
        'fastrtps_ipc_off',
    )

    artifacts = discover_benchmark_artifacts(tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].directory == leaf


def test_discovers_supported_families_in_sorted_order(tmp_path):
    first = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_multi_process',
        '100kb',
        'fastrtps_ipc_off/sub_100kb',
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


def test_mixed_payloads_warns_and_discovers_supported_artifacts(tmp_path):
    supported = _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_multi_process',
        '100kb',
        'cyclonedds_ipc_off/sub_100kb',
    )
    _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'pub-sub_multi_process',
        '5mb',
        'cyclonedds_ipc_off/sub_5mb',
    )

    with pytest.warns(UserWarning, match='skipping unsupported payload 5mb'):
        artifacts = discover_benchmark_artifacts(tmp_path)

    assert tuple(artifact.directory for artifact in artifacts) == (supported,)


def test_unsupported_service_payload_raises_clear_error(tmp_path):
    _artifact_leaf(
        tmp_path,
        'benchmark',
        'lyrical',
        'cli-srv_single_process',
        'cli_srv_5mb',
        'fastrtps_ipc_on',
    )

    with pytest.warns(UserWarning, match='skipping unsupported payload 5mb'):
        with pytest.raises(ArtifactError, match='no supported pub/sub or service artifacts'):
            discover_benchmark_artifacts(tmp_path)


def _artifact_leaf(tmp_path, root, distro, family, topology, rmw, missing=()):
    leaf = tmp_path / root / distro / family / topology / rmw
    leaf.mkdir(parents=True)
    for name in REQUIRED_FILES:
        if name not in missing:
            (leaf / name).write_text('\n')
    return leaf
