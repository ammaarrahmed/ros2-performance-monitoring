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

from collections import Counter
import math
import re
from statistics import fmean
from statistics import median
from statistics import stdev

from .comparison import CATEGORIES
from .comparison import CATEGORY_THRESHOLDS
from .statistical_comparison import analyse_paired_metric
from .statistical_comparison import DEFAULT_BOOTSTRAP_REPEATS
from .statistical_comparison import DEFAULT_CONFIDENCE_LEVEL
from .statistical_comparison import DEFAULT_SEED
from .statistical_comparison import load_paired_metric_samples
from .statistical_comparison import metric_policy
from .statistical_comparison import NO_REGRESSION
from .statistical_comparison import POSSIBLE_REGRESSION
from .statistical_comparison import REGRESSION
from .statistical_comparison import SCENARIO_FIELDS
from .statistical_comparison import StatisticalComparisonError


CALIBRATION_REPORT_SCHEMA_VERSION = 1
CALIBRATION_METHOD = 'paired-bootstrap-aa-calibration-v1'
CALIBRATION_NOTICE = (
    'Calibration evidence only; this report is not a regression verdict or release gate.'
)


class CalibrationError(ValueError):
    """Report invalid calibration evidence or report structure."""


def build_calibration_report(
    plan,
    trial_records,
    trial_environments,
    measured_environment,
    *,
    confidence_level=DEFAULT_CONFIDENCE_LEVEL,
    bootstrap_repeats=DEFAULT_BOOTSTRAP_REPEATS,
    seed=DEFAULT_SEED,
    dataset_sha256,
):
    """Build deterministic A/A noise evidence from two same-target streams."""
    if not re.fullmatch(r'[0-9a-f]{64}', str(dataset_sha256 or '')):
        raise CalibrationError('dataset checksum must be a lowercase SHA-256 digest')
    if not isinstance(measured_environment, dict):
        raise CalibrationError('measured environment provenance is required')
    try:
        targets, pairs, metric_pairs = load_paired_metric_samples(
            plan,
            trial_records,
            calibration=True,
        )
    except StatisticalComparisonError as exc:
        raise CalibrationError(str(exc)) from exc

    observations = _trial_observations(pairs, trial_environments)
    target = targets['reference']
    kpis = []
    all_classifications = []
    for identity, values in sorted(metric_pairs.items()):
        scenario, metric_name, aggregation, source_unit = identity
        category, direction, effect_unit = metric_policy(metric_name, aggregation)
        analysis = analyse_paired_metric(
            values,
            direction,
            confidence_level=confidence_level,
            bootstrap_repeats=bootstrap_repeats,
            seed=seed,
        )
        threshold = _threshold(category)
        paired_effects = []
        classifications = []
        for pair, (reference_value, candidate_value), effect in zip(
            pairs,
            values,
            analysis['paired_effects'],
        ):
            classification = _threshold_classification(effect, category)
            classifications.append(classification)
            paired_effects.append({
                'sequence': pair['sequence'],
                'reference': _clean_float(reference_value),
                'candidate': _clean_float(candidate_value),
                'adverse_effect': _clean_float(effect),
                'classification': classification,
            })
        all_classifications.extend(classifications)
        lower, upper = analysis['confidence_interval']
        kpis.append({
            'scenario': dict(zip(SCENARIO_FIELDS, scenario)),
            'category': category,
            'metric_name': metric_name,
            'aggregation': aggregation,
            'source_unit': source_unit,
            'adverse_direction': direction,
            'effect_unit': effect_unit,
            'practical_threshold': threshold,
            'point_estimate': _clean_float(analysis['point_estimate']),
            'confidence_interval': {
                'lower': _clean_float(lower),
                'upper': _clean_float(upper),
            },
            'variability': _variability(analysis['paired_effects']),
            'observed_classifications': _classification_summary(classifications),
            'paired_effects': paired_effects,
        })

    return {
        'schema_version': CALIBRATION_REPORT_SCHEMA_VERSION,
        'report_type': 'calibration',
        'notice': CALIBRATION_NOTICE,
        'experiment_id': plan.get('experiment_id'),
        'dataset': {
            'sha256': dataset_sha256,
            'experiment_id': plan.get('experiment_id'),
        },
        'target': {
            'target_key': target.get('target_key'),
            'identity': target.get('identity'),
            'verified_image': target.get('verified_image'),
        },
        'streams': {
            label: {
                'label': label,
                'target_key': targets[label].get('target_key'),
                'measured_trials': len(pairs),
            }
            for label in ('reference', 'candidate')
        },
        'configuration': {
            **dict(plan.get('configuration', {})),
            'schedule': {
                key: plan.get('schedule', {}).get(key)
                for key in ('order', 'seed', 'warmup_count', 'measured_repeat_count')
            },
        },
        'environment': {
            'identity': measured_environment,
            'measured_trial_observations': observations,
        },
        'analysis': {
            'method': CALIBRATION_METHOD,
            'confidence_level': float(confidence_level),
            'bootstrap_repeats': bootstrap_repeats,
            'seed': seed,
            'measured_trial_pairs': len(pairs),
            'pairing': 'recorded balanced execution blocks',
            'point_estimator': 'median of measured trials',
            'classification_unit': 'individual paired adverse effect',
        },
        'policy': {
            'practical_thresholds': {
                category: _threshold(category)
                for category in CATEGORIES
            },
            'threshold_recommendations': {
                'status': 'not_generated',
                'reason': (
                    'Measured calibration evidence does not automatically change '
                    'the comparison policy.'
                ),
            },
        },
        'summary': {
            'observed_classifications': _classification_summary(
                all_classifications
            ),
        },
        'kpis': kpis,
    }


