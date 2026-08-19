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
import math
import random
import re
from statistics import median

from ros2_performance_monitoring.comparison import CATEGORIES
from ros2_performance_monitoring.comparison import CATEGORY_THRESHOLDS


REPORT_SCHEMA_VERSION = 2
METHOD = 'paired-bootstrap-worst-scenario-v1'
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_BOOTSTRAP_REPEATS = 10000
DEFAULT_SEED = 0
MINIMUM_MEASURED_TRIALS = 3

EXIT_NO_REGRESSION = 0
EXIT_REGRESSION = 1
EXIT_INCONCLUSIVE = 2
EXIT_INVALID_COMPARISON = 3
EXIT_OPERATIONAL_FAILURE = 4

NO_REGRESSION = 'No regression'
POSSIBLE_REGRESSION = 'Possible regression'
REGRESSION = 'Regression'
INSUFFICIENT_EVIDENCE = 'Insufficient evidence'
INCOMPLETE_RESULTS = 'Incomplete results'
CANNOT_COMPARE = 'Cannot compare'
NOT_APPLICABLE = 'N/A'

SCENARIO_FIELDS = (
    'topology',
    'process_mode',
    'payload_size',
    'frequency',
    'rmw_implementation',
    'communication_mode',
    'executor',
    'node_role',
)
COMMON_PROVENANCE_FIELDS = (
    'schema_version',
    'benchmark_ref',
    'benchmark_commit',
    'client_library',
    'platform',
    'ros_distro',
    'executor',
)
TARGET_PROVENANCE_FIELDS = (
    'client_library_source',
    'client_library_ref',
    'client_library_commit',
)


class StatisticalComparisonError(ValueError):
    """Report an invalid analysis configuration or malformed experiment plan."""


def comparison_exit_code(report):
    """Map the report's overall evidence status to the documented CLI outcome."""
    status = report.get('overall', {}).get('status')
    if status == NO_REGRESSION:
        return EXIT_NO_REGRESSION
    if status == REGRESSION:
        return EXIT_REGRESSION
    if status in (POSSIBLE_REGRESSION, INSUFFICIENT_EVIDENCE):
        return EXIT_INCONCLUSIVE
    return EXIT_INVALID_COMPARISON


def build_comparison_report(
    plan,
    trial_records,
    *,
    reference='reference',
    candidate='candidate',
    confidence_level=DEFAULT_CONFIDENCE_LEVEL,
    bootstrap_repeats=DEFAULT_BOOTSTRAP_REPEATS,
    seed=DEFAULT_SEED,
    minimum_trials=MINIMUM_MEASURED_TRIALS,
    dataset_sha256=None,
):
    """Compare measured experiment trials with a deterministic paired bootstrap."""
    _validate_analysis_options(
        confidence_level,
        bootstrap_repeats,
        seed,
        minimum_trials,
    )
    if dataset_sha256 is not None and not re.fullmatch(
        r'[0-9a-f]{64}', dataset_sha256
    ):
        raise StatisticalComparisonError(
            'dataset checksum must be a lowercase SHA-256 digest'
        )
    report = _report_skeleton(
        plan,
        reference,
        candidate,
        confidence_level,
        bootstrap_repeats,
        seed,
        minimum_trials,
        dataset_sha256,
    )
    targets = _target_map(plan)
    if reference == candidate:
        return _invalid_report(report, CANNOT_COMPARE, 'targets must be different')
    if reference not in targets or candidate not in targets:
        return _invalid_report(
            report,
            CANNOT_COMPARE,
            'selected reference or candidate is not present in the experiment plan',
        )
    compatibility_error = _target_compatibility_error(
        targets[reference],
        targets[candidate],
    )
    if compatibility_error:
        return _invalid_report(report, CANNOT_COMPARE, compatibility_error)
    report['targets'] = {
        'reference': _target_report(targets[reference]),
        'candidate': _target_report(targets[candidate]),
    }

    pairs, pairing_error = _paired_trials(plan, reference, candidate)
    if pairing_error:
        return _invalid_report(report, CANNOT_COMPARE, pairing_error)
    report['analysis']['measured_trial_pairs'] = len(pairs)

    loaded_pairs = []
    for pair in pairs:
        reference_records = _measured_records(
            trial_records.get(pair['reference']['trial_id']),
            pair['reference']['trial_id'],
        )
        candidate_records = _measured_records(
            trial_records.get(pair['candidate']['trial_id']),
            pair['candidate']['trial_id'],
        )
        if reference_records is None or candidate_records is None:
            return _invalid_report(
                report,
                INCOMPLETE_RESULTS,
                f'measured trial block {pair["sequence"]} is not complete',
            )
        loaded_pairs.append((pair, reference_records, candidate_records))

    validation_error = _validate_records(loaded_pairs, targets, reference, candidate)
    if validation_error:
        status, reason = validation_error
        return _invalid_report(report, status, reason)

    metric_pairs = _metric_pairs(loaded_pairs)
    if not metric_pairs:
        return _invalid_report(
            report,
            CANNOT_COMPARE,
            'the experiment contains no supported KPI metrics',
        )

    if len(pairs) < minimum_trials:
        return _insufficient_report(report, metric_pairs)

    _analyse_metrics(report, metric_pairs)
    return report


