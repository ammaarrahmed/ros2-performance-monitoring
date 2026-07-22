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

import csv
import json
from pathlib import Path
import re
import sys

from ros2_performance_monitoring.model import MetricRecord
from ros2_performance_monitoring.model import SCHEMA_VERSION


SIZE_UNITS = {'b': 1, 'kb': 1024, 'mb': 1024 * 1024}
SUPPORTED_FAMILIES = {
    'pub-sub_single_process': ('pub-sub', 'single_process'),
    'pub-sub_multi_process': ('pub-sub', 'multi_process'),
}
RMW_NAMES = {
    'cyclonedds': 'rmw_cyclonedds_cpp',
    'fastrtps': 'rmw_fastrtps_cpp',
    'zenoh': 'rmw_zenoh_cpp',
}
COMMUNICATION_MODES = ('ipc_on', 'ipc_off', 'loaned')
TOPOLOGY_RE = re.compile(
    r'^pub_sub_(?P<freq>\d+(?:\.\d+)?)hz_(?P<size>10b|100kb)$',
    re.IGNORECASE,
)

SUBSCRIPTION_METRICS = {
    'received_msgs': ('subscription_messages_received', 'count', 'total'),
    'late_msgs': ('subscription_messages_late', 'count', 'total'),
    'too_late_msgs': ('subscription_messages_too_late', 'count', 'total'),
    'lost_msgs': ('subscription_messages_lost', 'count', 'total'),
    'mean_us': ('subscription_latency', 'us', 'mean'),
    'sd_us': ('subscription_latency', 'us', 'sd'),
    'min_us': ('subscription_latency', 'us', 'min'),
    'max_us': ('subscription_latency', 'us', 'max'),
    'freq_hz': ('subscription_frequency', 'Hz', 'observed'),
    'throughput_Kb_per_sec': ('subscription_throughput', 'Kb/s', 'observed'),
}
TOTAL_METRICS = {
    'received_msgs': ('total_messages_received', 'count', 'total'),
    'mean_us': ('total_latency', 'us', 'mean'),
    'late_msgs': ('total_messages_late', 'count', 'total'),
    'late_perc': ('total_messages_late', '%', 'percent'),
    'too_late_msgs': ('total_messages_too_late', 'count', 'total'),
    'too_late_perc': ('total_messages_too_late', '%', 'percent'),
    'lost_msgs': ('total_messages_lost', 'count', 'total'),
    'lost_perc': ('total_messages_lost', '%', 'percent'),
}
RESOURCE_METRICS = {
    'cpu_perc': ('resource_cpu', '%'),
    'latency_us': ('resource_latency', 'us'),
    'arena_KB': ('resource_memory_arena', 'KB'),
    'in_use_KB': ('resource_memory_in_use', 'KB'),
    'mmap_KB': ('resource_memory_mmap', 'KB'),
    'rss_KB': ('resource_memory_rss', 'KB'),
    'vsz_KB': ('resource_memory_vsz', 'KB'),
}


def parse_artifact(artifact, run_metadata):
    metadata = parse_metadata_txt(artifact.metadata)
    attrs = infer_topology(artifact.directory)
    run = _run_context(run_metadata)
    base = {
        'schema_version': SCHEMA_VERSION,
        'run_id': run['run_id'],
        'timestamp': run['timestamp'],
        'benchmark_ref': run['benchmark_ref'],
        'benchmark_commit': run['benchmark_commit'],
        'client_library_ref': run['client_library_ref'],
        'client_library_commit': run['client_library_commit'],
        'client_library': run['client_library'],
        'executor': metadata.get('system_executor') or run['executor'],
        **attrs,
    }

    records = []
    records.extend(_parse_latency_all(artifact.latency_all, base))
    records.extend(_parse_latency_total(artifact.latency_total, base))
    records.extend(_parse_resources(artifact.resources, base))
    return records


def latest_run_metadata(results_dir):
    results_dir = Path(results_dir).expanduser().resolve()
    if not results_dir.exists():
        raise FileNotFoundError(f'results directory does not exist: {results_dir}')
    files = sorted(results_dir.glob('metadata_*.json'))
    if not files:
        raise FileNotFoundError(f'no run metadata found in {results_dir}')
    if len(files) > 1:
        print(
            f'Warning: found {len(files)} run metadata files in {results_dir}; '
            'using the newest metadata for all discovered artifacts',
            file=sys.stderr,
        )

    metadata_file = max(files, key=lambda path: (path.stat().st_mtime, path.name))
    with metadata_file.open() as stream:
        data = json.load(stream)
    data.setdefault('_file_run_id', metadata_file.stem.removeprefix('metadata_'))
    return data


def parse_metadata_txt(path):
    items = {}
    for line_no, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        if ':' not in line:
            raise ValueError(f'{path}: malformed metadata line {line_no}')
        key, value = line.split(':', 1)
        items[key.strip()] = value.strip()
    return items


