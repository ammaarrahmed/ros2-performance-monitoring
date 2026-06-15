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
import subprocess
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
    monkeypatch.setattr(cli, 'build_container', lambda **kwargs: 'container/path')
    monkeypatch.setattr(cli, 'benchmark_runner', lambda **kwargs: None)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'run', '60'])
    cli.main()
    captured = capsys.readouterr()
    assert 'Running Performance Monitor...' in captured.out


def test_doctor_command(monkeypatch, capsys):
    importlib.reload(cli)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'doctor'])
    cli.main()
    captured = capsys.readouterr()
    assert 'Doctor checks are not implemented yet.' in captured.out


def test_build_container_command(monkeypatch, capsys):
    importlib.reload(cli)
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return 'abc123'

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'build_container', lambda **kwargs: 'container/path')
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'build-container'])
    cli.main()
    captured = capsys.readouterr()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': RunDefaults().cache_dir,
    }
    assert 'Building the container now...' in captured.out
    assert 'Container Repo Loaded is ready now! checked out commit : abc123' in captured.out
    assert 'successfully built container at : container/path' in captured.out


def test_build_container_command_returns_subprocess_error(monkeypatch, capsys):
    importlib.reload(cli)

    def fake_build_container(**kwargs):
        raise subprocess.CalledProcessError(7, ['docker/build', '-d', 'lyrical'])

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', lambda **kwargs: 'abc123')
    monkeypatch.setattr(cli, 'build_container', fake_build_container)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'build-container'])
    assert cli.main() == 7
    captured = capsys.readouterr()
    assert 'successfully built container' not in captured.out
    assert 'Command failed with exit code 7' in captured.err


def test_run_with_default_smoke(monkeypatch):
    importlib.reload(cli)
    defaults = RunDefaults()
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return 'abc123'

    def fake_benchmark_runner(**kwargs):
        received['benchmark_kwargs'] = kwargs

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(cli, 'build_container', lambda **kwargs: 'container/path')
    monkeypatch.setattr(cli, 'benchmark_runner', fake_benchmark_runner)
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
    assert received['benchmark_kwargs'] == {
        'cache_dir': defaults.cache_dir,
        'results_dir': defaults.results_dir,
        'benchmark_option': defaults.default_benchmark,
        'duration': defaults.duration,
        'ros_distro': defaults.ros_distro,
    }


def test_run_with_explicit_arguments(monkeypatch):
    importlib.reload(cli)
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return 'abc123'

    def fake_benchmark_runner(**kwargs):
        received['benchmark_kwargs'] = kwargs

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'build_container', lambda **kwargs: 'container/path')
    monkeypatch.setattr(cli, 'benchmark_runner', fake_benchmark_runner)
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
            '--suite',
            'pubsub-rclcpp-minimal',
        ],
    )
    cli.main()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': '~/.cache/custom-ros2-performance-monitoring',
    }
    assert received['benchmark_kwargs'] == {
        'cache_dir': '~/.cache/custom-ros2-performance-monitoring',
        'results_dir': './custom-results',
        'benchmark_option': 'pubsub-rclcpp-minimal',
        'duration': 120,
        'ros_distro': 'rolling',
    }


def test_run_with_invalid_duration_exits(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', 'not-a-number'],
    )
    with pytest.raises(SystemExit):
        cli.main()
