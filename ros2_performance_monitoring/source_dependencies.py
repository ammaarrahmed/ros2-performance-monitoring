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

"""Resolve exact vcstool source snapshots used below a client-library target."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

import yaml

from .client_target import resolve_source_checkout
from .controller import resolve_cache_path
from .remote_ref import resolve_remote_commit


_FULL_COMMIT_PATTERN = re.compile(r'[0-9a-f]{40}')


class SourceDependencyError(RuntimeError):
    """Report an invalid or unverifiable exact source dependency snapshot."""


@dataclass(frozen=True)
class SourceDependency:
    """Describe one repository in an exact source dependency snapshot."""

    path: str
    repository_url: str
    requested_ref: str
    resolved_commit: str
    checkout_path: Path | None = None

    def identity_payload(self) -> dict:
        """Return this dependency in the standard exact vcstool shape."""
        return {
            'type': 'git',
            'url': self.repository_url,
            'version': self.resolved_commit,
        }


@dataclass(frozen=True)
class SourceDependencySnapshot:
    """Describe a verified dependency workspace shared by benchmark targets."""

    repositories: tuple[SourceDependency, ...]
    checkout_path: Path | None = None

    def identity_payload(self) -> dict:
        """Return a stable exact vcstool manifest for provenance and hashing."""
        return {
            'repositories': {
                dependency.path: dependency.identity_payload()
                for dependency in self.repositories
            },
        }

    @property
    def snapshot_key(self) -> str:
        """Return the content identity of the exact dependency manifest."""
        return hashlib.sha256(
            _canonical_json(self.identity_payload()).encode()
        ).hexdigest()


def resolve_source_dependency_snapshot(
    manifest_path: str | Path,
    cache_dir: str,
) -> SourceDependencySnapshot:
    """Fetch every exact manifest entry into one managed workspace."""
    dependencies = _load_exact_manifest(manifest_path)
    managed_cache = _managed_cache_root(cache_dir)
    snapshot_key = _snapshot_key(dependencies)
    workspace = managed_cache / 'source-dependencies' / snapshot_key / 'workspace'
    resolved = []
    for dependency in dependencies:
        repository_key = hashlib.sha256(
            dependency.repository_url.encode()
        ).hexdigest()[:16]
        checkout = resolve_source_checkout(
            dependency.repository_url,
            dependency.requested_ref,
            managed_cache / 'source-repositories' / repository_key,
            checkout_path=workspace / dependency.path,
            label=f'source dependency {dependency.path!r}',
        )
        if checkout.resolved_commit != dependency.resolved_commit:
            raise SourceDependencyError(
                f'source dependency {dependency.path!r} resolved to '
                f'{checkout.resolved_commit}, expected {dependency.resolved_commit}'
            )
        resolved.append(SourceDependency(
            path=dependency.path,
            repository_url=dependency.repository_url,
            requested_ref=dependency.requested_ref,
            resolved_commit=checkout.resolved_commit,
            checkout_path=checkout.checkout_path,
        ))
    return SourceDependencySnapshot(tuple(resolved), checkout_path=workspace)


def resolve_remote_source_dependency_snapshot(
    manifest_path: str | Path,
) -> SourceDependencySnapshot:
    """Verify exact dependency commits remotely without creating a checkout."""
    dependencies = _load_exact_manifest(manifest_path)
    resolved = []
    for dependency in dependencies:
        commit = resolve_remote_commit(
            dependency.repository_url,
            dependency.requested_ref,
        )
        if commit != dependency.resolved_commit:
            raise SourceDependencyError(
                f'source dependency {dependency.path!r} resolved to {commit}, '
                f'expected {dependency.resolved_commit}'
            )
        resolved.append(SourceDependency(
            path=dependency.path,
            repository_url=dependency.repository_url,
            requested_ref=dependency.requested_ref,
            resolved_commit=commit,
        ))
    return SourceDependencySnapshot(tuple(resolved))


def _load_exact_manifest(manifest_path: str | Path) -> tuple[SourceDependency, ...]:
    path = Path(manifest_path).expanduser().resolve()
    if not path.is_file():
        raise SourceDependencyError(f'source dependency manifest does not exist: {path}')
    if shutil.which('vcs') is None:
        raise SourceDependencyError('vcstool executable "vcs" was not found on PATH')
    try:
        manifest = yaml.safe_load(path.read_text(encoding='utf-8'))
        repositories = manifest['repositories']
    except (OSError, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise SourceDependencyError('source dependency manifest is invalid') from exc
    if not isinstance(manifest, dict) or set(manifest) != {'repositories'}:
        raise SourceDependencyError('source dependency manifest has an unsupported shape')
    if not isinstance(repositories, dict) or not repositories:
        raise SourceDependencyError('source dependency manifest has no repositories')

    dependencies = []
    for repository_path, repository in repositories.items():
        path_value = _safe_repository_path(repository_path)
        if not isinstance(repository, dict) or set(repository) != {
            'type', 'url', 'version',
        }:
            raise SourceDependencyError(
                f'source dependency {path_value!r} has an unsupported shape'
            )
        if repository['type'] != 'git':
            raise SourceDependencyError(
                f'source dependency {path_value!r} must use Git'
            )
        repository_url = repository['url']
        version = repository['version']
        if not isinstance(repository_url, str) or not repository_url.strip():
            raise SourceDependencyError(
                f'source dependency {path_value!r} has an invalid URL'
            )
        if repository_url.startswith('-'):
            raise SourceDependencyError(
                f'source dependency {path_value!r} has an invalid URL'
            )
        if not isinstance(version, str) or not _FULL_COMMIT_PATTERN.fullmatch(version):
            raise SourceDependencyError(
                f'source dependency {path_value!r} must use a full lowercase commit SHA'
            )
        dependencies.append(SourceDependency(
            path=path_value,
            repository_url=repository_url,
            requested_ref=version,
            resolved_commit=version,
        ))
    dependencies.sort(key=lambda dependency: dependency.path)
    _validate_vcstool_manifest(repositories)
    return tuple(dependencies)


def _validate_vcstool_manifest(repositories: dict) -> None:
    structural_manifest = {
        'repositories': {
            path: {
                'type': repository['type'],
                'url': repository['url'],
            }
            for path, repository in repositories.items()
        },
    }
    validation = subprocess.run(
        ['vcs', 'validate', '--input', '-'],
        check=False,
        capture_output=True,
        input=_canonical_json(structural_manifest),
        text=True,
    )
    if validation.returncode != 0:
        detail = validation.stderr.strip() or validation.stdout.strip()
        suffix = f': {detail}' if detail else ''
        raise SourceDependencyError(f'source dependency manifest is invalid{suffix}')


def _safe_repository_path(value) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise SourceDependencyError('source dependency path is unsafe')
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in ('', '.', '..', '.git') for part in path.parts
    ):
        raise SourceDependencyError(f'source dependency path is unsafe: {value!r}')
    return value


def _managed_cache_root(cache_dir: str) -> Path:
    benchmark_cache = resolve_cache_path(cache_dir)
    return benchmark_cache.with_name(f'{benchmark_cache.name}-targets')


def _snapshot_key(dependencies: tuple[SourceDependency, ...]) -> str:
    snapshot = SourceDependencySnapshot(dependencies)
    return snapshot.snapshot_key


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
