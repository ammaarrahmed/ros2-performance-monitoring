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

import json
import os

import pytest

from ros2_performance_monitoring.artifacts import BenchmarkArtifact
from ros2_performance_monitoring.parsers.ros2_benchmark_container import infer_topology
from ros2_performance_monitoring.parsers.ros2_benchmark_container import latest_run_metadata
from ros2_performance_monitoring.parsers.ros2_benchmark_container import parse_artifact


def test_latest_run_metadata_warns_and_selects_newest_file(tmp_path, capsys):
    older = tmp_path / 'metadata_older.json'
    newer = tmp_path / 'metadata_newer.json'
    older.write_text(json.dumps({'name': 'older'}))
    newer.write_text(json.dumps({'name': 'newer'}))
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    metadata = latest_run_metadata(tmp_path)

    captured = capsys.readouterr()
    assert metadata['name'] == 'newer'
    assert 'Warning: found 2 run metadata files' in captured.err
    assert 'using the newest metadata for all discovered artifacts' in captured.err


def test_latest_run_metadata_reports_missing_results_directory(tmp_path):
    results_dir = tmp_path / 'missing-results'

    with pytest.raises(FileNotFoundError, match='results directory does not exist'):
        latest_run_metadata(results_dir)


def test_parse_artifact_uses_portable_source_filenames(tmp_path):
    directory = (
        tmp_path / 'benchmark' / 'lyrical' / 'pub-sub_single_process' /
        'pub_sub_10hz_10b' / 'fastrtps_ipc_on'
    )
    directory.mkdir(parents=True)
    metadata = directory / 'metadata.txt'
    resources = directory / 'resources.txt'
    latency_all = directory / 'latency_all.txt'
    latency_total = directory / 'latency_total.txt'
    metadata.write_text('system_executor: EventsExecutor\n')
    resources.write_text('cpu_perc,rss_KB\n10,100\n')
    latency_all.write_text(
        'Subscriptions stats:\n'
        'received_msgs,mean_us,all_lat\n'
        '1,5,[5]\n'
    )
    latency_total.write_text('received_msgs,mean_us\n1,5\n')
    artifact = BenchmarkArtifact(
        directory=directory,
        metadata=metadata,
        resources=resources,
        latency_all=latency_all,
        latency_total=latency_total,
    )

    records = parse_artifact(artifact, {'run_id': 'test-run'})

    assert {record.source_file for record in records} == {
        'latency_all.txt',
        'latency_total.txt',
        'resources.txt',
    }


@pytest.mark.parametrize(
    ('family', 'shape', 'rmw_directory', 'expected'),
    (
        (
            'pub-sub_single_process',
            'pub_sub_10hz_10b',
            'fastrtps_ipc_on',
            {
                'ros_distro': 'lyrical',
                'rmw_implementation': 'rmw_fastrtps_cpp',
                'topology': 'pub-sub',
                'process_mode': 'single_process',
                'communication_mode': 'ipc_on',
                'payload_size': 10,
                'frequency': 10.0,
            },
        ),
        (
            'pub-sub_multi_process',
            'pub_sub_10hz_100kb',
            'cyclonedds_ipc_off',
            {
                'ros_distro': 'lyrical',
                'rmw_implementation': 'rmw_cyclonedds_cpp',
                'topology': 'pub-sub',
                'process_mode': 'multi_process',
                'communication_mode': 'ipc_off',
                'payload_size': 102400,
                'frequency': 10.0,
            },
        ),
        (
            'pub-sub_single_process',
            'pub_sub_100hz_100kb',
            'fastrtps_loaned',
            {
                'ros_distro': 'lyrical',
                'rmw_implementation': 'rmw_fastrtps_cpp',
                'topology': 'pub-sub',
                'process_mode': 'single_process',
                'communication_mode': 'loaned',
                'payload_size': 102400,
                'frequency': 100.0,
            },
        ),
    ),
)
def test_parse_artifact_normalizes_reduced_pubsub_matrix_labels(
    tmp_path,
    family,
    shape,
    rmw_directory,
    expected,
):
    leaf = _artifact_leaf(tmp_path, family, shape, rmw_directory)

    records = parse_artifact(_artifact(leaf), _run_metadata())

    assert records
    for record in records:
        for key, value in expected.items():
            assert getattr(record, key) == value


def test_infer_topology_rejects_unsupported_layout_with_context(tmp_path):
    leaf = (
        tmp_path
        / 'benchmark'
        / 'lyrical'
        / 'pub-sub_service'
        / 'pub_sub_10hz_10b'
        / 'fastrtps_ipc_on'
    )

    with pytest.raises(ValueError) as exc_info:
        infer_topology(leaf)

    assert str(leaf) in str(exc_info.value)
    assert 'unsupported benchmark family pub-sub_service' in str(exc_info.value)


def _artifact_leaf(tmp_path, family, shape, rmw_directory):
    leaf = tmp_path / 'benchmark' / 'lyrical' / family / shape / rmw_directory
    leaf.mkdir(parents=True)
    (leaf / 'metadata.txt').write_text('system_executor: EventsExecutor\n')
    (leaf / 'latency_all.txt').write_text(
        'Subscriptions stats:\n'
        'received_msgs,late_msgs,too_late_msgs,lost_msgs,mean_us,sd_us,'
        'min_us,max_us,freq_hz,throughput_Kb_per_sec,all_lat\n'
        '10,1,0,0,2.0,0.5,1.0,3.0,10.0,20.0,[1; 2; 3]\n'
    )
    (leaf / 'latency_total.txt').write_text(
        'received_msgs,mean_us,late_msgs,late_perc,too_late_msgs,'
        'too_late_perc,lost_msgs,lost_perc\n'
        '10,2.0,1,10.0,0,0.0,0,0.0\n'
    )
    (leaf / 'resources.txt').write_text(
        'cpu_perc,latency_us,arena_KB,in_use_KB,mmap_KB,rss_KB,vsz_KB\n'
        '10.0,1.0,100.0,50.0,10.0,2048.0,4096.0\n'
    )
    return leaf


def _artifact(leaf):
    return BenchmarkArtifact(
        directory=leaf,
        metadata=leaf / 'metadata.txt',
        resources=leaf / 'resources.txt',
        latency_all=leaf / 'latency_all.txt',
        latency_total=leaf / 'latency_total.txt',
    )


def _run_metadata():
    return {
        'run_id': 'run-a',
        'timestamp': '2026-07-05T00:00:00Z',
        'benchmark_repo': {
            'ref': 'benchmark-branch',
            'resolved_commit_hash': 'def456',
        },
        'client_library_under_test': {
            'name': 'rclcpp',
            'ref': 'client-branch',
            'resolved_commit_hash': 'abc123',
        },
        'run_configuration': {},
    }
