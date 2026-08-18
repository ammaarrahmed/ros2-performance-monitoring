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

import json
from pathlib import Path
import re

from ros2_performance_monitoring.comparison import CATEGORY_THRESHOLDS
from ros2_performance_monitoring.comparison import STATUS_CATEGORIES
from ros2_performance_monitoring.comparison_report import EVIDENCE_STATUS_VALUES


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIRECTORY = REPOSITORY_ROOT / 'config' / 'grafana' / 'dashboards'
DASHBOARD_LINK_PATTERN = re.compile(r'/d/([a-zA-Z0-9_-]+)/')
VARIABLE_PATTERN = re.compile(r'\$(?:\{)?([a-zA-Z_][a-zA-Z0-9_]*)')
GRAFANA_BUILTIN_VARIABLES = {'__field'}
COMPARISON_DIMENSIONS = {
    'comm',
    'executor',
    'node_role',
    'payload_bytes',
    'process_mode',
    'rmw',
    'topology',
}
SINGLE_RUN_SCOPE_VARIABLES = ('library', 'platform', 'ros_distro', 'client_source')
COMPARISON_SCOPE_VARIABLES = (
    'library',
    'platform',
    'baseline_distro',
    'candidate_distro',
    'client_source',
)
COMPARISON_DASHBOARD_UIDS = {
    'rclcpp-pubsub-overview',
    'ros2-comparison-coverage',
    'ros2-regression-overview',
}


def _load_dashboards():
    return {
        path: json.loads(path.read_text())
        for path in sorted(DASHBOARD_DIRECTORY.glob('*.json'))
    }


def _dashboard_by_uid(dashboards, uid):
    return next(
        dashboard
        for dashboard in dashboards.values()
        if dashboard['uid'] == uid
    )


def test_dashboard_uids_and_panel_ids_are_unique():
    """Test provisioned dashboards and their panels have unique identifiers."""
    dashboards = _load_dashboards()
    uids = [dashboard['uid'] for dashboard in dashboards.values()]
    assert len(uids) == len(set(uids))

    for path, dashboard in dashboards.items():
        panel_ids = [panel['id'] for panel in dashboard['panels']]
        assert len(panel_ids) == len(set(panel_ids)), path


def test_dashboard_panels_do_not_overlap():
    """Test dashboard grid positions do not occupy the same cells."""
    for path, dashboard in _load_dashboards().items():
        occupied_cells = set()
        for panel in dashboard['panels']:
            position = panel['gridPos']
            panel_cells = {
                (x, y)
                for x in range(position['x'], position['x'] + position['w'])
                for y in range(position['y'], position['y'] + position['h'])
            }
            assert occupied_cells.isdisjoint(panel_cells), (path, panel['id'])
            occupied_cells.update(panel_cells)


def test_dashboard_auto_refresh_starts_at_five_minutes():
    """Test dashboards cannot select a noisy sub-five-minute refresh rate."""
    allowed_intervals = ['5m', '10m', '30m', '1h', '6h', '12h', '1d']
    for path, dashboard in _load_dashboards().items():
        assert dashboard['refresh'] == '5m', path
        assert dashboard['timepicker']['refresh_intervals'] == allowed_intervals, path


def test_dashboard_variables_are_declared():
    """Test dashboard content only references variables declared in that dashboard."""
    for path, dashboard in _load_dashboards().items():
        declared_variables = {
            variable['name']
            for variable in dashboard.get('templating', {}).get('list', [])
        }
        referenced_variables = set(VARIABLE_PATTERN.findall(json.dumps(dashboard)))
        referenced_variables -= GRAFANA_BUILTIN_VARIABLES
        assert referenced_variables <= declared_variables, (
            path,
            referenced_variables - declared_variables,
        )


def test_dashboard_queries_share_run_scope():
    """Test every dashboard query filters the selected run environment."""
    shared_selectors = {
        'client_library="$library"',
        'platform="$platform"',
        'client_source="$client_source"',
    }
    for path, dashboard in _load_dashboards().items():
        visible_variables = [
            variable['name']
            for variable in dashboard['templating']['list']
            if variable['hide'] == 0
        ]
        if dashboard['uid'] in COMPARISON_DASHBOARD_UIDS:
            assert tuple(visible_variables[:5]) == COMPARISON_SCOPE_VARIABLES, path
        else:
            assert tuple(visible_variables[:4]) == SINGLE_RUN_SCOPE_VARIABLES, path

        for panel in dashboard['panels']:
            for target in panel.get('targets', []):
                expression = target['expr']
                selectors = set(re.findall(r'\w+(?:=|=~)"\\?[^,}]+', expression))
                assert shared_selectors <= selectors, (
                    path,
                    panel['id'],
                )
                if dashboard['uid'] not in COMPARISON_DASHBOARD_UIDS:
                    assert 'ros_distro="$ros_distro"' in selectors
                if '$baseline_run' in expression:
                    expected = (
                        'baseline_distro="$baseline_distro"'
                        if 'ros2_perf_comparison_' in expression
                        else 'ros_distro="$baseline_distro"'
                    )
                    assert expected in selectors
                if '$candidate_run' in expression:
                    expected = (
                        'candidate_distro="$candidate_distro"'
                        if 'ros2_perf_comparison_' in expression
                        else 'ros_distro="$candidate_distro"'
                    )
                    assert expected in selectors


