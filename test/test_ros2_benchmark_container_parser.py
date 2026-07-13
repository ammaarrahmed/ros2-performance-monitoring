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

from ros2_performance_monitoring.parsers.ros2_benchmark_container import latest_run_metadata


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
