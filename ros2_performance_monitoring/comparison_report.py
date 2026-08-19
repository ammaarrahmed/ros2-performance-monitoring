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

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from ros2_performance_monitoring.comparison import CATEGORIES
from ros2_performance_monitoring.comparison import CATEGORY_THRESHOLDS
from ros2_performance_monitoring.model import normalize_client_library_source
from ros2_performance_monitoring.model import normalize_platform
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE
from ros2_performance_monitoring.statistical_comparison import INCOMPLETE_RESULTS
from ros2_performance_monitoring.statistical_comparison import INSUFFICIENT_EVIDENCE
from ros2_performance_monitoring.statistical_comparison import METHOD
from ros2_performance_monitoring.statistical_comparison import metric_policy
from ros2_performance_monitoring.statistical_comparison import MINIMUM_MEASURED_TRIALS
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

DECISIVE_STATUSES = {NO_REGRESSION, POSSIBLE_REGRESSION, REGRESSION}
INVALID_STATUSES = {INCOMPLETE_RESULTS, CANNOT_COMPARE}


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


def validate_comparison_report(report, records=None, dataset_checksum=None):
    """Validate report semantics and, when supplied, its exact dataset binding."""
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
        'topologies',
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
    analysis = _validate_analysis(report['analysis'])
    overall = _validate_overall_evidence(report['overall'])
    outcome = overall['status']
    targets = _validate_targets(
        report['targets'],
        require_complete=outcome not in INVALID_STATUSES,
    )
    categories = report['categories']
    if not isinstance(categories, dict) or set(categories) != set(CATEGORIES):
        raise ComparisonReportError('comparison report category coverage is invalid')
    for category, evidence in categories.items():
        _validate_category_evidence(evidence, category)

    scenarios = _validate_scenarios(report['scenarios'])
    _validate_outcome(
        overall,
        categories,
        scenarios,
        analysis,
    )
    topologies = _validate_topologies(report['topologies'])
    _validate_topology_outcomes(
        topologies,
        scenarios,
        analysis,
        outcome,
    )

    if (records is None) != (dataset_checksum is None):
        raise ComparisonReportError(
            'comparison report dataset records and checksum must be supplied together'
        )
    if records is None:
        checksum = dataset.get('sha256')
        if checksum is not None and not _valid_checksum(checksum):
            raise ComparisonReportError('comparison report dataset checksum is malformed')
        return ValidatedComparisonReport(
            report=report,
            reference_run='',
            candidate_run='',
        )

    checksum = dataset.get('sha256')
    if not _valid_checksum(checksum):
        raise ComparisonReportError('comparison report dataset checksum is malformed')
    if checksum != dataset_checksum:
        raise ComparisonReportError('comparison report dataset checksum does not match input')
    if not all(_complete_target(target) for target in targets.values()):
        raise ComparisonReportError(
            'comparison report targets cannot be resolved against the dataset'
        )
    if targets['reference']['target_key'] == targets['candidate']['target_key']:
        raise ComparisonReportError('comparison report targets must be different')

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

    if outcome in DECISIVE_STATUSES | {INSUFFICIENT_EVIDENCE}:
        _validate_bound_coverage(
            scenarios,
            run_groups,
            selected_runs,
            decisive=outcome in DECISIVE_STATUSES,
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
        or analysis['minimum_measured_trials'] < MINIMUM_MEASURED_TRIALS
        or analysis['measured_trial_pairs'] < 0
    ):
        raise ComparisonReportError('comparison report repeat settings are invalid')
    return analysis


def _validate_targets(targets, require_complete):
    if not isinstance(targets, dict) or set(targets) != {'reference', 'candidate'}:
        raise ComparisonReportError('comparison report targets are malformed')
    for role, target in targets.items():
        if not isinstance(target, dict):
            raise ComparisonReportError(f'comparison report {role} target is malformed')
        if set(target) == {'label'} and not require_complete:
            if not isinstance(target['label'], str) or not target['label']:
                raise ComparisonReportError(f'comparison report {role} target label is missing')
            continue
        if set(target) != {'label', 'target_key', 'identity'}:
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
    if (
        require_complete
        and targets['reference']['target_key'] == targets['candidate']['target_key']
    ):
        raise ComparisonReportError('comparison report targets must be different')
    return targets


