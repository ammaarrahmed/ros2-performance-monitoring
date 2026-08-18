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

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from ros2_performance_monitoring.comparison import CATEGORIES
from ros2_performance_monitoring.model import normalize_client_library_source
from ros2_performance_monitoring.model import normalize_platform
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE
from ros2_performance_monitoring.statistical_comparison import INCOMPLETE_RESULTS
from ros2_performance_monitoring.statistical_comparison import INSUFFICIENT_EVIDENCE
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import NO_REGRESSION
from ros2_performance_monitoring.statistical_comparison import NOT_APPLICABLE
from ros2_performance_monitoring.statistical_comparison import POSSIBLE_REGRESSION
from ros2_performance_monitoring.statistical_comparison import REGRESSION
from ros2_performance_monitoring.statistical_comparison import REPORT_SCHEMA_VERSION
from ros2_performance_monitoring.statistical_comparison import SCENARIO_FIELDS


EVIDENCE_STATUS_VALUES = {
    NO_REGRESSION: 0,
    POSSIBLE_REGRESSION: 1,
    REGRESSION: 2,
    INCOMPLETE_RESULTS: 3,
    CANNOT_COMPARE: 4,
    NOT_APPLICABLE: 5,
    INSUFFICIENT_EVIDENCE: 6,
}


class ComparisonReportError(ValueError):
    """Report a malformed, unsupported, stale, or unrelated comparison report."""


@dataclass(frozen=True)
class ValidatedComparisonReport:
    """Pair a validated report with the dataset runs used by Grafana."""

    report: dict
    reference_run: str
    candidate_run: str


def load_comparison_report(report_path, dataset_path, records):
    """Load and validate one report against the exact normalized dataset bytes."""
    path = Path(report_path).expanduser().resolve()
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ComparisonReportError(f'comparison report does not exist: {path}') from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ComparisonReportError(f'malformed comparison report: {path}') from exc

    dataset = Path(dataset_path).expanduser().resolve()
    try:
        checksum = hashlib.sha256(dataset.read_bytes()).hexdigest()
    except FileNotFoundError as exc:
        raise ComparisonReportError(f'normalized metrics file does not exist: {dataset}') from exc
    except IsADirectoryError as exc:
        raise ComparisonReportError(f'normalized metrics path is not a file: {dataset}') from exc
    return validate_comparison_report(report, records, checksum)


def validate_comparison_report(report, records, dataset_checksum):
    """Validate report structure, dataset binding, targets, and scenario coverage."""
    if not isinstance(report, dict):
        raise ComparisonReportError('comparison report must be a JSON object')
    version = report.get('schema_version')
    if version != REPORT_SCHEMA_VERSION:
        raise ComparisonReportError(
            f'unsupported comparison report schema version: {version!r}; '
            f'expected {REPORT_SCHEMA_VERSION}'
        )
    required = {
        'schema_version',
        'experiment_id',
        'dataset',
        'targets',
        'analysis',
        'overall',
        'categories',
        'scenarios',
    }
    if set(report) != required:
        raise ComparisonReportError('comparison report has invalid top-level fields')

    experiment_id = report['experiment_id']
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ComparisonReportError('comparison report experiment ID is missing')
    dataset = report['dataset']
    if not isinstance(dataset, dict) or set(dataset) != {'sha256', 'experiment_id'}:
        raise ComparisonReportError('comparison report dataset binding is malformed')
    if dataset.get('experiment_id') != experiment_id:
        raise ComparisonReportError('comparison report experiment identity is inconsistent')
    checksum = dataset.get('sha256')
    if not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise ComparisonReportError('comparison report dataset checksum is malformed')
    if checksum != dataset_checksum:
        raise ComparisonReportError('comparison report dataset checksum does not match input')

    analysis = _validate_analysis(report['analysis'])
    targets = _validate_targets(report['targets'])
    _validate_evidence(report['overall'], 'overall')
    categories = report['categories']
    if not isinstance(categories, dict) or tuple(categories) != CATEGORIES:
        raise ComparisonReportError('comparison report category coverage is invalid')
    for category, evidence in categories.items():
        _validate_evidence(evidence, f'category {category}')

    scenario_identities = _validate_scenarios(report['scenarios'])
    run_groups = _run_groups(records)
    selected_runs = {
        role: _select_target_run(
            role,
            target,
            run_groups,
            analysis['measured_trial_pairs'],
        )
        for role, target in targets.items()
    }
    if selected_runs['reference'] == selected_runs['candidate']:
        raise ComparisonReportError('comparison report targets resolve to the same dataset run')

    for role, run_id in selected_runs.items():
        coverage = {
            tuple(record.get(field, '') for field in SCENARIO_FIELDS)
            for record in run_groups[run_id]
        }
        if coverage != scenario_identities:
            raise ComparisonReportError(
                f'comparison report scenario coverage does not match {role} target'
            )
    return ValidatedComparisonReport(
        report=report,
        reference_run=selected_runs['reference'],
        candidate_run=selected_runs['candidate'],
    )


