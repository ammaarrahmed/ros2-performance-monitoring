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

from .exporters.prometheus import serve_metrics


def main():
    """Run the environment-configured container exporter."""
    input_path = os.environ.get(
        'ROS2_PERFORMANCE_EXPORTER_INPUT',
        '/data/dashboard-data.jsonl',
    )
    report_path = os.environ.get('ROS2_PERFORMANCE_EXPORTER_REPORT') or None
    port_text = os.environ.get('ROS2_PERFORMANCE_EXPORTER_PORT', '9108')
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SystemExit(
            f'ROS2_PERFORMANCE_EXPORTER_PORT must be an integer, got {port_text!r}'
        ) from exc
    serve_metrics(
        input_path,
        port=port,
        comparison_report_path=report_path,
    )