def _validate_overall_evidence(evidence):
    context = 'overall'
    if not isinstance(evidence, dict):
        raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    status = evidence.get('status')
    if status not in EVIDENCE_STATUS_VALUES or status == NOT_APPLICABLE:
        raise ComparisonReportError(f'comparison report {context} status is unsupported')
    common = {
        'status',
        'practical_threshold',
        'point_estimate',
        'confidence_interval',
        'responsible_scenario',
        'responsible_metric',
    }
    if status in DECISIVE_STATUSES:
        expected = common | {'responsible_category'}
        if set(evidence) != expected:
            raise ComparisonReportError('comparison report overall evidence is malformed')
        _validate_overall_threshold(evidence['practical_threshold'])
        _validate_estimate(evidence, context)
        if evidence['responsible_category'] not in CATEGORIES:
            raise ComparisonReportError(
                'comparison report overall responsible category is invalid'
            )
        _validate_scenario_reference(evidence['responsible_scenario'], context)
        _validate_metric_reference(evidence['responsible_metric'], context)
    else:
        if set(evidence) != common | {'reason'}:
            raise ComparisonReportError('comparison report overall evidence is malformed')
        _validate_reason(evidence, context)
        _validate_empty_evidence(evidence, context)
        if evidence['practical_threshold'] is not None:
            raise ComparisonReportError(
                'comparison report overall threshold is invalid for its status'
            )
    return evidence


def _validate_category_evidence(evidence, category):
    context = f'category {category}'
    if not isinstance(evidence, dict):
        raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    status = evidence.get('status')
    if status not in EVIDENCE_STATUS_VALUES:
        raise ComparisonReportError(f'comparison report {context} status is unsupported')
    common = {
        'status',
        'practical_threshold',
        'point_estimate',
        'confidence_interval',
        'responsible_scenario',
        'responsible_metric',
    }
    expected = common | ({'reason'} if status in INVALID_STATUSES | {
        INSUFFICIENT_EVIDENCE,
    } else set())
    if set(evidence) != expected:
        raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    _validate_category_threshold(evidence['practical_threshold'], category, context)
    if status in DECISIVE_STATUSES:
        _validate_estimate(evidence, context)
        _validate_scenario_reference(evidence['responsible_scenario'], context)
        _validate_metric_reference(evidence['responsible_metric'], context)
    else:
        if status in INVALID_STATUSES | {INSUFFICIENT_EVIDENCE}:
            _validate_reason(evidence, context)
        _validate_empty_evidence(evidence, context)


def _validate_scenarios(scenarios):
    if not isinstance(scenarios, list):
        raise ComparisonReportError('comparison report scenarios are malformed')
    validated = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {'identity', 'categories'}:
            raise ComparisonReportError('comparison report scenario is malformed')
        identity = scenario['identity']
        _validate_scenario_reference(identity, 'scenario')
        identity_key = _scenario_key(identity)
        if identity_key in validated:
            raise ComparisonReportError('comparison report contains duplicate scenarios')
        categories = scenario['categories']
        if (
            not isinstance(categories, dict)
            or not categories
            or not set(categories) <= set(CATEGORIES)
        ):
            raise ComparisonReportError('comparison report scenario categories are malformed')
        for category, evidence in categories.items():
            _validate_scenario_evidence(evidence, category)
        validated[identity_key] = scenario
    return validated


