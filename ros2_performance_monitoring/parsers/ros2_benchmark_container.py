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

from ros2_performance_monitoring import benchmark_layout
from ros2_performance_monitoring.model import MetricRecord
from ros2_performance_monitoring.model import normalize_client_library_source
from ros2_performance_monitoring.model import normalize_platform
from ros2_performance_monitoring.model import SCHEMA_VERSION


csv.field_size_limit(sys.maxsize)
PAYLOAD_RE = r'(?P<size>\d+(?:b|kb|mb))'
PUBSUB_TOPOLOGY_RE = re.compile(
    rf'^pub_sub_(?P<freq>\d+(?:\.\d+)?)hz_{PAYLOAD_RE}$',
    re.IGNORECASE,
)
PUBSUB_MULTI_TOPOLOGY_RE = re.compile(rf'^{PAYLOAD_RE}$', re.IGNORECASE)
SERVICE_SINGLE_TOPOLOGY_RE = re.compile(rf'^cli_srv_{PAYLOAD_RE}$', re.IGNORECASE)
SERVICE_MULTI_TOPOLOGY_RE = re.compile(rf'^{PAYLOAD_RE}$', re.IGNORECASE)

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
SERVICE_LATENCY_METRICS = {
    'mean_us': 'mean',
    'sd_us': 'sd',
    'min_us': 'min',
    'max_us': 'max',
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
        'client_library_source': run['client_library_source'],
        'platform': run['platform'],
        'executor': metadata.get('system_executor') or run['executor'],
        **attrs,
    }

    records = []
    if base['topology'] == 'service':
        records.extend(_parse_service_latency_all(artifact.latency_all, base))
    else:
        latency_records = _parse_latency_all(artifact.latency_all, base)
        records.extend(latency_records)
        if latency_records:
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
            'using the newest metadata for artifacts from its ROS distribution',
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
    multi_process_family = benchmark_layout.BENCHMARK_FAMILIES.get(
        leaf.parent.parent.parent.name
    )
    if (
        (
            multi_process_family is not None
            and multi_process_family.process_mode == 'multi_process'
        )
        or (multi_process_family is None and _node_role(leaf.name))
    ):
        shape = leaf.parent.parent.name
        rmw_directory = leaf.parent.name
        family_name = leaf.parent.parent.parent.name
        distro = leaf.parent.parent.parent.parent.name
        node_role = _node_role(leaf.name)
    else:
        shape = leaf.parent.name
        rmw_directory = leaf.name
        family_name = leaf.parent.parent.name
        distro = leaf.parent.parent.parent.name
        node_role = ''
    try:
        family = benchmark_layout.get_benchmark_family(family_name)
        rmw, communication_mode = benchmark_layout.parse_rmw_directory(
            family_name,
            rmw_directory,
        )
    except benchmark_layout.BenchmarkLayoutError as exc:
        raise ValueError(f'{directory}: {exc}') from exc

    match = _match_topology(family.topology, family.process_mode, shape)
    if not match:
        raise ValueError(f'{directory}: unsupported topology directory {shape}')

    try:
        payload = benchmark_layout.get_payload(match.group('size'))
    except benchmark_layout.BenchmarkLayoutError as exc:
        raise ValueError(f'{directory}: {exc}') from exc
    frequency = float(match.group('freq')) if 'freq' in match.groupdict() else 0.0
    return {
        'ros_distro': distro,
        'rmw_implementation': rmw.implementation_name,
        'topology': family.topology,
        'process_mode': family.process_mode,
        'communication_mode': communication_mode,
        'payload_size': payload.size_bytes,
        'frequency': frequency,
        'node_role': node_role,
    }


def _node_role(name):
    if name.startswith('pub_'):
        return 'publisher'
    if name.startswith('sub_'):
        return 'subscription'
    if name.startswith('cli_'):
        return 'client'
    if name.startswith('srv_'):
        return 'service'
    return ''


def _match_topology(topology, process_mode, shape):
    if topology == 'pub-sub':
        if process_mode == 'multi_process':
            return PUBSUB_MULTI_TOPOLOGY_RE.match(shape)
        return PUBSUB_TOPOLOGY_RE.match(shape)
    if process_mode == 'single_process':
        return SERVICE_SINGLE_TOPOLOGY_RE.match(shape)
    return SERVICE_MULTI_TOPOLOGY_RE.match(shape)


def _parse_latency_all(path, base):
    section = _latency_section(
        Path(path).read_text().splitlines(),
        'Subscriptions stats:',
        path,
        required=base.get('node_role') != 'publisher',
    )
    if not section:
        return []
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


def _parse_service_latency_all(path, base):
    lines = Path(path).read_text().splitlines()
    records = []
    for heading, metric_name in (
        ('Clients stats:', 'service_client_latency'),
        ('Services stats:', 'service_server_latency'),
    ):
        section = _latency_section(lines, heading, path, required=False)
        if not section:
            continue
        if len(section) < 2:
            raise ValueError(f'{path}: empty {heading.removesuffix(":").lower()} section')
        for row in csv.DictReader(section):
            for column, aggregation in SERVICE_LATENCY_METRICS.items():
                value = row.get(column)
                if value not in (None, ''):
                    records.append(
                        _record(base, metric_name, float(value), 'us', aggregation, path)
                    )
            latencies = _latency_list(row.get('all_lat', ''))
            for name, value in (('p50', 0.50), ('p95', 0.95), ('p99', 0.99)):
                percentile = _percentile(latencies, value)
                if percentile is not None:
                    records.append(_record(base, metric_name, percentile, 'us', name, path))
    if not records:
        raise ValueError(f'{path}: missing Clients stats or Services stats section')
    return records


def _latency_section(lines, heading, path, required=True):
    try:
        start = lines.index(heading) + 1
    except ValueError as exc:
        if not required:
            return []
        raise ValueError(f'{path}: missing {heading.removesuffix(":")} section') from exc

    section = []
    for line in lines[start:]:
        if not line.strip() or line.endswith('stats:'):
            break
        section.append(line)
    return section


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
    client_library_ref = client.get('ref', 'unknown')
    client_library_commit = client.get('resolved_commit_hash', 'unknown')
    return {
        'run_id': metadata.get('run_id') or metadata.get('_file_run_id') or 'unknown',
        'timestamp': timestamp,
        'benchmark_ref': benchmark.get('ref', 'unknown'),
        'benchmark_commit': benchmark.get('resolved_commit_hash', 'unknown'),
        'client_library_ref': client_library_ref,
        'client_library_commit': client_library_commit,
        'client_library': client_library,
        'client_library_source': normalize_client_library_source(
            client.get('source'),
            client_library_ref,
            client_library_commit,
        ),
        'platform': normalize_platform(host.get('architecture')),
        'executor': config.get('executor', ''),
    }


def _client_library(config):
    suite = config.get('suite', '')
    if 'rclpy' in suite:
        return 'rclpy'
    return 'rclcpp'
