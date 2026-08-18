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
import subprocess

import pytest
import ros2_performance_monitoring.remote_ref as remote_ref


REPOSITORY = 'https://example.test/project.git'


def test_resolves_branch_without_cloning(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return argparse.Namespace(
            returncode=0,
            stdout=f'{"a" * 40}\trefs/heads/rolling\n',
            stderr='',
            args=command,
        )

    monkeypatch.setattr(remote_ref.shutil, 'which', lambda executable: f'/usr/bin/{executable}')
    monkeypatch.setattr(remote_ref.subprocess, 'run', fake_run)

    assert remote_ref.resolve_remote_commit(REPOSITORY, 'rolling') == 'a' * 40
    assert calls == [(
        [
            'git', 'ls-remote', '--exit-code', REPOSITORY,
            'refs/heads/rolling', 'refs/tags/rolling', 'refs/tags/rolling^{}',
        ],
        {'check': False, 'capture_output': True, 'text': True},
    )]


def test_prefers_peeled_annotated_tag(monkeypatch):
    output = (
        f'{"a" * 40}\trefs/tags/release\n'
        f'{"b" * 40}\trefs/tags/release^{{}}\n'
    )
    monkeypatch.setattr(remote_ref.shutil, 'which', lambda _executable: '/usr/bin/git')
    monkeypatch.setattr(
        remote_ref.subprocess,
        'run',
        lambda command, **kwargs: argparse.Namespace(
            returncode=0, stdout=output, stderr='', args=command,
        ),
    )

    assert remote_ref.resolve_remote_commit(REPOSITORY, 'release') == 'b' * 40


def test_rejects_ambiguous_branch_and_tag(monkeypatch):
    output = (
        f'{"a" * 40}\trefs/heads/release\n'
        f'{"b" * 40}\trefs/tags/release\n'
    )
    monkeypatch.setattr(remote_ref.shutil, 'which', lambda _executable: '/usr/bin/git')
    monkeypatch.setattr(
        remote_ref.subprocess,
        'run',
        lambda command, **kwargs: argparse.Namespace(
            returncode=0, stdout=output, stderr='', args=command,
        ),
    )

    with pytest.raises(RuntimeError, match='ambiguous'):
        remote_ref.resolve_remote_commit(REPOSITORY, 'release')


def test_propagates_remote_access_failure(monkeypatch):
    monkeypatch.setattr(remote_ref.shutil, 'which', lambda _executable: '/usr/bin/git')
    monkeypatch.setattr(
        remote_ref.subprocess,
        'run',
        lambda command, **kwargs: argparse.Namespace(
            returncode=128, stdout='', stderr='repository not found', args=command,
        ),
    )

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        remote_ref.resolve_remote_commit(REPOSITORY, 'rolling')

    assert exc_info.value.returncode == 128


def test_full_commit_is_verified_with_a_filtered_temporary_fetch(monkeypatch):
    calls = []
    commit = 'a' * 40

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output = f'{commit}\n' if 'rev-parse' in command else ''
        return argparse.Namespace(returncode=0, stdout=output, stderr='', args=command)

    monkeypatch.setattr(remote_ref.shutil, 'which', lambda _executable: '/usr/bin/git')
    monkeypatch.setattr(remote_ref.subprocess, 'run', fake_run)

    assert remote_ref.resolve_remote_commit(REPOSITORY, commit) == commit
    temporary = calls[0][0][-1]
    assert calls[0][0] == ['git', 'init', '--bare', temporary]
    assert calls[1][0] == [
        'git', '-C', temporary, 'fetch', '--depth=1', '--filter=blob:none',
        '--no-tags', REPOSITORY, commit,
    ]
    assert calls[2][0] == [
        'git', '-C', temporary, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}',
    ]
    assert all(kwargs == {
        'check': True,
        'capture_output': True,
        'text': True,
    } for _, kwargs in calls)
