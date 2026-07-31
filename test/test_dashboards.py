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


DASHBOARD_PATH = (
    Path(__file__).resolve().parents[1]
    / 'config'
    / 'grafana'
    / 'dashboards'
    / 'rclcpp_pubsub_overview.json'
)
VARIABLE_PATTERN = re.compile(r'\$(?:\{)?([a-zA-Z_][a-zA-Z0-9_]*)')


def _load_dashboard():
    return json.loads(DASHBOARD_PATH.read_text())


def test_dashboard_panel_ids_are_unique():
    """Test every panel has a unique identifier."""
    dashboard = _load_dashboard()
    panel_ids = [panel['id'] for panel in dashboard['panels']]
    assert len(panel_ids) == len(set(panel_ids))


def test_dashboard_panels_do_not_overlap():
    """Test dashboard grid positions do not occupy the same cells."""
    occupied_cells = set()
    for panel in _load_dashboard()['panels']:
        position = panel['gridPos']
        panel_cells = {
            (x, y)
            for x in range(position['x'], position['x'] + position['w'])
            for y in range(position['y'], position['y'] + position['h'])
        }
        assert occupied_cells.isdisjoint(panel_cells), panel['id']
        occupied_cells.update(panel_cells)


def test_dashboard_variables_are_declared():
    """Test dashboard content only references declared template variables."""
    dashboard = _load_dashboard()
    declared_variables = {
        variable['name']
        for variable in dashboard.get('templating', {}).get('list', [])
    }
    referenced_variables = set(VARIABLE_PATTERN.findall(json.dumps(dashboard)))
    assert referenced_variables <= declared_variables
    assert {'baseline_run', 'candidate_run'} <= declared_variables