def _validate_analysis_options(confidence_level, bootstrap_repeats, seed, minimum_trials):
    if type(confidence_level) not in (int, float) or not 0.0 < confidence_level < 1.0:
        raise StatisticalComparisonError('confidence level must be between 0 and 1')
    if type(bootstrap_repeats) is not int or bootstrap_repeats < 1:
        raise StatisticalComparisonError('bootstrap repeat count must be a positive integer')
    if type(seed) is not int:
        raise StatisticalComparisonError('bootstrap seed must be an integer')
    if (
        type(minimum_trials) is not int
        or minimum_trials < MINIMUM_MEASURED_TRIALS
    ):
        raise StatisticalComparisonError(
            f'minimum trial count must be at least {MINIMUM_MEASURED_TRIALS}'
        )


def _report_skeleton(
    plan,
    reference,
    candidate,
    confidence_level,
    bootstrap_repeats,
    seed,
    minimum_trials,
    dataset_sha256,
):
    if not isinstance(plan, dict):
        raise StatisticalComparisonError('experiment plan must be a JSON object')
    experiment_id = str(plan.get('experiment_id') or '')
    return {
        'schema_version': REPORT_SCHEMA_VERSION,
        'experiment_id': experiment_id,
        'dataset': {
            'sha256': dataset_sha256,
            'experiment_id': experiment_id,
        },
        'targets': {
            'reference': {'label': reference},
            'candidate': {'label': candidate},
        },
        'analysis': {
            'method': METHOD,
            'confidence_level': float(confidence_level),
            'seed': seed,
            'bootstrap_repeats': bootstrap_repeats,
            'minimum_measured_trials': minimum_trials,
            'measured_trial_pairs': 0,
            'pairing': 'recorded balanced execution blocks',
            'point_estimator': 'median of measured trials',
        },
        'overall': _empty_evidence(),
        'categories': {
            category: _empty_evidence(_threshold_report(category))
            for category in CATEGORIES
        },
        'scenarios': [],
    }


def _target_map(plan):
    if plan.get('schema_version') != 1:
        return {}
    targets = plan.get('targets')
    if not isinstance(targets, list):
        return {}
    mapped = {}
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get('label'), str):
            return {}
        if target['label'] in mapped:
            return {}
        mapped[target['label']] = target
    return mapped


def _target_report(target):
    return {
        'label': target['label'],
        'target_key': target.get('target_key'),
        'identity': target.get('identity'),
    }


def _target_compatibility_error(reference, candidate):
    if reference.get('target_key') == candidate.get('target_key'):
        return 'reference and candidate resolve to the same target identity'
    reference_identity = reference.get('identity')
    candidate_identity = candidate.get('identity')
    if not isinstance(reference_identity, dict) or not isinstance(candidate_identity, dict):
        return 'selected targets have incomplete identity provenance'
    reference_common = {
        key: value for key, value in reference_identity.items()
        if key != 'client_library'
    }
    candidate_common = {
        key: value for key, value in candidate_identity.items()
        if key != 'client_library'
    }
    if reference_common != candidate_common:
        return 'selected targets have incompatible benchmark provenance'
    return None


