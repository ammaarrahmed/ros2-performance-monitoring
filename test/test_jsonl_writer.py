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

import os
import stat

import pytest
from ros2_performance_monitoring.writers import jsonl as jsonl_writer
from ros2_performance_monitoring.writers.jsonl import write_jsonl


class Record:

    def __init__(self, metric_name, numeric_value):
        self.metric_name = metric_name
        self.numeric_value = numeric_value

    def to_dict(self):
        return {
            'metric_name': self.metric_name,
            'numeric_value': self.numeric_value,
        }


class UnserializableRecord:

    def to_dict(self):
        return {
            'metric_name': 'invalid',
            'numeric_value': 1.0,
            'value': object(),
        }


class StreamWrapper:

    def __init__(self, stream, failure_point=None, partial_writes=False):
        self._stream = stream
        self._failure_point = failure_point
        self._partial_writes = partial_writes

    @property
    def closed(self):
        return self._stream.closed

    def write(self, contents):
        if self._failure_point == 'write':
            raise OSError('simulated write failure')
        if self._partial_writes and len(contents) > 1:
            contents = contents[:max(1, len(contents) // 2)]
        return self._stream.write(contents)

    def flush(self):
        if self._failure_point == 'flush':
            raise OSError('simulated flush failure')
        return self._stream.flush()

    def fileno(self):
        return self._stream.fileno()

    def close(self):
        result = self._stream.close()
        if self._failure_point == 'close':
            raise OSError('simulated close failure')
        return result


def test_writes_same_jsonl_bytes_and_returns_record_count(tmp_path):
    output = tmp_path / 'nested' / 'metrics.jsonl'
    records = [Record('latency', 1.25), Record('throughput', 2.5)]

    count = write_jsonl(records, output)

    assert count == 2
    assert output.read_bytes() == (
        b'{"metric_name":"latency","numeric_value":1.25}\n'
        b'{"metric_name":"throughput","numeric_value":2.5}\n'
    )


def test_replaces_existing_output_and_preserves_its_mode(tmp_path):
    output = tmp_path / 'metrics.jsonl'
    output.write_text('existing output\n')
    output.chmod(0o640)

    write_jsonl([Record('latency', 1.25)], output)

    assert output.read_text() == '{"metric_name":"latency","numeric_value":1.25}\n'
    assert stat.S_IMODE(output.stat().st_mode) == 0o640


def test_new_output_uses_permissions_from_current_umask(tmp_path):
    output = tmp_path / 'metrics.jsonl'
    previous_umask = os.umask(0o027)
    try:
        write_jsonl([Record('latency', 1.25)], output)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(output.stat().st_mode) == 0o640


def test_retries_short_writes_until_the_payload_is_complete(tmp_path, monkeypatch):
    output = tmp_path / 'metrics.jsonl'
    _wrap_temporary_stream(monkeypatch, partial_writes=True)

    count = write_jsonl([Record('latency', 1.25)], output)

    assert count == 1
    assert output.read_text() == '{"metric_name":"latency","numeric_value":1.25}\n'


def test_replaces_output_only_after_temporary_file_is_closed(tmp_path, monkeypatch):
    output = tmp_path / 'metrics.jsonl'
    streams = _wrap_temporary_stream(monkeypatch)
    real_replace = os.replace
    replacements = []

    def checked_replace(source, destination):
        assert streams[0].closed
        assert source.parent == output.parent
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(jsonl_writer.os, 'replace', checked_replace)

    write_jsonl([Record('latency', 1.25)], output)

    assert len(replacements) == 1
    assert replacements[0][1] == output


@pytest.mark.parametrize('failure_point', ('write', 'flush', 'fsync', 'close', 'replace'))
def test_io_failure_preserves_existing_output_and_removes_temporary_file(
    tmp_path,
    monkeypatch,
    failure_point,
):
    output = tmp_path / 'metrics.jsonl'
    output.write_text('existing output\n')
    stream_failure = failure_point if failure_point in ('write', 'flush', 'close') else None
    streams = _wrap_temporary_stream(monkeypatch, failure_point=stream_failure)

    if failure_point == 'fsync':
        def fail_fsync(_file_descriptor):
            raise OSError('simulated fsync failure')

        monkeypatch.setattr(jsonl_writer.os, 'fsync', fail_fsync)
    elif failure_point == 'replace':
        def fail_replace(_source, _destination):
            assert streams[0].closed
            raise OSError('simulated replace failure')

        monkeypatch.setattr(jsonl_writer.os, 'replace', fail_replace)

    with pytest.raises(OSError, match=f'simulated {failure_point} failure'):
        write_jsonl([Record('latency', 1.25)], output)

    assert output.read_text() == 'existing output\n'
    assert list(tmp_path.iterdir()) == [output]


def test_invalid_record_does_not_overwrite_existing_output(tmp_path):
    output = tmp_path / 'metrics.jsonl'
    output.write_text('existing output\n')
    records = [Record('valid', 1.0), Record('invalid', float('nan'))]

    with pytest.raises(ValueError, match='non-finite metric value for invalid'):
        write_jsonl(records, output)

    assert output.read_text() == 'existing output\n'


def test_serialization_failure_does_not_overwrite_existing_output(tmp_path):
    output = tmp_path / 'metrics.jsonl'
    output.write_text('existing output\n')

    with pytest.raises(TypeError, match='not JSON serializable'):
        write_jsonl([UnserializableRecord()], output)

    assert output.read_text() == 'existing output\n'
    assert list(tmp_path.iterdir()) == [output]


def _wrap_temporary_stream(monkeypatch, failure_point=None, partial_writes=False):
    real_open_temporary_file = jsonl_writer._open_temporary_file
    streams = []

    def open_wrapped_temporary_file(output_path):
        temporary_path, stream = real_open_temporary_file(output_path)
        wrapped_stream = StreamWrapper(
            stream,
            failure_point=failure_point,
            partial_writes=partial_writes,
        )
        streams.append(wrapped_stream)
        return temporary_path, wrapped_stream

    monkeypatch.setattr(jsonl_writer, '_open_temporary_file', open_wrapped_temporary_file)
    return streams