def test_comparison_run_variables_are_scoped_to_their_distributions():
    """Test comparison run selectors only return active, correctly scoped runs."""
    dashboards = _load_dashboards()
    for uid in COMPARISON_DASHBOARD_UIDS:
        dashboard = _dashboard_by_uid(dashboards, uid)
        variables = {
            variable['name']: variable
            for variable in dashboard['templating']['list']
        }
        assert 'ros_distro="$baseline_distro"' in variables['baseline_run']['definition']
        assert 'ros_distro="$candidate_distro"' in variables['candidate_run']['definition']
        assert 'run_id!="$baseline_run"' in variables['candidate_run']['definition']
        for name in ('baseline_run', 'candidate_run'):
            assert variables[name]['definition'].startswith(
                'query_result(max by (run_display,run_id) ('
            )
            assert variables[name]['regex'] == (
                '/.*run_display="(?<text>[^"]+)".*'
                'run_id="(?<value>[^"]+)".*/'
            )


def test_run_detail_selector_only_returns_active_runs():
    """Test stale Prometheus labels are not offered by the run detail view."""
    dashboard = _dashboard_by_uid(_load_dashboards(), 'ros2-run-detail')
    run_variable = next(
        variable
        for variable in dashboard['templating']['list']
        if variable['name'] == 'run'
    )
    assert run_variable['definition'].startswith(
        'query_result(max by (run_display,run_id) ('
    )
    assert run_variable['regex'] == (
        '/.*run_display="(?<text>[^"]+)".*'
        'run_id="(?<value>[^"]+)".*/'
    )


def test_internal_dashboard_links_target_provisioned_uids():
    """Test links between dashboards target another provisioned dashboard."""
    dashboards = _load_dashboards()
    provisioned_uids = {dashboard['uid'] for dashboard in dashboards.values()}

    for path, dashboard in dashboards.items():
        linked_uids = set(DASHBOARD_LINK_PATTERN.findall(json.dumps(dashboard)))
        assert linked_uids <= provisioned_uids, (path, linked_uids - provisioned_uids)


def test_configured_home_dashboard_is_provisioned():
    """Test the Compose home-dashboard path names an existing dashboard file."""
    compose = (REPOSITORY_ROOT / 'compose.dashboard.yml').read_text()
    home_dashboard = re.search(
        r'GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: '
        r'/var/lib/grafana/dashboards/([^\s]+)',
        compose,
    )
    assert home_dashboard is not None
    assert (DASHBOARD_DIRECTORY / home_dashboard.group(1)).is_file()


def test_comparability_uses_complete_scenario_identity():
    """Test set comparisons include every dimension that affects measurements."""
    dashboard = _dashboard_by_uid(
        _load_dashboards(),
        'ros2-comparison-coverage',
    )
    comparison_expressions = [
        target['expr']
        for panel in dashboard['panels']
        for target in panel.get('targets', [])
        if ' unless on ' in target.get('expr', '')
    ]
    assert comparison_expressions
    for expression in comparison_expressions:
        match = re.search(r'unless on \(([^)]+)\)', expression)
        assert match is not None
        assert set(match.group(1).split(',')) == COMPARISON_DIMENSIONS


def test_comparison_dashboards_share_all_kpi_status_panels():
    """Test overview and manual comparison render the shared verdict policy."""
    dashboards = _load_dashboards()
    expected_titles = {
        'overall': 'Overall status',
        'latency': 'Latency',
        'throughput': 'Throughput',
        'resources': 'Resources',
        'reliability': 'Reliability',
    }
    expected_labels = [
        status
        for status, _value in sorted(
            EVIDENCE_STATUS_VALUES.items(),
            key=lambda item: item[1],
        )
    ]
    dashboard_queries = []

    for uid in ('ros2-regression-overview', 'rclcpp-pubsub-overview'):
        dashboard = _dashboard_by_uid(dashboards, uid)
        status_panels = [
            panel for panel in dashboard['panels']
            if panel.get('targets')
            and 'ros2_perf_comparison_status' in panel['targets'][0]['expr']
        ]
        assert len(status_panels) == 5
        assert sum(panel['gridPos']['w'] for panel in status_panels) == 24

        queries = {}
        for panel in status_panels:
            expression = panel['targets'][0]['expr']
            category = re.search(r'category="([^"]+)"', expression).group(1)
            mapping = panel['fieldConfig']['defaults']['mappings'][0]['options']
            assert panel['title'] == expected_titles[category]
            assert [
                mapping[str(index)]['text']
                for index in range(len(EVIDENCE_STATUS_VALUES))
            ] == expected_labels
            assert mapping['6']['color'] == 'purple'
            assert panel['fieldConfig']['defaults']['noValue'] == 'Status unavailable'
            assert 'statistically significant' not in panel['description'].lower()
            assert expression.endswith(' or on() vector(4)')
            queries[category] = expression
        assert tuple(queries) == STATUS_CATEGORIES
        dashboard_queries.append(queries)

    assert dashboard_queries[0] == dashboard_queries[1]