def _validate_topologies(topologies):
    if not isinstance(topologies, dict):
        raise ComparisonReportError('comparison report topology summaries are malformed')
    validated = {}
    for topology, summary in topologies.items():
        if not isinstance(topology, str) or not topology:
            raise ComparisonReportError(
                'comparison report topology summary name is malformed'
            )
        if not isinstance(summary, dict) or set(summary) != {'overall', 'categories'}:
            raise ComparisonReportError(
                f'comparison report topology {topology} summary is malformed'
            )
        overall = _validate_overall_evidence(summary['overall'])
        categories = summary['categories']
        if not isinstance(categories, dict) or set(categories) != set(CATEGORIES):
            raise ComparisonReportError(
                f'comparison report topology {topology} category coverage is invalid'
            )
        for category, evidence in categories.items():
            _validate_category_evidence(evidence, category)
        validated[topology] = {
            'overall': overall,
            'categories': categories,
        }
    return validated


def _validate_topology_outcomes(topologies, scenarios, analysis, report_status):
    if report_status in INVALID_STATUSES:
        if topologies:
            raise ComparisonReportError(
                'invalid comparison report must not expose topology summaries'
            )
        return

    expected = {
        scenario['identity']['topology']
        for scenario in scenarios.values()
    }
    if set(topologies) != expected:
        raise ComparisonReportError(
            'comparison report topology summary coverage is inconsistent'
        )
    for topology, summary in topologies.items():
        scoped_scenarios = {
            identity: scenario
            for identity, scenario in scenarios.items()
            if scenario['identity']['topology'] == topology
        }
        _validate_outcome(
            summary['overall'],
            summary['categories'],
            scoped_scenarios,
            analysis,
        )


def _validate_scenario_evidence(evidence, category):
    context = f'scenario {category}'
    if not isinstance(evidence, dict):
        raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    status = evidence.get('status')
    common = {
        'status',
        'practical_threshold',
        'point_estimate',
        'confidence_interval',
        'responsible_metric',
        'metrics',
    }
    if status in DECISIVE_STATUSES:
        expected = common
    elif status == INSUFFICIENT_EVIDENCE:
        expected = common | {'reason'}
    else:
        raise ComparisonReportError(f'comparison report {context} status is unsupported')
    if set(evidence) != expected:
        raise ComparisonReportError(f'comparison report {context} evidence is malformed')
    _validate_category_threshold(evidence['practical_threshold'], category, context)
    metrics = evidence['metrics']
    if status == INSUFFICIENT_EVIDENCE:
        _validate_reason(evidence, context)
        _validate_empty_evidence(evidence, context, responsible_fields=('responsible_metric',))
        if metrics != []:
            raise ComparisonReportError(
                f'comparison report {context} exposes metrics without sufficient evidence'
            )
        return
    _validate_estimate(evidence, context)
    _validate_metric_reference(evidence['responsible_metric'], context)
    if not isinstance(metrics, list) or not metrics:
        raise ComparisonReportError(f'comparison report {context} metrics are malformed')
    identities = set()
    for metric in metrics:
        identity = _validate_metric_evidence(metric, category)
        if identity in identities:
            raise ComparisonReportError(
                f'comparison report {context} contains duplicate metrics'
            )
        identities.add(identity)


def _validate_metric_evidence(evidence, category):
    context = f'scenario {category} metric'
    fields = {
        'metric_name',
        'aggregation',
        'source_unit',
        'adverse_direction',
        'effect_unit',
        'practical_threshold',
        'point_estimate',
        'confidence_interval',
        'status',
    }
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise ComparisonReportError(f'comparison report {context} is malformed')
    policy = metric_policy(evidence['metric_name'], evidence['aggregation'])
    if policy is None or policy != (
        category,
        evidence['adverse_direction'],
        evidence['effect_unit'],
    ):
        raise ComparisonReportError(
            f'comparison report {context} does not belong to its category'
        )
    if not isinstance(evidence['source_unit'], str) or not evidence['source_unit']:
        raise ComparisonReportError(f'comparison report {context} source unit is invalid')
    if evidence['status'] not in DECISIVE_STATUSES:
        raise ComparisonReportError(f'comparison report {context} status is unsupported')
    _validate_category_threshold(evidence['practical_threshold'], category, context)
    _validate_estimate(evidence, context)
    _validate_threshold_status(evidence, category, context)
    return _metric_key(evidence)