def _paired_trials(plan, reference, candidate):
    schedule = plan.get('schedule')
    if not isinstance(schedule, dict) or not isinstance(schedule.get('trials'), list):
        return (), 'experiment plan has no recorded trial schedule'
    if schedule.get('order') != 'balanced':
        return (), 'recorded schedule is not balanced; valid paired blocks are unavailable'

    blocks = defaultdict(list)
    for trial in schedule['trials']:
        if not isinstance(trial, dict) or trial.get('kind') != 'measured':
            continue
        if trial.get('target') not in (reference, candidate):
            continue
        sequence = trial.get('sequence')
        planned_order = trial.get('planned_order')
        trial_id = trial.get('trial_id')
        if (
            type(sequence) is not int
            or type(planned_order) is not int
            or not isinstance(trial_id, str)
            or not trial_id
        ):
            return (), 'measured trial pairing metadata is invalid'
        blocks[sequence].append(trial)

    pairs = []
    previous_first = None
    for sequence in sorted(blocks):
        trials = sorted(blocks[sequence], key=lambda trial: trial['planned_order'])
        labels = [trial['target'] for trial in trials]
        orders = [trial['planned_order'] for trial in trials]
        if set(labels) != {reference, candidate} or len(trials) != 2:
            return (), f'measured trial block {sequence} does not contain both targets'
        if orders[1] != orders[0] + 1:
            return (), f'measured trial block {sequence} is not contiguous'
        if previous_first == labels[0]:
            return (), 'balanced trial blocks do not alternate target execution order'
        previous_first = labels[0]
        by_target = {trial['target']: trial for trial in trials}
        pairs.append({
            'sequence': sequence,
            'reference': by_target[reference],
            'candidate': by_target[candidate],
        })

    expected = schedule.get('measured_repeat_count')
    if type(expected) is not int or expected < 1 or len(pairs) != expected:
        return (), 'recorded schedule does not contain every measured trial block'
    return tuple(pairs), None


def _measured_records(records, trial_id):
    if records is None:
        return None
    selected = tuple(
        record for record in records
        if isinstance(record, dict) and record.get('run_kind', 'measured') == 'measured'
    )
    if not selected or any(record.get('run_id') != trial_id for record in selected):
        return None
    return selected


def _validate_records(loaded_pairs, targets, reference, candidate):
    expected_identities = None
    common_provenance = None
    for pair, reference_records, candidate_records in loaded_pairs:
        reference_map, error = _record_map(reference_records)
        if error:
            return CANNOT_COMPARE, error
        candidate_map, error = _record_map(candidate_records)
        if error:
            return CANNOT_COMPARE, error
        if set(reference_map) != set(candidate_map):
            return (
                CANNOT_COMPARE,
                f'scenario or metric coverage differs in trial block {pair["sequence"]}',
            )
        if expected_identities is None:
            expected_identities = set(reference_map)
        elif set(reference_map) != expected_identities:
            return INCOMPLETE_RESULTS, 'metric coverage changes between measured trial blocks'

        for label, records in (
            (reference, reference_records),
            (candidate, candidate_records),
        ):
            target_error = _validate_target_provenance(records, targets[label])
            if target_error:
                return CANNOT_COMPARE, target_error
            for record in records:
                provenance = tuple(record.get(field) for field in COMMON_PROVENANCE_FIELDS)
                if common_provenance is None:
                    common_provenance = provenance
                elif provenance != common_provenance:
                    return CANNOT_COMPARE, 'benchmark or environment provenance is incompatible'

    missing = _missing_required_metrics(expected_identities or set())
    if missing:
        return INCOMPLETE_RESULTS, f'required metric is missing: {missing[0]}'
    return None


def _record_map(records):
    mapped = {}
    for record in records:
        value = record.get('numeric_value')
        if type(value) not in (int, float) or not math.isfinite(value):
            return {}, 'measured trial contains a non-finite or non-numeric metric'
        identity = _metric_identity(record)
        if identity in mapped:
            return {}, 'measured trial contains a duplicate metric identity'
        mapped[identity] = float(value)
    return mapped, None


def _validate_target_provenance(records, target):
    identity = target.get('identity')
    client = identity.get('client_library') if isinstance(identity, dict) else None
    if not isinstance(client, dict):
        return f'target {target.get("label")} has no client-library identity'
    expected = (
        client.get('source'),
        client.get('requested_ref'),
        client.get('resolved_commit'),
    )
    for record in records:
        actual = tuple(record.get(field) for field in TARGET_PROVENANCE_FIELDS)
        if actual != expected:
            return f'trial provenance does not match target {target.get("label")}'
    return None


