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
from pathlib import Path


def write_jsonl(records, output_path):
    lines = []
    for record in records:
        item = record.to_dict()
        value = item.get('numeric_value')
        if not math.isfinite(value):
            raise ValueError(f'non-finite metric value for {item.get("metric_name")}')
        lines.append(json.dumps(item, sort_keys=True, separators=(',', ':')))

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w') as stream:
        for line in lines:
            stream.write(line + '\n')
    return len(lines)
