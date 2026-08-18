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

import re
import shutil
import subprocess


_FULL_COMMIT_PATTERN = re.compile(r'[0-9a-fA-F]{40}')


def resolve_remote_commit(repository_url: str, requested_ref: str) -> str:
    """Resolve a remote branch, tag, HEAD, or full SHA without cloning it."""
    if not repository_url.strip():
        raise ValueError('The repository URL cannot be empty')
    if not requested_ref.strip():
        raise ValueError('A non-empty repository ref is required')
    if requested_ref.startswith('-'):
        raise ValueError(f'Invalid repository ref: {requested_ref!r}')
    if shutil.which('git') is None:
        raise RuntimeError('Git executable was not found on PATH')

    if _FULL_COMMIT_PATTERN.fullmatch(requested_ref):
        _check_remote_access(repository_url)
        return requested_ref.lower()

    if requested_ref == 'HEAD':
        references = _ls_remote(repository_url, ('HEAD',))
        commit = references.get('HEAD')
        if commit is None:
            raise RuntimeError(f'remote HEAD was not found in {repository_url!r}')
        return commit

    if requested_ref.startswith('refs/'):
        patterns = (requested_ref, f'{requested_ref}^{{}}')
        references = _ls_remote(repository_url, patterns)
        commit = _commit_for_ref(references, requested_ref)
        if commit is None:
            raise RuntimeError(f'remote ref {requested_ref!r} was not found')
        return commit

    possible_refs = (
        f'refs/heads/{requested_ref}',
        f'refs/tags/{requested_ref}',
    )
    references = _ls_remote(
        repository_url,
        (*possible_refs, f'{possible_refs[1]}^{{}}'),
    )
    matches = [ref for ref in possible_refs if ref in references]
    if not matches:
        raise RuntimeError(f'remote ref {requested_ref!r} was not found')
    if len(matches) > 1:
        raise RuntimeError(
            f'remote ref {requested_ref!r} is ambiguous: {", ".join(matches)}'
        )
    return _commit_for_ref(references, matches[0])


def _check_remote_access(repository_url: str) -> None:
    subprocess.run(
        ['git', 'ls-remote', '--exit-code', repository_url, 'HEAD'],
        check=True,
        capture_output=True,
        text=True,
    )


def _ls_remote(repository_url: str, patterns: tuple[str, ...]) -> dict[str, str]:
    result = subprocess.run(
        ['git', 'ls-remote', '--exit-code', repository_url, *patterns],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 2):
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    references = {}
    for line in result.stdout.splitlines():
        commit, separator, ref = line.partition('\t')
        if separator and re.fullmatch(r'[0-9a-f]{40}', commit):
            references[ref] = commit
    return references


def _commit_for_ref(references: dict[str, str], ref: str) -> str | None:
    return references.get(f'{ref}^{{}}') or references.get(ref)