def _validate_analysis(analysis):
    fields = {
        'method',
        'confidence_level',
        'seed',
        'bootstrap_repeats',
        'minimum_measured_trials',
        'measured_trial_pairs',
        'pairing',
        'point_estimator',
    }
    if not isinstance(analysis, dict) or set(analysis) != fields:
        raise ComparisonReportError('comparison report analysis settings are malformed')
    if analysis.get('method') != METHOD:
        raise ComparisonReportError(
            f'unsupported comparison method: {analysis.get("method")!r}'
        )
    confidence = analysis.get('confidence_level')
    if type(confidence) not in (int, float) or not 0.0 < confidence < 1.0:
        raise ComparisonReportError('comparison report confidence level is invalid')
    integer_fields = (
        'seed',
        'bootstrap_repeats',
        'minimum_measured_trials',
        'measured_trial_pairs',
    )
    if any(type(analysis.get(field)) is not int for field in integer_fields):
        raise ComparisonReportError('comparison report repeat settings are invalid')
    if (
        analysis['bootstrap_repeats'] < 1
        or analysis['minimum_measured_trials'] < 1
        or analysis['measured_trial_pairs'] < 1
    ):
        raise ComparisonReportError('comparison report repeat settings are invalid')
    return analysis


def _validate_targets(targets):
    if not isinstance(targets, dict) or set(targets) != {'reference', 'candidate'}:
        raise ComparisonReportError('comparison report targets are malformed')
    for role, target in targets.items():
        if not isinstance(target, dict) or set(target) != {'label', 'target_key', 'identity'}:
            raise ComparisonReportError(f'comparison report {role} target is malformed')
        if not isinstance(target['label'], str) or not target['label']:
            raise ComparisonReportError(f'comparison report {role} target label is missing')
        if not isinstance(target['target_key'], str) or not target['target_key']:
            raise ComparisonReportError(f'comparison report {role} target key is missing')
        identity = target['identity']
        client = identity.get('client_library') if isinstance(identity, dict) else None
        if not isinstance(client, dict):
            raise ComparisonReportError(
                f'comparison report {role} target identity is malformed'
            )
        required_identity = {'schema_version', 'ros_distro', 'architecture'}
        required_client = {'name', 'source', 'requested_ref', 'resolved_commit'}
        if not required_identity <= set(identity) or not required_client <= set(client):
            raise ComparisonReportError(
                f'comparison report {role} target identity is malformed'
            )
    if targets['reference']['target_key'] == targets['candidate']['target_key']:
        raise ComparisonReportError('comparison report targets must be different')
    return targets


