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
import shutil
import subprocess

import pytest
from ros2_performance_monitoring.client_target import ClientLibraryTarget
from ros2_performance_monitoring.client_target import resolve_rclcpp_target


def test_packaged_target_records_explicit_provenance():
    target = ClientLibraryTarget.packaged('lyrical')

    assert target == ClientLibraryTarget(
        name='rclcpp',
        source='packaged',
        repository_url=None,
        requested_ref='ros-lyrical-packages',
        resolved_commit='packaged',
        checkout_path=None,
    )


def test_branch_tag_and_commit_resolve_to_full_sha(tmp_path):
    remote, commits = _make_remote(tmp_path)

    branch_target = resolve_rclcpp_target(str(remote), 'rolling', str(tmp_path / 'cache'))
    tag_target = resolve_rclcpp_target(str(remote), 'v1.0.0', str(tmp_path / 'cache'))
    commit_target = resolve_rclcpp_target(
        str(remote),
        commits['first'][:12],
        str(tmp_path / 'cache'),
    )

    assert branch_target.resolved_commit == commits['second']
    assert tag_target.resolved_commit == commits['first']
    assert commit_target.resolved_commit == commits['first']
    for target in (branch_target, tag_target, commit_target):
        assert len(target.resolved_commit) == 40
        assert target.checkout_path is not None
        assert target.checkout_path.is_dir()


def test_missing_and_ambiguous_refs_fail(tmp_path):
    remote, _ = _make_remote(tmp_path)
    work = tmp_path / 'work'
    _git(work, 'tag', 'rolling')
    _git(work, 'push', str(remote), 'refs/tags/rolling')

    with pytest.raises(RuntimeError, match="ref 'missing' was not found"):
        resolve_rclcpp_target(str(remote), 'missing', str(tmp_path / 'cache'))

    with pytest.raises(RuntimeError, match="ref 'rolling' is ambiguous"):
        resolve_rclcpp_target(str(remote), 'rolling', str(tmp_path / 'cache'))


def test_cache_fetches_updated_branch_without_changing_resolved_checkout(tmp_path):
    remote, commits = _make_remote(tmp_path)
    first_resolution = resolve_rclcpp_target(
        str(remote),
        'rolling',
        str(tmp_path / 'cache'),
    )

    work = tmp_path / 'work'
    (work / 'source.txt').write_text('third revision\n')
    _git(work, 'add', 'source.txt')
    _git(work, 'commit', '-m', 'Third revision')
    third_commit = _git(work, 'rev-parse', 'HEAD').stdout.strip()
    _git(work, 'push', str(remote), 'rolling')

    second_resolution = resolve_rclcpp_target(
        str(remote),
        'rolling',
        str(tmp_path / 'cache'),
    )

    assert first_resolution.resolved_commit == commits['second']
    assert second_resolution.resolved_commit == third_commit
    assert first_resolution.checkout_path != second_resolution.checkout_path
    assert _git(first_resolution.checkout_path, 'rev-parse', 'HEAD').stdout.strip() == (
        commits['second']
    )


def test_cache_repairs_worktree_links_after_cache_root_moves(tmp_path):
    remote, commits = _make_remote(tmp_path)
    original_cache = tmp_path / 'original' / 'benchmark'
    target = resolve_rclcpp_target(str(remote), 'rolling', str(original_cache))
    original_managed = original_cache.with_name('benchmark-targets')
    moved_cache = tmp_path / 'moved' / 'benchmark'
    moved_managed = moved_cache.with_name('benchmark-targets')
    moved_managed.parent.mkdir()
    shutil.move(original_managed, moved_managed)

    repaired = resolve_rclcpp_target(str(remote), 'rolling', str(moved_cache))

    assert target.resolved_commit == commits['second']
    assert repaired.resolved_commit == commits['second']
    assert moved_managed in repaired.checkout_path.parents
    assert _git(repaired.checkout_path, 'rev-parse', 'HEAD').stdout.strip() == (
        commits['second']
    )


def _make_remote(tmp_path: Path):
    remote = tmp_path / 'remote.git'
    work = tmp_path / 'work'
    _git(tmp_path, 'init', '--bare', str(remote))
    _git(tmp_path, 'init', '-b', 'rolling', str(work))
    _git(work, 'config', 'user.name', 'Test User')
    _git(work, 'config', 'user.email', 'test@example.com')
    (work / 'source.txt').write_text('first revision\n')
    _git(work, 'add', 'source.txt')
    _git(work, 'commit', '-m', 'First revision')
    first_commit = _git(work, 'rev-parse', 'HEAD').stdout.strip()
    _git(work, 'tag', 'v1.0.0')
    (work / 'source.txt').write_text('second revision\n')
    _git(work, 'commit', '-am', 'Second revision')
    second_commit = _git(work, 'rev-parse', 'HEAD').stdout.strip()
    _git(work, 'push', str(remote), 'rolling', '--tags')
    return remote, {'first': first_commit, 'second': second_commit}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
