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
from itertools import product
from types import MappingProxyType

from ros2_performance_monitoring.model import normalize_client_library_source
from ros2_performance_monitoring.model import normalize_platform


NO_REGRESSION = 0
POSSIBLE_REGRESSION = 1
REGRESSION = 2
INCOMPLETE_RESULTS = 3
CANNOT_COMPARE = 4
NOT_APPLICABLE = 5

STATUS_LABELS = MappingProxyType({
    NO_REGRESSION: 'No regression',
    POSSIBLE_REGRESSION: 'Possible regression',
    REGRESSION: 'Regression',
    INCOMPLETE_RESULTS: 'Incomplete results',
    CANNOT_COMPARE: 'Cannot compare',
    NOT_APPLICABLE: 'N/A',
})


@dataclass(frozen=True)
class Thresholds:
    """Define deterministic possible-regression and regression boundaries."""

    possible: float
    regression: float


CATEGORY_THRESHOLDS = MappingProxyType({
    'latency': Thresholds(0.5, 2.0),
    'throughput': Thresholds(0.5, 2.0),
    'resources': Thresholds(1.0, 5.0),
    'reliability': Thresholds(0.01, 0.1),
})
CATEGORIES = ('latency', 'throughput', 'resources', 'reliability')
STATUS_CATEGORIES = ('overall', *CATEGORIES)
COMPARISON_DIMENSIONS = (
    'topology',
    'process_mode',
    'payload_size',
    'rmw_implementation',
    'communication_mode',
    'executor',
    'node_role',
)


@dataclass(frozen=True)
class ComparisonResult:
    """Represent one category status for an ordered pair of runs."""

    baseline_run: str
    candidate_run: str
    baseline_distro: str
    candidate_distro: str
    client_library: str
    client_source: str
    platform: str
    topology: str
    category: str
    status: int


@dataclass(frozen=True)
class _Run:
    run_id: str
    ros_distro: str
    client_library: str
    client_source: str
    platform: str
    records: tuple[dict, ...]

    @property
    def comparison_scope(self):
        return (self.client_library, self.client_source, self.platform)

    @property
    def topologies(self):
        return frozenset(record.get('topology', '') for record in self.records)


def evaluate_comparison(baseline_records, candidate_records, topology):
    """Evaluate all KPI categories for one workload and ordered run pair."""
    baseline = tuple(
        record for record in baseline_records
        if record.get('topology') == topology
    )
    candidate = tuple(
        record for record in candidate_records
        if record.get('topology') == topology
    )
    baseline_scenarios = {_scenario_identity(record) for record in baseline}
    candidate_scenarios = {_scenario_identity(record) for record in candidate}

    if not baseline_scenarios or baseline_scenarios != candidate_scenarios:
        return {category: CANNOT_COMPARE for category in STATUS_CATEGORIES}

    statuses = {
        'latency': _evaluate_category(
            baseline,
            candidate,
            _latency_requirements(baseline_scenarios, topology),
            CATEGORY_THRESHOLDS['latency'],
            _relative_increase,
        ),
        'resources': _evaluate_category(
            baseline,
            candidate,
            _resource_requirements(baseline_scenarios),
            CATEGORY_THRESHOLDS['resources'],
            _relative_increase,
        ),
    }

    if topology == 'pub-sub':
        statuses['throughput'] = _evaluate_category(
            baseline,
            candidate,
            _throughput_requirements(baseline_scenarios),
            CATEGORY_THRESHOLDS['throughput'],
            _relative_decrease,
        )
        statuses['reliability'] = _evaluate_category(
            baseline,
            candidate,
            _reliability_requirements(baseline_scenarios),
            CATEGORY_THRESHOLDS['reliability'],
            _absolute_increase,
        )
    else:
        statuses['throughput'] = NOT_APPLICABLE
        statuses['reliability'] = NOT_APPLICABLE

    ordered_statuses = {category: statuses[category] for category in CATEGORIES}
    applicable = [
        status for status in ordered_statuses.values()
        if status != NOT_APPLICABLE
    ]
    ordered_statuses['overall'] = max(applicable) if applicable else CANNOT_COMPARE
    return {'overall': ordered_statuses.pop('overall'), **ordered_statuses}


