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
                    assert 'ros_distro="$baseline_distro"' in selectors
                if '$candidate_run' in expression:
                    assert 'ros_distro="$candidate_distro"' in selectors


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
                'query_result(max by (run_id) ('
            )
            assert variables[name]['regex'] == '/.*run_id="([^"]+)".*/'


def test_run_detail_selector_only_returns_active_runs():
    """Test stale Prometheus labels are not offered by the run detail view."""
    dashboard = _dashboard_by_uid(_load_dashboards(), 'ros2-run-detail')
    run_variable = next(
        variable
        for variable in dashboard['templating']['list']
        if variable['name'] == 'run'
    )
    assert run_variable['definition'].startswith(
        'query_result(max by (run_id) ('
    )
    assert run_variable['regex'] == '/.*run_id="([^"]+)".*/'


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
    dashboards = _load_dashboards()
    for uid in ('ros2-comparison-coverage', 'ros2-regression-overview'):
        dashboard = _dashboard_by_uid(dashboards, uid)
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


def test_overall_verdict_summarizes_and_links_the_worst_scenario():
    """Test the headline works for any run pair and links useful details."""
    dashboard = _dashboard_by_uid(
        _load_dashboards(),
        'ros2-regression-overview',
    )
    verdict_panels = [
        next(panel for panel in dashboard['panels'] if panel['id'] == panel_id)
        for panel_id in (5, 6, 7)
    ]
    verdict, largest_change, scenario = verdict_panels
    mapping = verdict['fieldConfig']['defaults']['mappings'][0]['options']

    assert verdict['title'] == (
        'Candidate ${candidate_run:text} vs reference ${baseline_run:text}'
    )
    assert set(mapping) == {'0', '1', '2', '3'}
    assert [mapping[str(state)]['text'] for state in range(4)] == [
        'No clear regression',
        'Possible regression',
        'Regression detected',
        'Results cannot be compared',
    ]
    for panel in verdict_panels:
        assert panel['fieldConfig']['defaults']['noValue'] == (
            'Select two different comparable runs.'
        )
        assert 'count by (ros_distro,run_id)' in panel['targets'][0]['expr']
    assert verdict['fieldConfig']['defaults']['links'] == []
    assert largest_change['title'] == 'Largest p95 latency increase'
    assert scenario['title'] == 'RMW and scenario most affected'
    assert largest_change['options']['textMode'] == 'value_and_name'
    assert scenario['options']['textMode'] == 'name'
    assert largest_change['fieldConfig']['defaults']['unit'] == 'percent'
    assert 'topk(1,' in largest_change['targets'][0]['expr']
    assert largest_change['targets'][0]['legendFormat'] == '{{rmw}}'
    assert scenario['targets'][0]['legendFormat'] == (
        '{{rmw}} · {{process_mode}} · {{payload_bytes}} B · {{comm}}'
    )
    for panel in (largest_change, scenario):
        link = panel['fieldConfig']['defaults']['links'][0]['url']
        assert '/d/rclcpp-pubsub-overview/' in link
        assert 'var-process_mode=${__field.labels.process_mode}' in link
        assert 'var-payload=${__field.labels.payload_bytes}' in link
        assert 'var-rmw=${__field.labels.rmw}' in link
        assert 'var-comm=${__field.labels.comm}' in link
    assert sum(panel['gridPos']['w'] for panel in verdict_panels) == 24


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
