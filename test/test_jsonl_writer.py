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
