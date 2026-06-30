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

import importlib
import sys

import pytest
import ros2_performance_monitoring.cli as cli
from ros2_performance_monitoring.config import RunDefaults

pytestmark = pytest.mark.smoke

DEFAULT_CONTAINER_REPO_URL = 'https://github.com/ammaarrahmed/ros2-benchmark-container'
DEFAULT_CONTAINER_REF = 'gitsubmodule-commit-fix'


def test_run_command_prints_message(monkeypatch, capsys):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', lambda **kwargs: 'abc123')
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'run', '60'])
    cli.main()
    captured = capsys.readouterr()
    assert 'Running Performance Monitor...' in captured.out


def test_doctor_command(monkeypatch, capsys):
    importlib.reload(cli)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'doctor'])
    cli.main()
    captured = capsys.readouterr()
    assert 'Checking environment...' in captured.out


def test_run_with_default_smoke(monkeypatch):
    importlib.reload(cli)
    defaults = RunDefaults()
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return 'abc123'

    def fake_generation_rundata(args, results_dir, commit_hash):
        received['args'] = args
        received['results_dir'] = results_dir
        received['commit_hash'] = commit_hash

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'generation_rundata', fake_generation_rundata)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', str(defaults.duration)],
    )
    cli.main()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': defaults.cache_dir,
    }
    assert received['args'].ros_distro == defaults.ros_distro
    assert received['args'].executor == defaults.executor
    assert received['args'].container_repo_url == DEFAULT_CONTAINER_REPO_URL
    assert received['args'].container_ref == DEFAULT_CONTAINER_REF
    assert received['results_dir'] == defaults.results_dir
    assert received['commit_hash'] == 'abc123'


def test_run_with_explicit_arguments(monkeypatch):
    importlib.reload(cli)
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return 'abc123'

    def fake_generation_rundata(args, results_dir, commit_hash):
        received['args'] = args
        received['results_dir'] = results_dir
        received['commit_hash'] = commit_hash

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'generation_rundata', fake_generation_rundata)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'ros2-performance-monitoring',
            'run',
            '120',
            'rolling',
            'multi-threaded',
            './custom-results',
            '~/.cache/custom-ros2-performance-monitoring',
            DEFAULT_CONTAINER_REPO_URL,
            DEFAULT_CONTAINER_REF,
        ],
    )
    cli.main()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': '~/.cache/custom-ros2-performance-monitoring',
    }
    assert received['args'].duration == 120
    assert received['args'].ros_distro == 'rolling'
    assert received['args'].executor == 'multi-threaded'
    assert received['results_dir'] == './custom-results'
    assert received['commit_hash'] == 'abc123'


def test_run_with_invalid_duration_exits(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', 'not-a-number'],
    )
    with pytest.raises(SystemExit):
        cli.main()
