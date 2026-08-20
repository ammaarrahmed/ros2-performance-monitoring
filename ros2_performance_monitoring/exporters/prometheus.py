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

from ros2_performance_monitoring.comparison import comparison_results
from ros2_performance_monitoring.comparison import run_display_name
from ros2_performance_monitoring.comparison import STATUS_LABELS
from ros2_performance_monitoring.comparison_report import EVIDENCE_STATUS_VALUES
from ros2_performance_monitoring.comparison_report import load_comparison_report
from ros2_performance_monitoring.model import client_library_version
from ros2_performance_monitoring.model import normalize_client_library_source
from ros2_performance_monitoring.model import normalize_platform
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import SCENARIO_FIELDS


# Maps stable Prometheus label names to normalized JSONL record fields.
PROMETHEUS_LABEL_FIELDS = (
    ('run_id', 'run_id'),
    ('benchmark_ref', 'benchmark_ref'),
    ('benchmark_commit', 'benchmark_commit'),
    ('client_library', 'client_library'),
    ('client_library_ref', 'client_library_ref'),
    ('client_library_commit', 'client_library_commit'),
    ('client_source', 'client_library_source'),
    ('platform', 'platform'),
    ('ros_distro', 'ros_distro'),
    ('rmw', 'rmw_implementation'),
    ('executor', 'executor'),
    ('comm', 'communication_mode'),
    ('topology', 'topology'),
    ('payload_bytes', 'payload_size'),
    ('process_mode', 'process_mode'),
    ('node_role', 'node_role'),
)

