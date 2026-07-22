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

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import math
from pathlib import Path


# Maps stable Prometheus label names to normalized JSONL record fields.
PROMETHEUS_LABEL_FIELDS = (
    ('run_id', 'run_id'),
    ('benchmark_ref', 'benchmark_ref'),
    ('benchmark_commit', 'benchmark_commit'),
    ('client_library', 'client_library'),
    ('client_library_ref', 'client_library_ref'),
    ('client_library_commit', 'client_library_commit'),
    ('ros_distro', 'ros_distro'),
    ('rmw', 'rmw_implementation'),
    ('executor', 'executor'),
    ('comm', 'communication_mode'),
    ('topology', 'topology'),
    ('payload_bytes', 'payload_size'),
    ('process_mode', 'process_mode'),
)

METRIC_FAMILIES = {
    'ros2_perf_latency_us': {
        'help': 'ROS 2 performance latency measurements in microseconds.',
        'type': 'gauge',
    },
    'ros2_perf_cpu_percent': {
        'help': 'ROS 2 performance CPU measurements as percentages.',
        'type': 'gauge',
    },
    'ros2_perf_memory_megabytes': {
        'help': 'ROS 2 performance memory measurements in megabytes.',
        'type': 'gauge',
    },
    'ros2_perf_messages_total': {
        'help': 'ROS 2 performance message counts.',
        'type': 'gauge',
    },
    'ros2_perf_messages_percent': {
        'help': 'ROS 2 performance message percentages.',
        'type': 'gauge',
    },
    'ros2_perf_throughput_kb_per_second': {
        'help': 'ROS 2 performance throughput measurements in kilobytes per second.',
        'type': 'gauge',
    },
    'ros2_perf_resource_samples_total': {
        'help': 'ROS 2 performance normalized resource sample count.',
        'type': 'gauge',
    },
    'ros2_perf_run_info': {
        'help': 'ROS 2 performance run metadata.',
        'type': 'gauge',
    },
}


def load_records(input_path):
    path = Path(input_path).expanduser().resolve()
    records = []
    with path.open() as stream:
        for line in stream:
            if line.strip():
                records.append(json.loads(line))
    return records


def records_to_prometheus(records):
    lines = []
    for name, metadata in METRIC_FAMILIES.items():
        lines.append(f'# HELP {name} {metadata["help"]}')
        lines.append(f'# TYPE {name} {metadata["type"]}')

    for labels, record in _unique_runs(records).items():
        info_labels = dict(labels)
        info_labels['timestamp'] = record.get('timestamp', '')
        lines.append(_sample('ros2_perf_run_info', info_labels, 1))

    for record in records:
        sample = _record_sample(record)
        if sample is not None:
            lines.append(sample)

    for labels, count in _resource_counts(records).items():
        resource_labels = dict(labels)
        lines.append(_sample('ros2_perf_resource_samples_total', resource_labels, count))

    lines.append('')
    return '\n'.join(lines)


def _record_sample(record):
    family = _family_for_record(record)
    if family is None:
        return None

    labels = _base_labels(record)
    labels['metric'] = record.get('metric_name', '')
    labels['aggregation'] = record.get('aggregation', '')
    return _sample(family, labels, _prometheus_value(record))


def _family_for_record(record):
    metric_name = record.get('metric_name', '')
    unit = record.get('unit', '')
    if unit == 'us' or metric_name.endswith('_latency'):
        return 'ros2_perf_latency_us'
    if metric_name == 'resource_cpu':
        return 'ros2_perf_cpu_percent'
    if metric_name.startswith('resource_memory_'):
        return 'ros2_perf_memory_megabytes'
    if unit == 'count' and '_messages_' in metric_name:
        return 'ros2_perf_messages_total'
    if unit == '%' and '_messages_' in metric_name:
        return 'ros2_perf_messages_percent'
    if metric_name == 'subscription_throughput':
        return 'ros2_perf_throughput_kb_per_second'
    return None


def _prometheus_value(record):
    value = float(record.get('numeric_value', 0))
    if not math.isfinite(value):
        raise ValueError(f'non-finite metric value for {record.get("metric_name")}')
    if record.get('metric_name', '').startswith('resource_memory_') and record.get('unit') == 'KB':
        return value / 1024.0
    return value


def _unique_runs(records):
    runs = {}
    for record in records:
        labels = tuple(_base_labels(record).items())
        runs.setdefault(labels, record)
    return runs


def _resource_counts(records):
    counts = {}
    for record in records:
        if record.get('metric_name', '').startswith('resource_'):
            labels = tuple(_base_labels(record).items())
            counts[labels] = counts.get(labels, 0) + 1
    return counts


def _base_labels(record):
    labels = {}
    for label, field in PROMETHEUS_LABEL_FIELDS:
        value = record.get(field, '')
        if label == 'benchmark_ref' and not value:
            value = record.get('target_ref', '')
        if not value:
            value = 'unknown'
        labels[label] = str(value)
    return labels


def _sample(name, labels, value):
    return f'{name}{_label_block(labels)} {_format_number(value)}'


def _label_block(labels):
    if not labels:
        return ''
    items = [f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items())]
    return '{' + ','.join(items) + '}'


def _escape_label(value):
    return str(value).replace('\\', r'\\').replace('\n', r'\n').replace('"', r'\"')


def _format_number(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return repr(value)


def serve_metrics(input_path, port=9108, host='0.0.0.0'):
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f'normalized metrics file does not exist: {path}')
    if not path.is_file():
        raise ValueError(f'normalized metrics path is not a file: {path}')

    class MetricsHandler(BaseHTTPRequestHandler):

        def do_GET(self):
            if self.path not in ('/metrics', '/metrics/'):
                self.send_response(404)
                self.end_headers()
                return

            try:
                body = records_to_prometheus(load_records(path)).encode()
                self.send_response(200)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                body = str(exc).encode()
                self.send_response(500)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            return

    server = HTTPServer((host, port), MetricsHandler)
    print(f'Serving Prometheus metrics from {path}')
    print(f'Exporter: http://localhost:{port}/metrics')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Stopping Prometheus exporter')
    finally:
        server.server_close()