def comparison_results(records):
    """Return category statuses for every selectable ordered run pair."""
    runs = _group_runs(records)
    results = []
    for baseline, candidate in product(runs, repeat=2):
        if baseline.comparison_scope != candidate.comparison_scope:
            continue
        topologies = sorted(baseline.topologies | candidate.topologies)
        for topology in topologies:
            if baseline.run_id == candidate.run_id:
                statuses = {
                    category: CANNOT_COMPARE
                    for category in STATUS_CATEGORIES
                }
            else:
                statuses = evaluate_comparison(
                    baseline.records,
                    candidate.records,
                    topology,
                )
            for category in STATUS_CATEGORIES:
                results.append(ComparisonResult(
                    baseline_run=baseline.run_id,
                    candidate_run=candidate.run_id,
                    baseline_distro=baseline.ros_distro,
                    candidate_distro=candidate.ros_distro,
                    client_library=baseline.client_library,
                    client_source=baseline.client_source,
                    platform=baseline.platform,
                    topology=topology,
                    category=category,
                    status=statuses[category],
                ))
    return tuple(results)


def run_display_name(record):
    """Return a selector label that distinguishes measured and aggregate runs."""
    run_id = str(record.get('run_id') or 'unknown')
    if record.get('run_kind', 'measured') != 'aggregate':
        return f'{run_id} (measured)'
    method = str(record.get('aggregation_method') or 'aggregate')
    repeat_count = int(record.get('repeat_count', 1))
    return f'{run_id} ({method}, n={repeat_count})'


def _evaluate_category(baseline, candidate, requirements, thresholds, change):
    if not requirements:
        return INCOMPLETE_RESULTS
    baseline_values = _metric_values(baseline)
    candidate_values = _metric_values(candidate)
    if any(
        requirement not in baseline_values or requirement not in candidate_values
        for requirement in requirements
    ):
        return INCOMPLETE_RESULTS

    worst_change = max(
        change(candidate_values[requirement], baseline_values[requirement])
        for requirement in requirements
    )
    if worst_change >= thresholds.regression:
        return REGRESSION
    if worst_change >= thresholds.possible:
        return POSSIBLE_REGRESSION
    return NO_REGRESSION


def _group_runs(records):
    grouped = {}
    for record in records:
        run_key = (
            str(record.get('run_id') or 'unknown'),
            str(record.get('ros_distro') or 'unknown'),
            str(record.get('client_library') or 'unknown'),
            normalize_client_library_source(
                record.get('client_library_source'),
                record.get('client_library_ref'),
                record.get('client_library_commit'),
            ),
            normalize_platform(record.get('platform')),
        )
        grouped.setdefault(run_key, []).append(record)
    return tuple(
        _Run(*key, tuple(run_records))
        for key, run_records in sorted(grouped.items())
    )


def _scenario_identity(record):
    return tuple(record.get(field, '') for field in COMPARISON_DIMENSIONS)


def _metric_identity(record):
    return (
        _scenario_identity(record),
        record.get('metric_name', ''),
        record.get('aggregation', ''),
    )


def _metric_values(records):
    return {
        _metric_identity(record): float(record.get('numeric_value', 0))
        for record in records
    }


def _measurement_scenarios(scenarios, topology):
    excluded_role = 'publisher' if topology == 'pub-sub' else 'service'
    node_role_index = COMPARISON_DIMENSIONS.index('node_role')
    return {
        scenario for scenario in scenarios
        if scenario[node_role_index] != excluded_role
    }


def _requirements(scenarios, metrics):
    return {
        (scenario, metric_name, aggregation)
        for scenario in scenarios
        for metric_name, aggregation in metrics
    }


def _latency_requirements(scenarios, topology):
    if topology == 'pub-sub':
        metric_name = 'subscription_latency'
    elif topology == 'service':
        metric_name = 'service_client_latency'
    else:
        return set()
    return _requirements(
        _measurement_scenarios(scenarios, topology),
        ((metric_name, 'mean'), (metric_name, 'p95')),
    )


def _throughput_requirements(scenarios):
    return _requirements(
        _measurement_scenarios(scenarios, 'pub-sub'),
        (('subscription_throughput', 'observed'),),
    )


def _resource_requirements(scenarios):
    return _requirements(
        scenarios,
        (
            ('resource_cpu', 'max'),
            ('resource_memory_rss', 'max'),
        ),
    )


def _reliability_requirements(scenarios):
    return _requirements(
        _measurement_scenarios(scenarios, 'pub-sub'),
        tuple(
            (f'total_messages_{name}', 'percent')
            for name in ('lost', 'late', 'too_late')
        ),
    )


def _relative_increase(candidate, baseline):
    return 100.0 * (candidate - baseline) / max(baseline, 0.000001)


def _relative_decrease(candidate, baseline):
    return 100.0 * (baseline - candidate) / max(baseline, 0.000001)


def _absolute_increase(candidate, baseline):
    return candidate - baseline
