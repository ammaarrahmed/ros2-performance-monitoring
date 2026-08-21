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

from pathlib import Path
import subprocess

import pytest
from ros2_performance_monitoring.source_dependencies import (
    resolve_remote_source_dependency_snapshot,
)
from ros2_performance_monitoring.source_dependencies import resolve_source_dependency_snapshot
from ros2_performance_monitoring.source_dependencies import SourceDependencyError
import yaml


def test_exact_manifest_resolves_to_verified_managed_workspace(tmp_path):
    repository = tmp_path / 'source'
    repository.mkdir()
    _git(repository, 'init')
    _git(repository, 'config', 'user.name', 'Test User')
    _git(repository, 'config', 'user.email', 'test@example.com')
    (repository / 'package.xml').write_text('<package/>\n')
    _git(repository, 'add', 'package.xml')
    _git(repository, 'commit', '-m', 'Initial source')
    commit = _git(repository, 'rev-parse', 'HEAD').stdout.strip()
    manifest = _manifest(
        tmp_path,
        {'ros2/rcl': {'type': 'git', 'url': str(repository), 'version': commit}},
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            'ros2_performance_monitoring.source_dependencies._validate_vcstool_manifest',
            lambda repositories: None,
        )
        snapshot = resolve_source_dependency_snapshot(manifest, str(tmp_path / 'cache'))
        repeated = resolve_source_dependency_snapshot(manifest, str(tmp_path / 'cache'))

    dependency = snapshot.repositories[0]
    assert dependency.path == 'ros2/rcl'
    assert dependency.resolved_commit == commit
    assert dependency.checkout_path == snapshot.checkout_path / 'ros2/rcl'
    assert _git(dependency.checkout_path, 'rev-parse', 'HEAD').stdout.strip() == commit
    assert repeated.snapshot_key == snapshot.snapshot_key
    assert repeated.checkout_path == snapshot.checkout_path
    assert snapshot.identity_payload() == {
        'repositories': {
            'ros2/rcl': {
                'type': 'git',
                'url': str(repository),
                'version': commit,
            },
        },
    }


def test_remote_snapshot_verifies_every_exact_commit(tmp_path, monkeypatch):
    commit = 'a' * 40
    manifest = _manifest(
        tmp_path,
        {'ros2/rcl': {
            'type': 'git',
            'url': 'https://github.com/ros2/rcl.git',
            'version': commit,
        }},
    )
    calls = []
    monkeypatch.setattr(
        'ros2_performance_monitoring.source_dependencies.resolve_remote_commit',
        lambda repository, ref: calls.append((repository, ref)) or commit,
    )

    snapshot = resolve_remote_source_dependency_snapshot(manifest)

    assert calls == [('https://github.com/ros2/rcl.git', commit)]
    assert snapshot.checkout_path is None
    assert snapshot.repositories[0].checkout_path is None


@pytest.mark.parametrize(
    ('repository_path', 'version', 'message'),
    (
        ('../rcl', 'a' * 40, 'path is unsafe'),
        ('/rcl', 'a' * 40, 'path is unsafe'),
        ('ros2\\rcl', 'a' * 40, 'path is unsafe'),
        ('ros2/rcl', 'rolling', 'full lowercase commit SHA'),
        ('ros2/.git/rcl', 'a' * 40, 'path is unsafe'),
    ),
)
def test_manifest_rejects_unsafe_paths_and_moving_refs(
    tmp_path,
    monkeypatch,
    repository_path,
    version,
    message,
):
    manifest = _manifest(
        tmp_path,
        {repository_path: {
            'type': 'git',
            'url': 'https://github.com/ros2/rcl.git',
            'version': version,
        }},
    )
    monkeypatch.setattr(
        'ros2_performance_monitoring.source_dependencies.subprocess.run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )

    with pytest.raises(SourceDependencyError, match=message):
        resolve_remote_source_dependency_snapshot(manifest)


def test_manifest_must_pass_vcstool_validation(tmp_path, monkeypatch):
    manifest = _manifest(
        tmp_path,
        {'ros2/rcl': {
            'type': 'git',
            'url': 'https://github.com/ros2/rcl.git',
            'version': 'a' * 40,
        }},
    )
    monkeypatch.setattr(
        'ros2_performance_monitoring.source_dependencies.subprocess.run',
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout='', stderr='invalid repositories file'
        ),
    )

    with pytest.raises(SourceDependencyError, match='invalid repositories file'):
        resolve_remote_source_dependency_snapshot(manifest)


@pytest.mark.parametrize(
    'repositories',
    (
        {
            1: {
                'type': 'git',
                'url': 'https://github.com/ros2/rcl.git',
                'version': 'a' * 40,
            },
            'ros2/rcl': {
                'type': 'git',
                'url': 'https://github.com/ros2/rcl.git',
                'version': 'a' * 40,
            },
        },
        {
            'ros2/rcl': {
                'type': 'git',
                'url': '--upload-pack=unsafe',
                'version': 'a' * 40,
            },
        },
    ),
)
def test_manifest_rejects_invalid_paths_and_option_like_urls(
    tmp_path,
    monkeypatch,
    repositories,
):
    manifest = _manifest(tmp_path, repositories)
    monkeypatch.setattr(
        'ros2_performance_monitoring.source_dependencies.subprocess.run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )

    with pytest.raises(SourceDependencyError):
        resolve_remote_source_dependency_snapshot(manifest)


def _manifest(tmp_path, repositories):
    path = tmp_path / 'source-dependencies.repos'
    path.write_text(yaml.safe_dump({'repositories': repositories}))
    return path


def _git(repository: Path, *arguments: str):
    return subprocess.run(
        ['git', '-C', str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