def _validate_outcome(overall, categories, scenarios, analysis):
    status = overall['status']
    measured = analysis['measured_trial_pairs']
    minimum = analysis['minimum_measured_trials']
    if status in DECISIVE_STATUSES:
        if measured < minimum:
            raise ComparisonReportError(
                'decisive comparison report has too few measured trial pairs'
            )
        if not scenarios:
            raise ComparisonReportError(
                'decisive comparison report contains no scenario coverage'
            )
        _validate_decisive_consistency(overall, categories, scenarios)
        return
    if status == INSUFFICIENT_EVIDENCE:
        if measured < 1 or measured >= minimum:
            raise ComparisonReportError(
                'insufficient-evidence report measured-pair count is inconsistent'
            )
        if not scenarios:
            raise ComparisonReportError(
                'insufficient-evidence report contains no scenario coverage'
            )
        _validate_insufficient_consistency(categories, scenarios)
        return
    if status in INVALID_STATUSES:
        if scenarios:
            raise ComparisonReportError(
                'invalid comparison report must not expose scenario evidence'
            )
        for category, evidence in categories.items():
            if evidence['status'] != status:
                raise ComparisonReportError(
                    f'comparison report category {category} status is inconsistent'
                )
        return
    raise ComparisonReportError('comparison report overall status is unsupported')


def _validate_decisive_consistency(overall, categories, scenarios):
    category_scenarios = defaultdict(list)
    for scenario in scenarios.values():
        for category, evidence in scenario['categories'].items():
            if evidence['status'] not in DECISIVE_STATUSES:
                raise ComparisonReportError(
                    f'comparison report scenario {category} status is inconsistent'
                )
            metrics = evidence['metrics']
            worst = max(metric['point_estimate'] for metric in metrics)
            if not _same_number(evidence['point_estimate'], worst):
                raise ComparisonReportError(
                    f'comparison report scenario {category} point estimate is inconsistent'
                )
            responsible = _find_metric(metrics, evidence['responsible_metric'])
            if responsible is None or not _same_number(
                responsible['point_estimate'],
                worst,
            ):
                raise ComparisonReportError(
                    f'comparison report scenario {category} responsible metric is inconsistent'
                )
            _validate_threshold_status(evidence, category, f'scenario {category}')
            category_scenarios[category].append((scenario, evidence))

    for category, evidence in categories.items():
        contributing = category_scenarios.get(category, ())
        if not contributing:
            if evidence['status'] != NOT_APPLICABLE:
                raise ComparisonReportError(
                    f'comparison report category {category} must be N/A'
                )
            continue
        if evidence['status'] not in DECISIVE_STATUSES:
            raise ComparisonReportError(
                f'comparison report category {category} status is inconsistent'
            )
        worst = max(item['point_estimate'] for _scenario, item in contributing)
        if not _same_number(evidence['point_estimate'], worst):
            raise ComparisonReportError(
                f'comparison report category {category} point estimate is inconsistent'
            )
        identity = _scenario_key(evidence['responsible_scenario'])
        scenario = scenarios.get(identity)
        scenario_evidence = (
            scenario['categories'].get(category) if scenario is not None else None
        )
        if (
            scenario_evidence is None
            or not _same_number(scenario_evidence['point_estimate'], worst)
            or evidence['responsible_metric'] != scenario_evidence['responsible_metric']
        ):
            raise ComparisonReportError(
                f'comparison report category {category} responsible evidence is inconsistent'
            )
        _validate_threshold_status(evidence, category, f'category {category}')

    responsible_category = overall['responsible_category']
    responsible = categories[responsible_category]
    if responsible['status'] == NOT_APPLICABLE:
        raise ComparisonReportError(
            'comparison report overall responsible category is not applicable'
        )
    normalized_points = {
        category: evidence['point_estimate'] / CATEGORY_THRESHOLDS[category].regression
        for category, evidence in categories.items()
        if evidence['status'] != NOT_APPLICABLE
    }
    worst = max(normalized_points.values())
    if not _same_number(overall['point_estimate'], worst):
        raise ComparisonReportError(
            'comparison report overall point estimate is inconsistent'
        )
    if (
        not _same_number(normalized_points[responsible_category], worst)
        or overall['responsible_scenario'] != responsible['responsible_scenario']
        or overall['responsible_metric'] != responsible['responsible_metric']
    ):
        raise ComparisonReportError(
            'comparison report overall responsible evidence is inconsistent'
        )
    lower = overall['confidence_interval']['lower']
    upper = overall['confidence_interval']['upper']
    if lower > 1.0:
        expected = REGRESSION
    elif (
        upper >= 1.0
        or any(evidence['status'] != NO_REGRESSION for evidence in categories.values()
               if evidence['status'] != NOT_APPLICABLE)
    ):
        expected = POSSIBLE_REGRESSION
    else:
        expected = NO_REGRESSION
    if overall['status'] != expected:
        raise ComparisonReportError('comparison report overall status is inconsistent')