THRESHOLD_ONLY_METHOD = 'threshold-only-v1'

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
    'ros2_perf_comparison_status': {
        'help': 'ROS 2 performance comparison status by KPI category.',
        'type': 'gauge',
    },
    'ros2_perf_comparison_analysis': {
        'help': 'ROS 2 performance comparison analysis mode (0 threshold-only, 1 report).',
        'type': 'gauge',
    },
    'ros2_perf_comparison_evidence': {
        'help': 'ROS 2 performance statistical evidence values by KPI category.',
        'type': 'gauge',
    },
    'ros2_perf_comparison_scenario_status': {
        'help': 'ROS 2 performance statistical status by scenario and KPI category.',
        'type': 'gauge',
    },
    'ros2_perf_comparison_scenario_evidence': {
        'help': 'ROS 2 performance statistical evidence by scenario and KPI category.',
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


def records_to_prometheus(records, comparison_report=None):
    lines = []
    for name, metadata in METRIC_FAMILIES.items():
        lines.append(f'# HELP {name} {metadata["help"]}')
        lines.append(f'# TYPE {name} {metadata["type"]}')

    for labels, record in _unique_runs(records).items():
        info_labels = dict(labels)
        info_labels['timestamp'] = record.get('timestamp', '')
        info_labels['run_kind'] = record.get('run_kind', 'measured')
        info_labels['aggregation_method'] = record.get('aggregation_method', 'none')
        info_labels['repeat_count'] = str(record.get('repeat_count', 1))
        info_labels['run_display'] = run_display_name(record)
        lines.append(_sample('ros2_perf_run_info', info_labels, 1))

    for record in records:
        sample = _record_sample(record)
        if sample is not None:
            lines.append(sample)

    for labels, count in _resource_counts(records).items():
        resource_labels = dict(labels)
        lines.append(_sample('ros2_perf_resource_samples_total', resource_labels, count))

    if comparison_report is None:
        lines.extend(_legacy_comparison_samples(records))
    else:
        lines.extend(_report_comparison_samples(records, comparison_report))

    lines.append('')
    return '\n'.join(lines)


def load_export_data(input_path, comparison_report_path=None):
    """Load normalized records and an optional report validated against their bytes."""
    path = Path(input_path).expanduser().resolve()
    records = load_records(path)
    report = None
    if comparison_report_path is not None:
        report = load_comparison_report(comparison_report_path, path, records)
    return records, report


def _legacy_comparison_samples(records):
    lines = []
    exported_analysis = set()
    for result in comparison_results(records):
        labels = _comparison_labels(result)
        labels.update({
            'category': result.category,
            'method': THRESHOLD_ONLY_METHOD,
            'evidence_state': STATUS_LABELS[result.status],
        })
        lines.append(_sample('ros2_perf_comparison_status', labels, result.status))
        analysis_labels = _comparison_labels(result)
        analysis_key = tuple(sorted(analysis_labels.items()))
        if analysis_key not in exported_analysis:
            analysis_labels['method'] = THRESHOLD_ONLY_METHOD
            lines.append(_sample('ros2_perf_comparison_analysis', analysis_labels, 0))
            exported_analysis.add(analysis_key)
    return lines


def _comparison_labels(result):
    return {
        'baseline_run': result.baseline_run,
        'candidate_run': result.candidate_run,
        'baseline_distro': result.baseline_distro,
        'candidate_distro': result.candidate_distro,
        'client_library': result.client_library,
        'client_source': result.client_source,
        'platform': result.platform,
        'topology': result.topology,
    }


def _report_comparison_samples(records, validated):
    report = validated.report
    reference = _record_for_run(records, validated.reference_run)
    candidate = _record_for_run(records, validated.candidate_run)
    base_scope = {
        'baseline_run': validated.reference_run,
        'candidate_run': validated.candidate_run,
        'baseline_distro': reference.get('ros_distro', 'unknown'),
        'candidate_distro': candidate.get('ros_distro', 'unknown'),
        'client_library': reference.get('client_library', 'unknown'),
        'client_source': normalize_client_library_source(
            reference.get('client_library_source'),
            reference.get('client_library_ref'),
            reference.get('client_library_commit'),
        ),
        'platform': normalize_platform(reference.get('platform')),
    }
    analysis = report['analysis']
    summaries = []
    if len(report['topologies']) != 1:
        summaries.append(('all', {
            'overall': report['overall'],
            'categories': report['categories'],
        }))
    summaries.extend(sorted(report['topologies'].items()))

    lines = []
    for topology, summary in summaries:
        scope = {**base_scope, 'topology': topology}
        analysis_labels = {
            **scope,
            'experiment_id': report['experiment_id'],
            'method': METHOD,
            'confidence_level': _format_number(analysis['confidence_level']),
            'repeat_count': str(analysis['measured_trial_pairs']),
        }
        lines.append(_sample('ros2_perf_comparison_analysis', analysis_labels, 1))
        evidence_items = [
            ('overall', summary['overall']),
            *summary['categories'].items(),
        ]
        for category, evidence in evidence_items:
            labels = _report_evidence_labels(scope, report, category, evidence)
            lines.append(_sample(
                'ros2_perf_comparison_status',
                labels,
                EVIDENCE_STATUS_VALUES[evidence['status']],
            ))
            lines.extend(_evidence_samples(scope, report, category, evidence))

    for scenario in report['scenarios']:
        scenario_labels = {
            **base_scope,
            **_scenario_labels(scenario['identity']),
        }
        for category, evidence in scenario['categories'].items():
            labels = _report_evidence_labels(
                scenario_labels,
                report,
                category,
                evidence,
            )
            lines.append(_sample(
                'ros2_perf_comparison_scenario_status',
                labels,
                EVIDENCE_STATUS_VALUES[evidence['status']],
            ))
            lines.extend(_evidence_samples(
                scenario_labels,
                report,
                category,
                evidence,
                scenario=True,
            ))
    return lines


def _report_evidence_labels(scope, report, category, evidence):
    labels = {
        **scope,
        'category': category,
        'experiment_id': report['experiment_id'],
        'method': report['analysis']['method'],
        'evidence_state': evidence['status'],
    }
    responsible = evidence.get('responsible_scenario')
    for field in SCENARIO_FIELDS:
        labels[f'responsible_{_scenario_label_name(field)}'] = (
            responsible.get(field, '') if isinstance(responsible, dict) else ''
        )
    metric = evidence.get('responsible_metric')
    labels['responsible_metric'] = (
        metric.get('metric_name', '') if isinstance(metric, dict) else ''
    )
    labels['responsible_aggregation'] = (
        metric.get('aggregation', '') if isinstance(metric, dict) else ''
    )
    return labels


def _evidence_samples(scope, report, category, evidence, scenario=False):
    values = {
        'confidence_level': report['analysis']['confidence_level'],
        'repeat_count': report['analysis']['measured_trial_pairs'],
        'point_estimate': evidence.get('point_estimate'),
    }
    interval = evidence.get('confidence_interval')
    if isinstance(interval, dict):
        values['interval_lower'] = interval['lower']
        values['interval_upper'] = interval['upper']
    threshold = evidence.get('practical_threshold')
    unit = threshold.get('unit', '') if isinstance(threshold, dict) else ''
    if isinstance(threshold, dict):
        if type(threshold.get('possible')) in (int, float):
            values['possible_threshold'] = threshold['possible']
        if type(threshold.get('regression')) in (int, float):
            values['regression_threshold'] = threshold['regression']

    family = (
        'ros2_perf_comparison_scenario_evidence'
        if scenario else 'ros2_perf_comparison_evidence'
    )
    lines = []
    for statistic, value in values.items():
        if value is None:
            continue
        labels = {
            **scope,
            'category': category,
            'experiment_id': report['experiment_id'],
            'method': report['analysis']['method'],
            'evidence_state': evidence['status'],
            'statistic': statistic,
            'unit': unit,
        }
        lines.append(_sample(family, labels, value))
    return lines


def _scenario_labels(identity):
    return {
        _scenario_label_name(field): identity[field]
        for field in SCENARIO_FIELDS
    }


def _scenario_label_name(field):
    return {
        'payload_size': 'payload_bytes',
        'rmw_implementation': 'rmw',
        'communication_mode': 'comm',
    }.get(field, field)


def _record_for_run(records, run_id):
    return next(record for record in records if record.get('run_id') == run_id)


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
        elif label == 'client_source':
            value = normalize_client_library_source(
                value,
                record.get('client_library_ref'),
                record.get('client_library_commit'),
            )
        elif label == 'platform':
            value = normalize_platform(value)
        if not value:
            value = 'unknown'
        labels[label] = str(value)
    labels['client_library_version'] = client_library_version(
        labels['client_source'],
        labels['client_library_commit'],
    )
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


def serve_metrics(
    input_path,
    port=9108,
    host='0.0.0.0',
    comparison_report_path=None,
):
    server = create_metrics_server(
        input_path,
        port=port,
        host=host,
        comparison_report_path=comparison_report_path,
    )
    path = Path(input_path).expanduser().resolve()
    print(f'Serving Prometheus metrics from {path}')
    print(f'Exporter: http://localhost:{port}/metrics')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Stopping Prometheus exporter')
    finally:
        server.server_close()


def create_metrics_server(
    input_path,
    port=9108,
    host='0.0.0.0',
    comparison_report_path=None,
):
    """Create a validated metrics and health server without starting its loop."""
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f'normalized metrics file does not exist: {path}')
    if not path.is_file():
        raise ValueError(f'normalized metrics path is not a file: {path}')
    load_export_data(path, comparison_report_path)

    class MetricsHandler(BaseHTTPRequestHandler):

        def do_GET(self):
            if self.path in ('/healthz', '/healthz/'):
                body = b'ok\n'
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path not in ('/metrics', '/metrics/'):
                self.send_response(404)
                self.end_headers()
                return

            try:
                records, report = load_export_data(path, comparison_report_path)
                body = records_to_prometheus(records, report).encode()
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

    return HTTPServer((host, port), MetricsHandler)
