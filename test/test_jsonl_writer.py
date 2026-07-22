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


def test_invalid_record_does_not_overwrite_existing_output(tmp_path):
    output = tmp_path / 'metrics.jsonl'
    output.write_text('existing output\n')
    records = [Record('valid', 1.0), Record('invalid', float('nan'))]

    with pytest.raises(ValueError, match='non-finite metric value for invalid'):
        write_jsonl(records, output)

    assert output.read_text() == 'existing output\n'