def _validate_insufficient_consistency(categories, scenarios):
    applicable = {
        category
        for scenario in scenarios.values()
        for category in scenario['categories']
    }
    for scenario in scenarios.values():
        for category, evidence in scenario['categories'].items():
            if evidence['status'] != INSUFFICIENT_EVIDENCE:
                raise ComparisonReportError(
                    f'comparison report scenario {category} status is inconsistent'
                )
    for category, evidence in categories.items():
        expected = (
            INSUFFICIENT_EVIDENCE if category in applicable else NOT_APPLICABLE
        )
        if evidence['status'] != expected:
            raise ComparisonReportError(
                f'comparison report category {category} status is inconsistent'
            )


def _validate_bound_coverage(scenarios, run_groups, selected_runs, decisive):
    scenario_identities = set(scenarios)
    for role, run_id in selected_runs.items():
        dataset_coverage = _dataset_coverage(run_groups[run_id])
        if set(dataset_coverage) != scenario_identities:
            raise ComparisonReportError(
                f'comparison report scenario coverage does not match {role} target'
            )
        for identity, scenario in scenarios.items():
            reported_categories = set(scenario['categories'])
            dataset_categories = set(dataset_coverage[identity])
            if reported_categories != dataset_categories:
                raise ComparisonReportError(
                    f'comparison report category coverage does not match {role} target'
                )
            if not decisive:
                continue
            for category, evidence in scenario['categories'].items():
                reported_metrics = {_metric_key(metric) for metric in evidence['metrics']}
                if reported_metrics != dataset_coverage[identity][category]:
                    raise ComparisonReportError(
                        f'comparison report metric coverage does not match {role} target'
                    )


def _dataset_coverage(records):
    coverage = defaultdict(lambda: defaultdict(set))
    for record in records:
        identity = tuple(record.get(field, '') for field in SCENARIO_FIELDS)
        policy = metric_policy(record.get('metric_name'), record.get('aggregation'))
        if policy is None:
            continue
        category = policy[0]
        coverage[identity][category].add((
            record.get('metric_name'),
            record.get('aggregation'),
            record.get('unit'),
        ))
    return {
        identity: dict(categories)
        for identity, categories in coverage.items()
    }


def _validate_category_threshold(threshold, category, context):
    expected = CATEGORY_THRESHOLDS[category]
    unit = 'percentage_points' if category == 'reliability' else 'percent'
    if (
        not isinstance(threshold, dict)
        or set(threshold) != {'possible', 'regression', 'unit'}
        or not _finite_number(threshold.get('possible'))
        or not _finite_number(threshold.get('regression'))
        or threshold['possible'] < 0.0
        or threshold['possible'] >= threshold['regression']
        or threshold['possible'] != expected.possible
        or threshold['regression'] != expected.regression
        or threshold.get('unit') != unit
    ):
        raise ComparisonReportError(
            f'comparison report {context} practical threshold is invalid'
        )


