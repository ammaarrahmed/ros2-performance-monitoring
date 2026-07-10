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

from dataclasses import asdict
from dataclasses import dataclass


SCHEMA_VERSION = 3


@dataclass(frozen=True)
class MetricRecord:
    schema_version: int
    run_id: str
    timestamp: str
    benchmark_ref: str
    benchmark_commit: str
    client_library_ref: str
    client_library_commit: str
    client_library: str
    ros_distro: str
    rmw_implementation: str
    executor: str
    topology: str
    process_mode: str
    communication_mode: str
    payload_size: int
    frequency: float
    metric_name: str
    numeric_value: float
    unit: str
    aggregation: str
    source_file: str
    node_role: str = ''

    def to_dict(self):
        return asdict(self)