def validate_calibration_report(
    report,
    plan,
    trial_records,
    trial_environments,
    measured_environment,
    *,
    confidence_level=DEFAULT_CONFIDENCE_LEVEL,
    bootstrap_repeats=DEFAULT_BOOTSTRAP_REPEATS,
    seed=DEFAULT_SEED,
    dataset_sha256,
):
    """Require calibration output to match deterministic verified evidence."""
    expected = build_calibration_report(
        plan,
        trial_records,
        trial_environments,
        measured_environment,
        confidence_level=confidence_level,
        bootstrap_repeats=bootstrap_repeats,
        seed=seed,
        dataset_sha256=dataset_sha256,
    )
    if report != expected:
        raise CalibrationError(
            'calibration report does not match the verified experiment evidence'
        )
    return report


def _trial_observations(pairs, trial_environments):
    observations = []
    for pair in pairs:
        for stream in ('reference', 'candidate'):
            trial = pair[stream]
            evidence = trial_environments.get(trial['trial_id'])
            if not isinstance(evidence, dict):
                raise CalibrationError(
                    f'measured trial environment is missing: {trial["trial_id"]}'
                )
            observations.append({
                'trial_id': trial['trial_id'],
                'stream': stream,
                'sequence': pair['sequence'],
                'planned_order': trial['planned_order'],
                'captured_at': evidence.get('captured_at'),
                'load_average': evidence.get('observations', {}).get(
                    'load_average'
                ),
                'cpu_temperature_celsius': evidence.get('observations', {}).get(
                    'cpu_temperature_celsius',
                    {},
                ),
            })
    return sorted(observations, key=lambda item: item['planned_order'])


def _threshold(category):
    threshold = CATEGORY_THRESHOLDS[category]
    return {
        'possible': threshold.possible,
        'regression': threshold.regression,
        'unit': 'percentage_points' if category == 'reliability' else 'percent',
    }


def _threshold_classification(effect, category):
    threshold = CATEGORY_THRESHOLDS[category]
    if effect > threshold.regression:
        return REGRESSION
    if effect >= threshold.possible:
        return POSSIBLE_REGRESSION
    return NO_REGRESSION


def _classification_summary(classifications):
    counts = Counter(classifications)
    total = len(classifications)
    possible_or_regression = counts[POSSIBLE_REGRESSION] + counts[REGRESSION]
    return {
        'total': total,
        'no_regression': counts[NO_REGRESSION],
        'possible_regression': counts[POSSIBLE_REGRESSION],
        'regression': counts[REGRESSION],
        'possible_or_regression': possible_or_regression,
        'possible_or_regression_rate': _clean_float(
            possible_or_regression / total if total else 0.0
        ),
    }


def _variability(effects):
    values = tuple(float(value) for value in effects)
    if not values or any(not math.isfinite(value) for value in values):
        raise CalibrationError('paired effects must be finite numeric values')
    return {
        'count': len(values),
        'minimum': _clean_float(min(values)),
        'maximum': _clean_float(max(values)),
        'mean': _clean_float(fmean(values)),
        'median': _clean_float(median(values)),
        'sample_standard_deviation': _clean_float(
            stdev(values) if len(values) > 1 else 0.0
        ),
    }


def _clean_float(value):
    cleaned = round(float(value), 12)
    return 0.0 if cleaned == 0.0 else cleaned