def infer_topology(directory):
    leaf = Path(directory)
    shape = leaf.parent.name
    family_name = leaf.parent.parent.name
    distro = leaf.parent.parent.parent.name
    if family_name not in SUPPORTED_FAMILIES:
        raise ValueError(f'{directory}: unsupported benchmark family {family_name}')

    match = TOPOLOGY_RE.match(shape)
    if not match:
        raise ValueError(f'{directory}: unsupported topology directory {shape}')

    if '_' not in leaf.name:
        raise ValueError(f'{directory}: unsupported RMW directory {leaf.name}')

    rmw, communication_mode = leaf.name.split('_', 1)
    if rmw not in RMW_NAMES:
        raise ValueError(f'{directory}: unsupported RMW directory {leaf.name}')
    if communication_mode not in COMMUNICATION_MODES:
        raise ValueError(f'{directory}: unsupported communication mode {communication_mode}')

    topology, process_mode = SUPPORTED_FAMILIES[family_name]
    size_match = re.match(r'(?P<count>\d+)(?P<unit>b|kb)$', match.group('size'), re.IGNORECASE)
    size = int(size_match.group('count')) * SIZE_UNITS[size_match.group('unit').lower()]
    return {
        'ros_distro': distro,
        'rmw_implementation': RMW_NAMES[rmw],
        'topology': topology,
        'process_mode': process_mode,
        'communication_mode': communication_mode,
        'payload_size': size,
        'frequency': float(match.group('freq')),
    }


def _parse_latency_all(path, base):
    lines = Path(path).read_text().splitlines()
    try:
        start = lines.index('Subscriptions stats:') + 1
    except ValueError as exc:
        raise ValueError(f'{path}: missing Subscriptions stats section') from exc

    section = []
    for line in lines[start:]:
        if not line.strip() or line.endswith('stats:'):
            break
        section.append(line)
    if len(section) < 2:
        raise ValueError(f'{path}: empty subscription stats section')

    records = []
    for row in csv.DictReader(section):
        records.extend(_records_from_row(row, SUBSCRIPTION_METRICS, path, base))
        latencies = _latency_list(row.get('all_lat', ''))
        for name, value in (('p50', 0.50), ('p95', 0.95), ('p99', 0.99)):
            percentile = _percentile(latencies, value)
            if percentile is not None:
                records.append(_record(base, 'subscription_latency', percentile, 'us', name, path))
    return records


def _parse_latency_total(path, base):
    with Path(path).open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f'{path}: empty latency total file')
    return _records_from_row(rows[0], TOTAL_METRICS, path, base)


def _parse_resources(path, base):
    values = {name: [] for name in RESOURCE_METRICS}
    with Path(path).open(newline='') as stream:
        for row in csv.DictReader(stream):
            for name in values:
                if row.get(name) not in (None, ''):
                    values[name].append(float(row[name]))

    records = []
    for column, samples in values.items():
        if not samples:
            continue
        metric, unit = RESOURCE_METRICS[column]
        records.append(_record(base, metric, sum(samples) / len(samples), unit, 'mean', path))
        records.append(_record(base, metric, max(samples), unit, 'max', path))
    return records


def _records_from_row(row, mapping, path, base):
    records = []
    for column, (metric, unit, aggregation) in mapping.items():
        value = row.get(column)
        if value not in (None, ''):
            records.append(_record(base, metric, float(value), unit, aggregation, path))
    return records


def _record(base, metric_name, value, unit, aggregation, source_file):
    return MetricRecord(
        metric_name=metric_name,
        numeric_value=float(value),
        unit=unit,
        aggregation=aggregation,
        source_file=Path(source_file).name,
        **base,
    )


def _latency_list(raw):
    raw = raw.strip().strip('[]')
    if not raw:
        return []
    return [float(item.strip()) for item in raw.split(';') if item.strip()]


def _percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * quantile)
    return ordered[index]


def _run_context(metadata):
    config = metadata.get('run_configuration', {})
    host = metadata.get('host_environment', {})
    benchmark = metadata.get('benchmark_repo') or metadata.get('target_repo', {})
    client = metadata.get('client_library_under_test', {})
    timestamp = metadata.get('timestamp') or host.get('timestamp') or host.get('timestamp ') or ''
    client_library = config.get('client_library') or client.get('name') or _client_library(config)
    return {
        'run_id': metadata.get('run_id') or metadata.get('_file_run_id') or 'unknown',
        'timestamp': timestamp,
        'benchmark_ref': benchmark.get('ref', 'unknown'),
        'benchmark_commit': benchmark.get('resolved_commit_hash', 'unknown'),
        'client_library_ref': client.get('ref', 'unknown'),
        'client_library_commit': client.get('resolved_commit_hash', 'unknown'),
        'client_library': client_library,
        'executor': config.get('executor', ''),
    }


def _client_library(config):
    suite = config.get('suite', '')
    if 'rclpy' in suite:
        return 'rclpy'
    return 'rclcpp'
