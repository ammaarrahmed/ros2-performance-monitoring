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
COMPARISON_DIMENSIONS = {
    'comm',
    'executor',
    'node_role',
    'payload_bytes',
    'process_mode',
    'rmw',
    'topology',
}
RUN_SCOPE_VARIABLES = ('library', 'platform', 'ros_distro', 'client_source')


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
        assert referenced_variables <= declared_variables, (
            path,
            referenced_variables - declared_variables,
        )


def test_dashboard_queries_share_run_scope():
    """Test every dashboard query filters the selected run environment."""
    selectors = {
        'client_library="$library"',
        'platform="$platform"',
        'ros_distro="$ros_distro"',
        'client_source="$client_source"',
    }
    for path, dashboard in _load_dashboards().items():
        visible_variables = [
            variable['name']
            for variable in dashboard['templating']['list']
            if variable['hide'] == 0
        ]
        assert tuple(visible_variables[:4]) == RUN_SCOPE_VARIABLES, path

        for panel in dashboard['panels']:
            for target in panel.get('targets', []):
                assert selectors <= set(re.findall(r'\w+="\\?[^,}]+', target['expr'])), (
                    path,
                    panel['id'],
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
