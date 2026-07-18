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