def _metric_identity(record):
    scenario = tuple(record.get(field, '') for field in SCENARIO_FIELDS)
    return (
        scenario,
        record.get('metric_name', ''),
        record.get('aggregation', ''),
        record.get('unit', ''),
    )


def _missing_required_metrics(identities):
    by_scenario = defaultdict(set)
    for scenario, metric_name, aggregation, _unit in identities:
        by_scenario[scenario].add((metric_name, aggregation))
    missing = []
    for scenario, available in sorted(by_scenario.items()):
        scenario_item = dict(zip(SCENARIO_FIELDS, scenario))
        for required in sorted(_required_metrics(scenario_item)):
            if required not in available:
                missing.append(f'{required[0]}/{required[1]} in {_scenario_label(scenario_item)}')
    return missing


def _required_metrics(scenario):
    topology = scenario['topology']
    role = scenario['node_role']
    resources = {('resource_cpu', 'max'), ('resource_memory_rss', 'max')}
    if topology == 'pub-sub':
        if role == 'publisher':
            return resources
        return resources | {
            ('subscription_latency', 'mean'),
            ('subscription_latency', 'p95'),
            ('subscription_throughput', 'observed'),
            ('total_messages_lost', 'percent'),
            ('total_messages_late', 'percent'),
            ('total_messages_too_late', 'percent'),
        }
    if topology == 'service':
        if role == 'service':
            return resources
        return resources | {
            ('service_client_latency', 'mean'),
            ('service_client_latency', 'p95'),
        }
    return set()


def _metric_pairs(loaded_pairs):
    values = defaultdict(list)
    for _pair, reference_records, candidate_records in loaded_pairs:
        reference_map, _ = _record_map(reference_records)
        candidate_map, _ = _record_map(candidate_records)
        for identity in sorted(reference_map):
            if metric_policy(identity[1], identity[2]) is None:
                continue
            values[identity].append((reference_map[identity], candidate_map[identity]))
    return dict(values)


def metric_policy(metric_name, aggregation):
    """Return the statistical category, adverse direction, and effect unit."""
    if metric_name in ('subscription_latency', 'service_client_latency'):
        if aggregation in ('mean', 'p95'):
            return 'latency', 'increase', 'percent'
    elif metric_name == 'subscription_throughput' and aggregation == 'observed':
        return 'throughput', 'decrease', 'percent'
    elif metric_name in ('resource_cpu', 'resource_memory_rss') and aggregation == 'max':
        return 'resources', 'increase', 'percent'
    elif metric_name in (
        'total_messages_lost',
        'total_messages_late',
        'total_messages_too_late',
    ) and aggregation == 'percent':
        return 'reliability', 'absolute_increase', 'percentage_points'
    return None


def _insufficient_report(report, metric_pairs):
    reason = (
        f'{report["analysis"]["measured_trial_pairs"]} measured trial pairs are available; '
        f'at least {report["analysis"]["minimum_measured_trials"]} are required'
    )
    scenario_categories = defaultdict(set)
    for identity in metric_pairs:
        scenario, metric_name, aggregation, _unit = identity
        category, _direction, _effect_unit = metric_policy(
            metric_name,
            aggregation,
        )
        scenario_categories[scenario].add(category)
    applicable = {
        category
        for categories in scenario_categories.values()
        for category in categories
    }

    report['overall'] = _empty_evidence(status=INSUFFICIENT_EVIDENCE, reason=reason)
    for category in CATEGORIES:
        if category in applicable:
            report['categories'][category].update({
                'status': INSUFFICIENT_EVIDENCE,
                'reason': reason,
            })
        else:
            report['categories'][category].update({'status': NOT_APPLICABLE})

    for scenario, categories in sorted(scenario_categories.items()):
        report['scenarios'].append({
            'identity': dict(zip(SCENARIO_FIELDS, scenario)),
            'categories': {
                category: _empty_scenario_evidence(
                    category,
                    INSUFFICIENT_EVIDENCE,
                    reason,
                )
                for category in CATEGORIES
                if category in categories
            },
        })
    return report