def test_comparison_dashboards_show_report_method_and_selected_category_evidence():
    """Test both comparison views expose report estimates and uncertainty."""
    dashboards = _load_dashboards()
    expected_panels = {
        'Analysis method': ('ros2_perf_comparison_analysis', ()),
        'Measured pairs': ('ros2_perf_comparison_evidence', ('repeat_count',)),
        'Effect estimate': ('ros2_perf_comparison_evidence', ('point_estimate',)),
        'Confidence interval': (
            'ros2_perf_comparison_evidence',
            ('interval_lower', 'interval_upper'),
        ),
        'Practical thresholds': (
            'ros2_perf_comparison_evidence',
            ('possible_threshold', 'regression_threshold'),
        ),
    }
    dashboard_queries = []

    for uid in ('ros2-regression-overview', 'rclcpp-pubsub-overview'):
        dashboard = _dashboard_by_uid(dashboards, uid)
        variables = {
            variable['name']: variable
            for variable in dashboard['templating']['list']
        }
        assert variables['evidence_category']['type'] == 'custom'
        assert variables['evidence_category']['current']['value'] == 'latency'

        queries = {}
        for title, (family, statistics) in expected_panels.items():
            panel = next(panel for panel in dashboard['panels'] if panel['title'] == title)
            expressions = tuple(target['expr'] for target in panel['targets'])
            assert all(family in expression for expression in expressions)
            if statistics:
                assert all('$evidence_category' in expression for expression in expressions)
                assert tuple(
                    re.search(r'statistic="([^"]+)"', expression).group(1)
                    for expression in expressions
                ) == statistics
            queries[title] = expressions
        dashboard_queries.append(queries)

    assert dashboard_queries[0] == dashboard_queries[1]


def test_status_descriptions_and_detail_panels_use_policy_thresholds():
    """Test rendered threshold values cannot drift from the shared policy."""
    dashboards = _load_dashboards()
    overview = _dashboard_by_uid(dashboards, 'ros2-regression-overview')
    manual = _dashboard_by_uid(dashboards, 'rclcpp-pubsub-overview')

    status_panels = {
        panel['title'].lower(): panel
        for panel in overview['panels']
        if panel['title'].lower() in CATEGORY_THRESHOLDS
    }
    for category, policy in CATEGORY_THRESHOLDS.items():
        description = status_panels[category]['description']
        assert f'{policy.possible:g}' in description
        assert f'{policy.regression:g}' in description

    panel_thresholds = (
        (overview, 14, CATEGORY_THRESHOLDS['throughput']),
        (overview, 17, CATEGORY_THRESHOLDS['resources']),
        (overview, 18, CATEGORY_THRESHOLDS['resources']),
        (overview, 20, CATEGORY_THRESHOLDS['reliability']),
        (overview, 28, CATEGORY_THRESHOLDS['latency']),
        (overview, 29, CATEGORY_THRESHOLDS['latency']),
        (overview, 30, CATEGORY_THRESHOLDS['throughput']),
        (overview, 33, CATEGORY_THRESHOLDS['latency']),
        (overview, 34, CATEGORY_THRESHOLDS['throughput']),
        (manual, 5, CATEGORY_THRESHOLDS['latency']),
        (manual, 6, CATEGORY_THRESHOLDS['latency']),
        (manual, 7, CATEGORY_THRESHOLDS['throughput']),
        (manual, 8, CATEGORY_THRESHOLDS['resources']),
        (manual, 9, CATEGORY_THRESHOLDS['resources']),
        (manual, 24, CATEGORY_THRESHOLDS['latency']),
        (manual, 25, CATEGORY_THRESHOLDS['latency']),
        (manual, 26, CATEGORY_THRESHOLDS['throughput']),
    )
    for dashboard, panel_id, policy in panel_thresholds:
        panel = next(panel for panel in dashboard['panels'] if panel['id'] == panel_id)
        steps = panel['fieldConfig']['defaults']['thresholds']['steps']
        assert [step['value'] for step in steps[1:]] == [
            policy.possible,
            policy.regression,
        ]


def test_run_detail_performance_queries_are_scoped_to_workload():
    """Test a run detail never mixes measurements from different workloads."""
    dashboards = _load_dashboards()
    dashboard = _dashboard_by_uid(dashboards, 'ros2-run-detail')
    performance_row = next(
        panel
        for panel in dashboard['panels']
        if panel['title'] == 'Performance profile'
    )
    performance_panels = [
        panel
        for panel in dashboard['panels']
        if panel['gridPos']['y'] > performance_row['gridPos']['y']
    ]
    assert performance_panels
    for panel in performance_panels:
        for target in panel.get('targets', []):
            assert 'topology="$topology"' in target['expr'], panel['title']
