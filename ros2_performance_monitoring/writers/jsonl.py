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
import math
import os
from pathlib import Path
import stat
import uuid


def _open_temporary_file(output_path):
    while True:
        temporary_path = output_path.parent / (
            f'.ros2-performance-monitoring-{uuid.uuid4().hex}.tmp'
        )
        try:
            return temporary_path, temporary_path.open('x', encoding='utf-8')
        except FileExistsError:
            continue


def _write_all(stream, contents):
    offset = 0
    while offset < len(contents):
        written = stream.write(contents[offset:])
        if written is None or written <= 0:
            raise OSError('failed to write complete JSONL output')
        offset += written


def _write_chunks_atomically(chunks, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        output_mode = stat.S_IMODE(output_path.stat().st_mode)
    except FileNotFoundError:
        output_mode = None

    temporary_path, stream = _open_temporary_file(output_path)
    count = 0
    try:
        if output_mode is not None:
            temporary_path.chmod(output_mode)
        for chunk in chunks:
            _write_all(stream, chunk)
            count += 1
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        os.replace(temporary_path, output_path)
    except BaseException:
        if not stream.closed:
            try:
                stream.close()
            except BaseException:
                pass
        try:
            temporary_path.unlink()
        except BaseException:
            pass
        raise
    return count


def _serialize_records(records):
    for record in records:
        item = record.to_dict()
        value = item.get('numeric_value')
        if not math.isfinite(value):
            raise ValueError(f'non-finite metric value for {item.get("metric_name")}')
        yield json.dumps(item, sort_keys=True, separators=(',', ':')) + '\n'


def write_jsonl(records, output_path):
    output_path = Path(output_path).expanduser().resolve()
    return _write_chunks_atomically(_serialize_records(records), output_path)