def _analyse_metrics(report, metric_pairs):
    confidence_level = report['analysis']['confidence_level']
    repeats = report['analysis']['bootstrap_repeats']
    seed = report['analysis']['seed']
    grouped = defaultdict(lambda: defaultdict(list))
    for identity, pairs in metric_pairs.items():
        scenario, metric_name, aggregation, unit = identity
        category, direction, effect_unit = metric_policy(metric_name, aggregation)
        grouped[scenario][category].append({
            'metric_name': metric_name,
            'aggregation': aggregation,
            'source_unit': unit,
            'direction': direction,
            'effect_unit': effect_unit,
            'pairs': pairs,
        })

    category_distributions = dict.fromkeys(CATEGORIES)
    category_points = dict.fromkeys(CATEGORIES)
    category_responsible = dict.fromkeys(CATEGORIES)
    overall_distribution = None
    overall_point = None
    overall_responsible = None

    for scenario in sorted(grouped):
        scenario_item = dict(zip(SCENARIO_FIELDS, scenario))
        scenario_report = {'identity': scenario_item, 'categories': {}}
        for category in CATEGORIES:
            metrics = grouped[scenario].get(category, ())
            if not metrics:
                continue
            scenario_distribution = None
            scenario_point = None
            responsible_metric = None
            metric_reports = []
            for metric in sorted(
                metrics,
                key=lambda item: (item['metric_name'], item['aggregation'], item['source_unit']),
            ):
                point = _effect_for_pairs(metric['pairs'], metric['direction'])
                distribution = _bootstrap_distribution(
                    metric['pairs'],
                    metric['direction'],
                    repeats,
                    seed,
                )
                lower, upper = _interval(distribution, confidence_level)
                thresholds = CATEGORY_THRESHOLDS[category]
                metric_report = {
                    'metric_name': metric['metric_name'],
                    'aggregation': metric['aggregation'],
                    'source_unit': metric['source_unit'],
                    'adverse_direction': metric['direction'],
                    'effect_unit': metric['effect_unit'],
                    'practical_threshold': _threshold_report(category),
                    'point_estimate': _clean_float(point),
                    'confidence_interval': _interval_report(lower, upper),
                    'status': _evidence_status(point, lower, upper, thresholds),
                }
                metric_reports.append(metric_report)
                if scenario_point is None or point > scenario_point:
                    scenario_point = point
                    responsible_metric = _metric_reference(metric)
                scenario_distribution = _elementwise_max(
                    scenario_distribution,
                    distribution,
                )

                normalized = [value / thresholds.regression for value in distribution]
                overall_distribution = _elementwise_max(overall_distribution, normalized)
                normalized_point = point / thresholds.regression
                if overall_point is None or normalized_point > overall_point:
                    overall_point = normalized_point
                    overall_responsible = {
                        'category': category,
                        'scenario': scenario_item,
                        'metric': _metric_reference(metric),
                    }

            lower, upper = _interval(scenario_distribution, confidence_level)
            scenario_evidence = {
                'status': _evidence_status(
                    scenario_point,
                    lower,
                    upper,
                    CATEGORY_THRESHOLDS[category],
                ),
                'practical_threshold': _threshold_report(category),
                'point_estimate': _clean_float(scenario_point),
                'confidence_interval': _interval_report(lower, upper),
                'responsible_metric': responsible_metric,
                'metrics': metric_reports,
            }
            scenario_report['categories'][category] = scenario_evidence
            category_distributions[category] = _elementwise_max(
                category_distributions[category],
                scenario_distribution,
            )
            if category_points[category] is None or scenario_point > category_points[category]:
                category_points[category] = scenario_point
                category_responsible[category] = {
                    'scenario': scenario_item,
                    'metric': responsible_metric,
                }
        report['scenarios'].append(scenario_report)

    for category in CATEGORIES:
        distribution = category_distributions[category]
        if distribution is None:
            report['categories'][category].update({'status': NOT_APPLICABLE})
            continue
        lower, upper = _interval(distribution, confidence_level)
        point = category_points[category]
        report['categories'][category] = {
            'status': _evidence_status(
                point,
                lower,
                upper,
                CATEGORY_THRESHOLDS[category],
            ),
            'practical_threshold': _threshold_report(category),
            'point_estimate': _clean_float(point),
            'confidence_interval': _interval_report(lower, upper),
            'responsible_scenario': category_responsible[category]['scenario'],
            'responsible_metric': category_responsible[category]['metric'],
        }

    lower, upper = _interval(overall_distribution, confidence_level)
    category_statuses = {
        evidence['status'] for evidence in report['categories'].values()
    }
    if lower > 1.0:
        overall_status = REGRESSION
    elif POSSIBLE_REGRESSION in category_statuses or upper >= 1.0:
        overall_status = POSSIBLE_REGRESSION
    else:
        overall_status = NO_REGRESSION
    report['overall'] = {
        'status': overall_status,
        'practical_threshold': {
            'regression': 1.0,
            'unit': 'category_regression_threshold_multiple',
            'possible_by_category': {
                category: _clean_float(
                    thresholds.possible / thresholds.regression
                )
                for category, thresholds in CATEGORY_THRESHOLDS.items()
            },
        },
        'point_estimate': _clean_float(overall_point),
        'confidence_interval': _interval_report(lower, upper),
        'responsible_category': overall_responsible['category'],
        'responsible_scenario': overall_responsible['scenario'],
        'responsible_metric': overall_responsible['metric'],
    }


