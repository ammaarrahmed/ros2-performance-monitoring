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

import hashlib
import json

import pytest
from ros2_performance_monitoring.dataset import build_dataset
from ros2_performance_monitoring.dataset import DatasetError
from ros2_performance_monitoring.dataset import manifest_path_for


def test_combines_runs_deterministically_and_records_input_lineage(tmp_path):
    run_a = tmp_path / 'run-a.jsonl'
    run_b = tmp_path / 'run-b.jsonl'
    output = tmp_path / 'dashboard-data.jsonl'
    _write_records(run_a, [_record('run-a', 1.0)])
    _write_records(run_b, [_record('run-b', 2.0)])

    result = build_dataset([run_b, run_a], output)
    first_bytes = output.read_bytes()
    first_manifest = manifest_path_for(output).read_bytes()
    build_dataset([run_a, run_b], output)

    records = _read_records(output)
    manifest = json.loads(first_manifest)
    assert result.record_count == 2
    assert result.run_count == 2
    assert result.aggregate_count == 0
    assert [record['run_id'] for record in records] == ['run-a', 'run-b']
    assert [record['numeric_value'] for record in records] == [1.0, 2.0]
    assert output.read_bytes() == first_bytes
    assert manifest_path_for(output).read_bytes() == first_manifest
    assert manifest['aggregation_method'] == 'none'
    assert manifest['aggregates'] == []
    assert [item['path'] for item in manifest['inputs']] == sorted((str(run_a), str(run_b)))
    assert manifest['inputs'][0]['sha256'] == hashlib.sha256(run_a.read_bytes()).hexdigest()


def test_reads_supported_v4_records_without_changing_their_shape(tmp_path):
    input_path = tmp_path / 'legacy.jsonl'
    output = tmp_path / 'dataset.jsonl'
    record = _record('legacy-run', 4.0)
    record['schema_version'] = 4
    record.pop('run_kind')
    record.pop('aggregation_method')
    record.pop('repeat_count')
    _write_records(input_path, [record])

    build_dataset([input_path], output)

    assert _read_records(output) == [record]


def test_excludes_runs_from_output_and_manifest_lineage(tmp_path):
    input_path = tmp_path / 'runs.jsonl'
    output = tmp_path / 'dataset.jsonl'
    _write_records(input_path, [
        _record('warm-up', 100.0),
        _record('run-a', 1.0),
    ])

    build_dataset([input_path], output, exclude_runs=['warm-up'])

    assert {record['run_id'] for record in _read_records(output)} == {'run-a'}
    manifest_text = manifest_path_for(output).read_text()
    manifest = json.loads(manifest_text)
    assert manifest['excluded_run_ids'] == ['warm-up']
    assert manifest['inputs'][0]['run_ids'] == ['run-a']


@pytest.mark.parametrize(
    ('contents', 'message'),
    (
        ('not-json\n', 'invalid JSON'),
        ('[]\n', 'must be a JSON object'),
        (json.dumps({'schema_version': 5}) + '\n', 'missing field'),
    ),
)
def test_invalid_lines_report_input_path_and_line(tmp_path, contents, message):
    input_path = tmp_path / 'invalid.jsonl'
    output = tmp_path / 'dataset.jsonl'
    input_path.write_text('\n' + contents)

    with pytest.raises(DatasetError) as exc_info:
        build_dataset([input_path], output)

    assert f'{input_path}:2' in str(exc_info.value)
    assert message in str(exc_info.value)


def test_non_finite_values_report_input_path_and_line(tmp_path):
    input_path = tmp_path / 'non-finite.jsonl'
    output = tmp_path / 'dataset.jsonl'
    record = _record('run-a', float('nan'))
    _write_records(input_path, [record])

    with pytest.raises(DatasetError) as exc_info:
        build_dataset([input_path], output)

    assert f'{input_path}:1' in str(exc_info.value)
    assert 'non-finite metric value' in str(exc_info.value)


def test_rejects_unsupported_schema_version(tmp_path):
    input_path = tmp_path / 'future.jsonl'
    output = tmp_path / 'dataset.jsonl'
    record = _record('run-a', 1.0)
    record['schema_version'] = 99
    _write_records(input_path, [record])

    with pytest.raises(DatasetError, match='unsupported schema_version 99'):
        build_dataset([input_path], output)


def test_rejects_conflicting_run_provenance_without_replacing_output(tmp_path):
    input_path = tmp_path / 'conflict.jsonl'
    output = tmp_path / 'dataset.jsonl'
    output.write_text('existing output\n')
    first = _record('run-a', 1.0, metric_name='subscription_latency')
    second = _record('run-a', 2.0, metric_name='resource_cpu')
    second['benchmark_commit'] = 'different-commit'
    _write_records(input_path, [first, second])

    with pytest.raises(DatasetError, match='conflicting run-level provenance'):
        build_dataset([input_path], output)

    assert output.read_text() == 'existing output\n'


def test_rejects_run_id_spread_across_input_files(tmp_path):
    first = tmp_path / 'first.jsonl'
    second = tmp_path / 'second.jsonl'
    output = tmp_path / 'dataset.jsonl'
    _write_records(first, [_record('same-run', 1.0)])
    _write_records(second, [_record('same-run', 2.0)])

    with pytest.raises(DatasetError, match='also appears in'):
        build_dataset([first, second], output)


def test_rejects_duplicate_metric_identity(tmp_path):
    input_path = tmp_path / 'duplicate.jsonl'
    output = tmp_path / 'dataset.jsonl'
    _write_records(input_path, [
        _record('run-a', 1.0),
        _record('run-a', 2.0),
    ])

    with pytest.raises(DatasetError, match='duplicate metric identity'):
        build_dataset([input_path], output)


@pytest.mark.parametrize('collision', ('output', 'manifest'))
def test_rejects_output_input_collisions_without_replacing_input(tmp_path, collision):
    output = tmp_path / 'dataset.jsonl'
    input_path = output if collision == 'output' else manifest_path_for(output)
    original = json.dumps(_record('run-a', 1.0)) + '\n'
    input_path.write_text(original)

    with pytest.raises(DatasetError, match='path is also an input'):
        build_dataset([input_path], output)

    assert input_path.read_text() == original


def _record(run_id, value, metric_name='subscription_latency'):
    return {
        'schema_version': 5,
        'run_id': run_id,
        'timestamp': '2026-08-15T00:00:00Z',
        'benchmark_ref': 'rolling',
        'benchmark_commit': 'benchmark-commit',
        'client_library_ref': 'client-ref',
        'client_library_commit': 'client-commit',
        'client_library': 'rclcpp',
        'client_library_source': 'build',
        'platform': 'x86_64',
        'ros_distro': 'rolling',
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': 'pub-sub',
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': 10,
        'frequency': 10.0,
        'metric_name': metric_name,
        'numeric_value': value,
        'unit': 'us',
        'aggregation': 'mean',
        'source_file': 'latency_all.txt',
        'node_role': '',
        'run_kind': 'measured',
        'aggregation_method': 'none',
        'repeat_count': 1,
    }


def _write_records(path, records):
    path.write_text(''.join(json.dumps(record) + '\n' for record in records))


def _read_records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]
