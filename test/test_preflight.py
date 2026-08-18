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

import argparse

import pytest
import ros2_performance_monitoring.preflight as preflight


def test_preflight_checks_requirements_without_dashboard_ports(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output = str(tmp_path) if command[1] == 'info' else 'Platforms: linux/amd64'
        return argparse.Namespace(returncode=0, stdout=output, stderr='')

    monkeypatch.setattr(preflight.shutil, 'which', lambda executable: f'/usr/bin/{executable}')
    monkeypatch.setattr(preflight.subprocess, 'run', fake_run)
    monkeypatch.setattr(preflight, 'detect_architecture', lambda: 'amd64')
    monkeypatch.setattr(
        preflight.shutil,
        'disk_usage',
        lambda path: argparse.Namespace(free=20 * 1024 ** 3),
    )
    monkeypatch.setattr(
        preflight,
        '_check_port',
        lambda port: pytest.fail(f'port {port} must not be checked'),
    )

    result = preflight.run_comparison_preflight(tmp_path / 'experiment', '0-3')

    assert result.architecture == 'amd64'
    assert commands == [
        ['docker', 'info', '--format', '{{.DockerRootDir}}'],
        ['docker', 'buildx', 'version'],
        ['docker', 'buildx', 'inspect', '--bootstrap'],
    ]


def test_preflight_checks_compose_and_requested_dashboard_ports(tmp_path, monkeypatch):
    commands = []
    ports = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output = str(tmp_path) if command[1] == 'info' else 'Platforms: linux/amd64'
        return argparse.Namespace(returncode=0, stdout=output, stderr='')

    monkeypatch.setattr(preflight.shutil, 'which', lambda executable: f'/usr/bin/{executable}')
    monkeypatch.setattr(preflight.subprocess, 'run', fake_run)
    monkeypatch.setattr(preflight, 'detect_architecture', lambda: 'amd64')
    monkeypatch.setattr(
        preflight.shutil,
        'disk_usage',
        lambda path: argparse.Namespace(free=20 * 1024 ** 3),
    )
    monkeypatch.setattr(preflight, '_check_port', ports.append)

    preflight.run_comparison_preflight(
        tmp_path / 'experiment',
        dashboard_requested=True,
        dashboard_port=9200,
    )

    assert ['docker', 'compose', 'version'] in commands
    assert ports == [3000, 9090, 9200]


def test_preflight_fails_before_subprocesses_when_executable_is_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        preflight.shutil,
        'which',
        lambda executable: None if executable == 'vcs' else f'/usr/bin/{executable}',
    )
    monkeypatch.setattr(
        preflight.subprocess,
        'run',
        lambda *args, **kwargs: pytest.fail('subprocess must not run'),
    )

    with pytest.raises(preflight.PreflightError, match="'vcs'"):
        preflight.run_comparison_preflight(tmp_path / 'experiment')


def test_preflight_reports_invalid_cpuset(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        output = str(tmp_path) if command[1] == 'info' else 'Platforms: linux/amd64'
        return argparse.Namespace(returncode=0, stdout=output, stderr='')

    monkeypatch.setattr(preflight.shutil, 'which', lambda executable: f'/usr/bin/{executable}')
    monkeypatch.setattr(preflight.subprocess, 'run', fake_run)
    monkeypatch.setattr(preflight, 'detect_architecture', lambda: 'amd64')

    with pytest.raises(preflight.PreflightError, match='invalid CPU-set'):
        preflight.run_comparison_preflight(tmp_path / 'experiment', '4-2')


def test_preflight_reports_insufficient_result_disk_space(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        output = str(tmp_path) if command[1] == 'info' else 'Platforms: linux/amd64'
        return argparse.Namespace(returncode=0, stdout=output, stderr='')

    monkeypatch.setattr(preflight.shutil, 'which', lambda executable: f'/usr/bin/{executable}')
    monkeypatch.setattr(preflight.subprocess, 'run', fake_run)
    monkeypatch.setattr(preflight, 'detect_architecture', lambda: 'amd64')
    monkeypatch.setattr(
        preflight.shutil,
        'disk_usage',
        lambda path: argparse.Namespace(free=1024),
    )

    with pytest.raises(preflight.PreflightError, match='Insufficient free space'):
        preflight.run_comparison_preflight(tmp_path / 'experiment')