def _effect_for_pairs(pairs, direction):
    reference = median(pair[0] for pair in pairs)
    candidate = median(pair[1] for pair in pairs)
    return _adverse_effect(reference, candidate, direction)


def _bootstrap_distribution(pairs, direction, repeats, seed):
    randomizer = random.Random(seed)
    count = len(pairs)
    distribution = []
    for _ in range(repeats):
        sample = [pairs[randomizer.randrange(count)] for _ in range(count)]
        distribution.append(_effect_for_pairs(sample, direction))
    return distribution


def _adverse_effect(reference, candidate, direction):
    if direction == 'absolute_increase':
        return candidate - reference
    if direction == 'increase':
        if reference == 0.0 and candidate == 0.0:
            return 0.0
        return 100.0 * (candidate - reference) / max(abs(reference), 0.000001)
    if direction == 'decrease':
        if reference == 0.0 and candidate == 0.0:
            return 0.0
        return 100.0 * (reference - candidate) / max(abs(reference), 0.000001)
    raise StatisticalComparisonError(f'unsupported adverse direction: {direction}')


def _interval(distribution, confidence_level):
    if not distribution:
        return None, None
    ordered = sorted(distribution)
    alpha = (1.0 - confidence_level) / 2.0
    return _quantile(ordered, alpha), _quantile(ordered, 1.0 - alpha)


def _quantile(ordered, probability):
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + fraction * (
        ordered[upper_index] - ordered[lower_index]
    )


def _evidence_status(point, lower, upper, thresholds):
    if lower > thresholds.regression:
        return REGRESSION
    if upper >= thresholds.possible:
        return POSSIBLE_REGRESSION
    return NO_REGRESSION


def _elementwise_max(current, candidate):
    if current is None:
        return list(candidate)
    return [max(left, right) for left, right in zip(current, candidate)]


def _metric_reference(metric):
    return {
        'metric_name': metric['metric_name'],
        'aggregation': metric['aggregation'],
        'source_unit': metric['source_unit'],
    }


def _threshold_report(category):
    thresholds = CATEGORY_THRESHOLDS[category]
    return {
        'possible': thresholds.possible,
        'regression': thresholds.regression,
        'unit': 'percentage_points' if category == 'reliability' else 'percent',
    }


def _empty_evidence(threshold=None, status=NOT_APPLICABLE, reason=None):
    evidence = {
        'status': status,
        'practical_threshold': threshold,
        'point_estimate': None,
        'confidence_interval': None,
        'responsible_scenario': None,
        'responsible_metric': None,
    }
    if reason is not None:
        evidence['reason'] = reason
    return evidence


def _empty_scenario_evidence(category, status, reason):
    return {
        'status': status,
        'practical_threshold': _threshold_report(category),
        'point_estimate': None,
        'confidence_interval': None,
        'responsible_metric': None,
        'metrics': [],
        'reason': reason,
    }


def _invalid_report(report, status, reason):
    report['overall'] = _empty_evidence(status=status, reason=reason)
    for category in CATEGORIES:
        report['categories'][category] = _empty_evidence(
            _threshold_report(category),
            status=status,
            reason=reason,
        )
    return report


def _interval_report(lower, upper):
    return {
        'lower': _clean_float(lower),
        'upper': _clean_float(upper),
    }


def _clean_float(value):
    if value is None:
        return None
    cleaned = round(float(value), 12)
    return 0.0 if cleaned == 0.0 else cleaned


def _scenario_label(scenario):
    return '/'.join(str(scenario[field]) for field in SCENARIO_FIELDS)