def _validate_overall_threshold(threshold):
    expected_possible = {
        category: round(values.possible / values.regression, 12)
        for category, values in CATEGORY_THRESHOLDS.items()
    }
    if (
        not isinstance(threshold, dict)
        or set(threshold) != {'regression', 'unit', 'possible_by_category'}
        or threshold.get('regression') != 1.0
        or threshold.get('unit') != 'category_regression_threshold_multiple'
        or threshold.get('possible_by_category') != expected_possible
    ):
        raise ComparisonReportError(
            'comparison report overall practical threshold is invalid'
        )


def _validate_estimate(evidence, context):
    point = evidence.get('point_estimate')
    if not _finite_number(point):
        raise ComparisonReportError(
            f'comparison report {context} point estimate is invalid'
        )
    interval = evidence.get('confidence_interval')
    if not isinstance(interval, dict) or set(interval) != {'lower', 'upper'}:
        raise ComparisonReportError(f'comparison report {context} interval is malformed')
    lower = interval['lower']
    upper = interval['upper']
    if not _finite_number(lower) or not _finite_number(upper) or lower > upper:
        raise ComparisonReportError(f'comparison report {context} interval is invalid')


def _validate_empty_evidence(
    evidence,
    context,
    responsible_fields=('responsible_scenario', 'responsible_metric'),
):
    if evidence.get('point_estimate') is not None:
        raise ComparisonReportError(
            f'comparison report {context} exposes a point estimate for its status'
        )
    if evidence.get('confidence_interval') is not None:
        raise ComparisonReportError(
            f'comparison report {context} exposes an interval for its status'
        )
    if any(evidence.get(field) is not None for field in responsible_fields):
        raise ComparisonReportError(
            f'comparison report {context} exposes responsible evidence for its status'
        )


def _validate_reason(evidence, context):
    reason = evidence.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise ComparisonReportError(f'comparison report {context} reason is missing')


def _validate_threshold_status(evidence, category, context):
    thresholds = CATEGORY_THRESHOLDS[category]
    lower = evidence['confidence_interval']['lower']
    upper = evidence['confidence_interval']['upper']
    if lower > thresholds.regression:
        expected = REGRESSION
    elif upper >= thresholds.possible:
        expected = POSSIBLE_REGRESSION
    else:
        expected = NO_REGRESSION
    if evidence['status'] != expected:
        raise ComparisonReportError(
            f'comparison report {context} status is inconsistent with its evidence'
        )


def _validate_scenario_reference(identity, context):
    if not isinstance(identity, dict) or set(identity) != set(SCENARIO_FIELDS):
        raise ComparisonReportError(
            f'comparison report {context} scenario reference is malformed'
        )
    string_fields = set(SCENARIO_FIELDS) - {'payload_size', 'frequency'}
    if any(not isinstance(identity[field], str) for field in string_fields):
        raise ComparisonReportError(
            f'comparison report {context} scenario reference is malformed'
        )
    if type(identity['payload_size']) is not int or identity['payload_size'] < 0:
        raise ComparisonReportError(
            f'comparison report {context} scenario reference is malformed'
        )
    if not _finite_number(identity['frequency']) or identity['frequency'] < 0.0:
        raise ComparisonReportError(
            f'comparison report {context} scenario reference is malformed'
        )


def _validate_metric_reference(metric, context):
    if (
        not isinstance(metric, dict)
        or set(metric) != {'metric_name', 'aggregation', 'source_unit'}
        or any(not isinstance(metric[field], str) or not metric[field] for field in metric)
    ):
        raise ComparisonReportError(
            f'comparison report {context} metric reference is malformed'
        )


def _scenario_key(identity):
    return tuple(identity[field] for field in SCENARIO_FIELDS)


def _metric_key(metric):
    return (
        metric['metric_name'],
        metric['aggregation'],
        metric['source_unit'],
    )


def _find_metric(metrics, reference):
    key = _metric_key(reference)
    return next((metric for metric in metrics if _metric_key(metric) == key), None)


def _complete_target(target):
    return isinstance(target, dict) and set(target) == {
        'label',
        'target_key',
        'identity',
    }


def _valid_checksum(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _same_number(left, right):
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


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