def _validate_evidence(evidence, context):
    if not isinstance(evidence, dict):
        raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    if evidence.get('status') not in EVIDENCE_STATUS_VALUES:
        raise ComparisonReportError(f'comparison report {context} status is unsupported')
    for key in ('point_estimate', 'confidence_interval'):
        if key not in evidence:
            raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    point = evidence['point_estimate']
    if point is not None and not _finite_number(point):
        raise ComparisonReportError(f'comparison report {context} point estimate is invalid')
    interval = evidence['confidence_interval']
    if interval is not None:
        if not isinstance(interval, dict) or set(interval) != {'lower', 'upper'}:
            raise ComparisonReportError(f'comparison report {context} interval is malformed')
        lower = interval['lower']
        upper = interval['upper']
        if not _finite_number(lower) or not _finite_number(upper) or lower > upper:
            raise ComparisonReportError(f'comparison report {context} interval is invalid')


def _validate_scenarios(scenarios):
    if not isinstance(scenarios, list):
        raise ComparisonReportError('comparison report scenarios are malformed')
    identities = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {'identity', 'categories'}:
            raise ComparisonReportError('comparison report scenario is malformed')
        identity = scenario['identity']
        if not isinstance(identity, dict) or tuple(identity) != SCENARIO_FIELDS:
            raise ComparisonReportError('comparison report scenario identity is malformed')
        identity_key = tuple(identity[field] for field in SCENARIO_FIELDS)
        if identity_key in identities:
            raise ComparisonReportError('comparison report contains duplicate scenarios')
        identities.add(identity_key)
        categories = scenario['categories']
        if not isinstance(categories, dict) or not set(categories) <= set(CATEGORIES):
            raise ComparisonReportError('comparison report scenario categories are malformed')
        for category, evidence in categories.items():
            _validate_evidence(evidence, f'scenario {category}')
    if not identities:
        raise ComparisonReportError('comparison report contains no scenario coverage')
    return identities


def _run_groups(records):
    groups = {}
    for record in records:
        if not isinstance(record, dict):
            raise ComparisonReportError('normalized dataset contains a non-object record')
        run_id = record.get('run_id')
        if not isinstance(run_id, str) or not run_id:
            raise ComparisonReportError('normalized dataset contains an invalid run ID')
        groups.setdefault(run_id, []).append(record)
    if not groups:
        raise ComparisonReportError('normalized dataset contains no records')
    return groups


def _select_target_run(role, target, run_groups, repeat_count):
    matching = {
        run_id: records
        for run_id, records in run_groups.items()
        if all(_record_matches_target(record, target) for record in records)
    }
    aggregates = [
        run_id for run_id, records in matching.items()
        if all(record.get('run_kind', 'measured') == 'aggregate' for record in records)
        and all(record.get('aggregation_method') == 'median' for record in records)
        and all(record.get('repeat_count') == repeat_count for record in records)
    ]
    if len(aggregates) == 1:
        return aggregates[0]
    if repeat_count == 1:
        measured = [
            run_id for run_id, records in matching.items()
            if all(record.get('run_kind', 'measured') == 'measured' for record in records)
        ]
        if len(measured) == 1:
            return measured[0]
    raise ComparisonReportError(
        f'comparison report {role} target does not resolve to one dataset run'
    )


def _record_matches_target(record, target):
    identity = target['identity']
    client = identity['client_library']
    expected = (
        client['name'],
        normalize_client_library_source(client['source']),
        client['requested_ref'],
        client['resolved_commit'],
        normalize_platform(identity['architecture']),
        identity['ros_distro'],
    )
    actual = (
        record.get('client_library'),
        normalize_client_library_source(
            record.get('client_library_source'),
            record.get('client_library_ref'),
            record.get('client_library_commit'),
        ),
        record.get('client_library_ref'),
        record.get('client_library_commit'),
        normalize_platform(record.get('platform')),
        record.get('ros_distro'),
    )
    if actual != expected:
        return False
    benchmark = identity.get('benchmark_repository')
    if isinstance(benchmark, dict):
        return (
            record.get('benchmark_ref'),
            record.get('benchmark_commit'),
        ) == (
            benchmark.get('requested_ref'),
            benchmark.get('resolved_commit'),
        )
    return True


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)
