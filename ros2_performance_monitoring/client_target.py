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

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil
import subprocess

from .controller import resolve_cache_path
from .remote_ref import resolve_remote_commit


DEFAULT_RCLCPP_REPOSITORY = 'https://github.com/ros2/rclcpp.git'
_COMMIT_PATTERN = re.compile(r'[0-9a-fA-F]{7,40}')


@dataclass(frozen=True)
class ClientLibraryTarget:
    """Describe resolved client-library source used by a benchmark image."""

    name: str
    source: str
    repository_url: str | None
    requested_ref: str
    resolved_commit: str
    checkout_path: Path | None = None

    @classmethod
    def packaged(cls, ros_distro: str) -> 'ClientLibraryTarget':
        """Create provenance for the client library supplied by ROS packages."""
        return cls(
            name='rclcpp',
            source='packaged',
            repository_url=None,
            requested_ref=f'ros-{ros_distro}-packages',
            resolved_commit='packaged',
        )


def resolve_rclcpp_target(
    repository_url: str,
    requested_ref: str,
    cache_dir: str,
) -> ClientLibraryTarget:
    """Fetch and resolve one rclcpp ref into an immutable cached checkout."""
    if not repository_url.strip():
        raise ValueError('The rclcpp repository URL cannot be empty')
    if not requested_ref.strip():
        raise ValueError('A non-empty rclcpp ref is required for a source build')
    if requested_ref.startswith('-'):
        raise ValueError(f'Invalid rclcpp ref: {requested_ref!r}')
    if shutil.which('git') is None:
        raise RuntimeError('Git executable was not found on PATH')

    cache_root = _target_cache_root(cache_dir, repository_url)
    mirror_path = cache_root / 'repository.git'
    if mirror_path.exists() and not (mirror_path / 'HEAD').is_file():
        raise RuntimeError(
            f'Cannot use rclcpp cache at {mirror_path}: it is not a Git mirror'
        )
    if not mirror_path.exists():
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['git', 'clone', '--mirror', repository_url, str(mirror_path)],
            check=True,
        )

    subprocess.run(
        [
            'git', '-C', str(mirror_path), 'fetch', '--prune', 'origin',
            '+refs/heads/*:refs/heads/*',
            '+refs/tags/*:refs/tags/*',
        ],
        check=True,
    )
    resolved_commit = _resolve_commit(mirror_path, requested_ref)
    checkout_path = cache_root / 'checkouts' / resolved_commit
    _prepare_checkout(mirror_path, checkout_path, resolved_commit)
    return ClientLibraryTarget(
        name='rclcpp',
        source='build',
        repository_url=repository_url,
        requested_ref=requested_ref,
        resolved_commit=resolved_commit,
        checkout_path=checkout_path,
    )


def resolve_remote_rclcpp_target(
    repository_url: str,
    requested_ref: str,
) -> ClientLibraryTarget:
    """Resolve rclcpp provenance without creating a persistent checkout."""
    return ClientLibraryTarget(
        name='rclcpp',
        source='build',
        repository_url=repository_url,
        requested_ref=requested_ref,
        resolved_commit=resolve_remote_commit(repository_url, requested_ref),
    )


def _target_cache_root(cache_dir: str, repository_url: str) -> Path:
    benchmark_cache = resolve_cache_path(cache_dir)
    managed_cache = benchmark_cache.with_name(f'{benchmark_cache.name}-targets')
    repository_key = hashlib.sha256(repository_url.encode()).hexdigest()[:16]
    return managed_cache / 'rclcpp' / repository_key


def _resolve_commit(mirror_path: Path, requested_ref: str) -> str:
    revision = requested_ref
    if requested_ref.startswith('refs/'):
        candidates = _matching_refs(mirror_path, (requested_ref,))
        if candidates != (requested_ref,):
            raise RuntimeError(f'rclcpp ref {requested_ref!r} was not found')
    elif not _COMMIT_PATTERN.fullmatch(requested_ref) and requested_ref != 'HEAD':
        possible_refs = (
            f'refs/heads/{requested_ref}',
            f'refs/tags/{requested_ref}',
        )
        candidates = _matching_refs(mirror_path, possible_refs)
        if not candidates:
            raise RuntimeError(f'rclcpp ref {requested_ref!r} was not found')
        if len(candidates) > 1:
            joined_candidates = ', '.join(candidates)
            raise RuntimeError(
                f'rclcpp ref {requested_ref!r} is ambiguous: {joined_candidates}'
            )
        revision = candidates[0]

    result = subprocess.run(
        [
            'git', '-C', str(mirror_path), 'rev-parse', '--verify',
            f'{revision}^{{commit}}',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    resolved_commit = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r'[0-9a-f]{40}', resolved_commit):
        raise RuntimeError(
            f'rclcpp ref {requested_ref!r} does not resolve to one full commit SHA'
        )
    return resolved_commit


def _matching_refs(mirror_path: Path, possible_refs: tuple[str, ...]) -> tuple[str, ...]:
    result = subprocess.run(
        [
            'git', '-C', str(mirror_path), 'for-each-ref',
            '--format=%(refname)', *possible_refs,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _prepare_checkout(mirror_path: Path, checkout_path: Path, commit: str) -> None:
    if checkout_path.exists():
        result = _checkout_revision(checkout_path)
        if result.returncode != 0:
            subprocess.run(
                [
                    'git', '-C', str(mirror_path), 'worktree', 'repair',
                    str(checkout_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            result = _checkout_revision(checkout_path)
        if result.returncode != 0 or result.stdout.strip() != commit:
            raise RuntimeError(
                f'Cached rclcpp checkout at {checkout_path} does not match {commit}'
            )
        return

    checkout_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            'git', '-C', str(mirror_path), 'worktree', 'add', '--detach',
            str(checkout_path), commit,
        ],
        check=True,
    )


def _checkout_revision(checkout_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['git', '-C', str(checkout_path), 'rev-parse', '--verify', 'HEAD'],
        check=False,
        capture_output=True,
        text=True,
    )
