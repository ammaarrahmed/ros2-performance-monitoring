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

"""Publish verified dashboard bundles through an atomic active-history index."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import zipfile

from ros2_performance_monitoring.exporters.history import HISTORY_ORDER
from ros2_performance_monitoring.exporters.history import HISTORY_SCHEMA_VERSION
from ros2_performance_monitoring.exporters.history import load_active_history
from ros2_performance_monitoring.exporters.history import MAX_HISTORY_LIMIT
from ros2_performance_monitoring.exporters.history import REPORT_EVIDENCE
from ros2_performance_monitoring.scheduled_comparison import CHECKSUM_FILENAME
from ros2_performance_monitoring.scheduled_comparison import DASHBOARD_BUNDLE_FILES
from ros2_performance_monitoring.scheduled_comparison import load_profile
from ros2_performance_monitoring.scheduled_comparison import validate_bundle


AUDIT_SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_REPOSITORY_PATTERN = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
_POSITIVE_INTEGER_PATTERN = re.compile(r'[1-9][0-9]*')


class PublicationError(RuntimeError):
    """Report a rejected or failed dashboard publication."""


@dataclass(frozen=True)
class PublicationResult:
    """Describe one activated or idempotently accepted publication."""

    outcome: str
    bundle_id: str
    index_path: Path
    removed_bundle_ids: tuple


def publish_dashboard_bundle(
    source,
    profile_path,
    deployment_root,
    *,
    history_limit=10,
    inactive_retention=20,
    restart_hook=None,
    exporter_health_url='http://127.0.0.1:9108/healthz',
    prometheus_health_url='http://127.0.0.1:9090/-/healthy',
    health_timeout=30.0,
    audit_log=None,
    delete_source=False,
):
    """Validate, activate, health-check, and retain one local dashboard bundle."""
    source_path = Path(source).expanduser().absolute()
    if source_path.is_symlink():
        raise PublicationError('bundle source cannot be a symbolic link')
    source = source_path.resolve()
    deployment_root = Path(deployment_root).expanduser().resolve()
    index_path = deployment_root / 'active-history.json'
    audit_path = (
        Path(audit_log).expanduser().resolve()
        if audit_log is not None
        else deployment_root / 'publication-audit.jsonl'
    )
    _validate_options(
        source,
        deployment_root,
        history_limit,
        inactive_retention,
        health_timeout,
    )
    profile = load_profile(profile_path)
    deployment_root.mkdir(parents=True, exist_ok=True)
    (deployment_root / 'bundles').mkdir(exist_ok=True)
    staging_parent = deployment_root / '.staging'
    staging_parent.mkdir(exist_ok=True)
    _ensure_audit_log(audit_path)

    manifest = None
    identity_validated = False
    bundle_id = ''
    temporary_root = Path(tempfile.mkdtemp(prefix='publish-', dir=staging_parent))
    staged_bundle = temporary_root / 'bundle'
    try:
        _stage_source(source, staged_bundle)
        _validate_bundle_graph(staged_bundle)
        manifest = validate_bundle(staged_bundle, profile)
        _validate_publication_identity(manifest)
        identity_validated = True
        bundle_id = _bundle_id(manifest)
        staged_entry = _history_entry(
            deployment_root,
            staged_bundle,
            bundle_id,
            profile,
        )
        _validate_candidate_history(deployment_root, [staged_entry], history_limit)

        with _publication_lock(deployment_root / '.publish.lock'):
            current = _load_current_index(index_path)
            if current is not None:
                load_active_history(index_path)
            existing = _find_existing_bundle(deployment_root / 'bundles', manifest)
            if existing is None:
                installed = deployment_root / 'bundles' / bundle_id
                if installed.exists():
                    raise PublicationError(
                        f'immutable bundle destination already exists: {bundle_id}'
                    )
                os.replace(staged_bundle, installed)
                _make_bundle_read_only(installed)
                _sync_directory(installed.parent)
            else:
                installed = existing
                bundle_id = installed.name
                existing_manifest = validate_bundle(installed, profile)
                _assert_idempotent_identity(existing_manifest, manifest)
                _validate_bundle_graph(installed)
                _make_bundle_read_only(installed)

            entry = _history_entry(deployment_root, installed, bundle_id, profile)
            if current is not None and _entry_is_active(current, entry):
                result = PublicationResult('idempotent', bundle_id, index_path, ())
                _write_audit(
                    audit_path,
                    result.outcome,
                    manifest,
                    bundle_id,
                    trusted_identity=True,
                )
                if delete_source:
                    _delete_source(source, deployment_root)
                return result

            entries = [] if current is None else list(current['bundles'])
            entries = [
                item for item in entries
                if item['bundle_id'] != bundle_id and item['path'] != entry['path']
            ]
            entries.append(entry)
            entries = entries[-history_limit:]
            next_index = _index(entries, history_limit)
            _validate_candidate_history(deployment_root, entries, history_limit)

            previous = index_path.read_bytes() if index_path.exists() else None
            _atomic_write_json(index_path, next_index)
            expected_revision = _sha256(index_path)
            try:
                _run_restart_hook(restart_hook, index_path)
                _wait_for_health(
                    exporter_health_url,
                    prometheus_health_url,
                    expected_revision,
                    health_timeout,
                )
            except Exception as exc:
                _restore_index(index_path, previous)
                rollback_error = _run_rollback_hook(restart_hook, index_path)
                rollback_notice = (
                    f'; rollback hook also failed: {rollback_error}'
                    if rollback_error is not None
                    else ''
                )
                raise PublicationError(
                    'activation health check failed and the active index was '
                    f'rolled back: {exc}{rollback_notice}'
                ) from exc

            result = PublicationResult('activated', bundle_id, index_path, ())
            try:
                _write_audit(
                    audit_path,
                    result.outcome,
                    manifest,
                    bundle_id,
                    trusted_identity=True,
                )
            except OSError as exc:
                _restore_index(index_path, previous)
                _run_rollback_hook(restart_hook, index_path)
                raise PublicationError(
                    'audit write failed and the active index was rolled back'
                ) from exc
            removed = _prune_inactive(
                deployment_root / 'bundles',
                next_index,
                inactive_retention,
            )
            result = PublicationResult('activated', bundle_id, index_path, removed)
            if delete_source:
                _delete_source(source, deployment_root)
            return result
    except Exception as exc:
        if not isinstance(exc, PublicationError):
            exc = PublicationError(str(exc))
        try:
            _write_audit(
                audit_path,
                'failed',
                manifest,
                bundle_id,
                error=str(exc),
                trusted_identity=identity_validated,
            )
        except OSError:
            pass
        raise exc
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _validate_options(
    source,
    deployment_root,
    history_limit,
    inactive_retention,
    health_timeout,
):
    if not source.exists():
        raise PublicationError(f'bundle source does not exist: {source}')
    if type(history_limit) is not int or not 1 <= history_limit <= MAX_HISTORY_LIMIT:
        raise PublicationError(
            f'history limit must be between 1 and {MAX_HISTORY_LIMIT}'
        )
    if type(inactive_retention) is not int or inactive_retention < 0:
        raise PublicationError('inactive retention must be zero or greater')
    if health_timeout <= 0 or health_timeout > 300:
        raise PublicationError('health timeout must be greater than zero and at most 300 seconds')
    if source == deployment_root or deployment_root in source.parents:
        raise PublicationError('bundle source cannot be inside the deployment root')


def _stage_source(source, destination):
    destination.mkdir()
    if source.is_dir():
        _copy_directory(source, destination)
        return
    if not source.is_file() or source.is_symlink():
        raise PublicationError('bundle source must be a regular directory or archive')
    if zipfile.is_zipfile(source):
        _extract_zip(source, destination)
        return
    if tarfile.is_tarfile(source):
        _extract_tar(source, destination)
        return
    raise PublicationError('bundle archive must be ZIP or TAR formatted')


def _copy_directory(source, destination):
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise PublicationError(f'bundle contains an unsafe file: {relative.as_posix()}')
        target = destination / relative
        if path.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def _extract_zip(source, destination):
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        _check_archive_member_count(members)
        prefix = _archive_prefix(member.filename for member in members)
        total = 0
        seen = set()
        for member in members:
            relative = _archive_relative(member.filename, prefix)
            if relative is None:
                continue
            if relative in seen:
                raise PublicationError(f'archive contains duplicate path: {relative}')
            seen.add(relative)
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise PublicationError(f'archive contains an unsafe file: {relative}')
            if member.is_dir():
                (destination / relative).mkdir(parents=True, exist_ok=True)
                continue
            total += member.file_size
            _check_archive_size(total)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source_file, target.open('xb') as output:
                shutil.copyfileobj(source_file, output)


def _extract_tar(source, destination):
    with tarfile.open(source, mode='r:*') as archive:
        members = archive.getmembers()
        _check_archive_member_count(members)
        prefix = _archive_prefix(member.name for member in members)
        total = 0
        seen = set()
        for member in members:
            relative = _archive_relative(member.name, prefix)
            if relative is None:
                continue
            if relative in seen:
                raise PublicationError(f'archive contains duplicate path: {relative}')
            seen.add(relative)
            if not (member.isfile() or member.isdir()):
                raise PublicationError(f'archive contains an unsafe file: {relative}')
            if member.isdir():
                (destination / relative).mkdir(parents=True, exist_ok=True)
                continue
            total += member.size
            _check_archive_size(total)
            source_file = archive.extractfile(member)
            if source_file is None:
                raise PublicationError(f'archive file cannot be read: {relative}')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source_file, target.open('xb') as output:
                shutil.copyfileobj(source_file, output)


def _archive_prefix(names):
    files = [
        PurePosixPath(name.rstrip('/'))
        for name in names
        if name and not name.endswith('/')
    ]
    if any(_unsafe_archive_path(path) for path in files):
        raise PublicationError('archive contains an absolute or traversing path')
    if not files:
        raise PublicationError('bundle archive is empty')
    if all(len(path.parts) > 1 for path in files):
        roots = {path.parts[0] for path in files}
        if len(roots) == 1:
            return next(iter(roots))
    return None


def _archive_relative(name, prefix):
    if not name or '\\' in name:
        raise PublicationError('archive contains an unsafe path')
    path = PurePosixPath(name.rstrip('/'))
    if _unsafe_archive_path(path):
        raise PublicationError('archive contains an absolute or traversing path')
    parts = path.parts
    if prefix is not None:
        if not parts or parts[0] != prefix:
            raise PublicationError('archive has inconsistent top-level paths')
        parts = parts[1:]
    return PurePosixPath(*parts).as_posix() if parts else None


def _unsafe_archive_path(path):
    return path.is_absolute() or not path.parts or any(
        part in ('', '.', '..') for part in path.parts
    )


def _check_archive_size(total):
    if total > MAX_ARCHIVE_BYTES:
        raise PublicationError('bundle archive exceeds the extraction size limit')


def _check_archive_member_count(members):
    maximum = len(DASHBOARD_BUNDLE_FILES) + 4
    if len(members) > maximum:
        raise PublicationError('bundle archive contains too many entries')


def _validate_bundle_graph(root):
    paths = tuple(root.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise PublicationError('bundle contains a symbolic link')
    actual_files = {
        path.relative_to(root).as_posix() for path in paths if path.is_file()
    }
    if actual_files != DASHBOARD_BUNDLE_FILES:
        missing = sorted(DASHBOARD_BUNDLE_FILES - actual_files)
        unexpected = sorted(actual_files - DASHBOARD_BUNDLE_FILES)
        raise PublicationError(
            f'dashboard bundle file graph is invalid; missing={missing}, unexpected={unexpected}'
        )
    expected_directories = {
        PurePosixPath(relative).parent.as_posix()
        for relative in DASHBOARD_BUNDLE_FILES
        if PurePosixPath(relative).parent.as_posix() != '.'
    }
    actual_directories = {
        path.relative_to(root).as_posix() for path in paths if path.is_dir()
    }
    if actual_directories != expected_directories:
        raise PublicationError('dashboard bundle contains unexpected directories')


def _validate_publication_identity(manifest):
    github = manifest.get('github')
    if not isinstance(github, dict) or set(github) != {
        'repository', 'run_id', 'run_attempt',
    }:
        raise PublicationError('producer workflow identity is malformed')
    if not isinstance(github['repository'], str) or not _REPOSITORY_PATTERN.fullmatch(
        github['repository']
    ):
        raise PublicationError('producer repository identity is malformed')
    if not all(
        isinstance(github[field], str)
        and _POSITIVE_INTEGER_PATTERN.fullmatch(github[field])
        for field in ('run_id', 'run_attempt')
    ):
        raise PublicationError('producer workflow run identity is malformed')
    if not isinstance(manifest.get('experiment_id'), str) or not manifest['experiment_id']:
        raise PublicationError('producer comparison identity is malformed')
    run_ids = manifest.get('run_ids')
    if (
        not isinstance(run_ids, list)
        or not run_ids
        or any(not isinstance(run_id, str) or not run_id for run_id in run_ids)
        or len(run_ids) != len(set(run_ids))
    ):
        raise PublicationError('producer run identities are malformed')
    if manifest.get('bundle_kind') != 'dashboard':
        raise PublicationError('only compact dashboard bundles can be published')


def _bundle_id(manifest):
    identity = '|'.join((
        manifest['github']['repository'],
        manifest['github']['run_id'],
        manifest['experiment_id'],
    ))
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return '-'.join((
        manifest['profile'],
        manifest['candidate_sha'][:12],
        manifest['github']['run_id'],
        suffix,
    ))


def _history_entry(deployment_root, bundle, bundle_id, profile):
    return {
        'bundle_id': bundle_id,
        'path': bundle.relative_to(deployment_root).as_posix(),
        'checksums_sha256': _sha256(bundle / CHECKSUM_FILENAME),
        'evidence': REPORT_EVIDENCE,
        'profile': {
            'name': profile['name'],
            'authoritative': profile['authoritative'],
            'notice': profile['notice'],
        },
    }


def _index(entries, history_limit):
    return {
        'schema_version': HISTORY_SCHEMA_VERSION,
        'order': HISTORY_ORDER,
        'history_limit': history_limit,
        'bundles': entries,
    }


def _validate_candidate_history(deployment_root, entries, history_limit):
    candidate = deployment_root / f'.active-history-{uuid.uuid4().hex}.json'
    try:
        _atomic_write_json(candidate, _index(entries, history_limit))
        load_active_history(candidate)
    finally:
        candidate.unlink(missing_ok=True)


def _load_current_index(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationError(f'active history index is invalid: {exc}') from exc
    if not isinstance(value, dict):
        raise PublicationError('active history index must be a JSON object')
    return value


def _find_existing_bundle(bundles_root, manifest):
    workflow_key = (manifest['github']['repository'], manifest['github']['run_id'])
    experiment_id = manifest['experiment_id']
    matches = []
    for path in sorted(bundles_root.iterdir()):
        if not path.is_dir() or path.is_symlink():
            continue
        candidate_path = path / 'producer-manifest.json'
        try:
            candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
            candidate_workflow = (
                candidate['github']['repository'], candidate['github']['run_id'],
            )
        except (OSError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if candidate_workflow == workflow_key or candidate.get('experiment_id') == experiment_id:
            matches.append((path, candidate))
    if len(matches) > 1:
        raise PublicationError('deployment contains ambiguous duplicate publication identities')
    if not matches:
        return None
    _assert_idempotent_identity(matches[0][1], manifest)
    return matches[0][0]


def _assert_idempotent_identity(existing, incoming):
    fields = (
        'profile', 'reference_sha', 'candidate_sha', 'experiment_id', 'run_ids',
        'comparison_exit_code', 'comparison_outcome',
    )
    if any(existing.get(field) != incoming.get(field) for field in fields):
        raise PublicationError('reused workflow or comparison identity has conflicting evidence')


def _entry_is_active(index, entry):
    return any(
        item['bundle_id'] == entry['bundle_id'] and item['path'] == entry['path']
        for item in index['bundles']
    )


@contextmanager
def _publication_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PublicationError('another dashboard publication is already running') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _atomic_write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f'.{path.name}.{uuid.uuid4().hex}.tmp'
    try:
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_index(path, previous):
    if previous is None:
        path.unlink(missing_ok=True)
        _sync_directory(path.parent)
        return
    temporary = path.parent / f'.{path.name}.{uuid.uuid4().hex}.rollback'
    try:
        with temporary.open('xb') as output:
            output.write(previous)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run_restart_hook(hook, index_path):
    if hook is None:
        return
    hook = Path(hook).expanduser().resolve()
    if not hook.is_file() or hook.is_symlink() or not os.access(hook, os.X_OK):
        raise PublicationError(f'restart hook is not an executable regular file: {hook}')
    environment = os.environ.copy()
    environment['ROS2_PERFORMANCE_ACTIVE_HISTORY'] = str(index_path)
    try:
        subprocess.run(
            [str(hook)],
            check=True,
            timeout=60,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublicationError('restart hook timed out') from exc
    except subprocess.CalledProcessError as exc:
        raise PublicationError(
            f'restart hook failed with exit code {exc.returncode}'
        ) from exc


def _run_rollback_hook(hook, index_path):
    try:
        _run_restart_hook(hook, index_path)
    except Exception as exc:
        return exc
    return None


def _wait_for_health(exporter_url, prometheus_url, expected_revision, timeout):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            _check_url(exporter_url, expected_revision)
            _check_url(prometheus_url)
            return
        except PublicationError as exc:
            last_error = exc
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise PublicationError(str(last_error or 'health checks timed out'))


def _check_url(url, expected_revision=None):
    try:
        request = Request(
            url,
            headers={'User-Agent': 'ros2-performance-monitoring-publisher'},
        )
        with urlopen(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise PublicationError(f'health endpoint returned HTTP {response.status}')
            if expected_revision is not None and response.headers.get(
                'X-ROS2-Performance-Source-SHA256'
            ) != expected_revision:
                raise PublicationError('exporter has not loaded the activated history index')
    except HTTPError as exc:
        raise PublicationError(f'health endpoint returned HTTP {exc.code}') from exc
    except ValueError as exc:
        raise PublicationError('health endpoint URL is invalid') from exc
    except (OSError, URLError) as exc:
        raise PublicationError(f'health endpoint is unavailable: {type(exc).__name__}') from exc


def _prune_inactive(bundles_root, index, keep):
    active = {item['path'] for item in index['bundles']}
    inactive = []
    for path in bundles_root.iterdir():
        relative = path.relative_to(bundles_root.parent).as_posix()
        if relative in active or not path.is_dir() or path.is_symlink():
            continue
        manifest = path / 'producer-manifest.json'
        checksums = path / CHECKSUM_FILENAME
        try:
            identity = json.loads(manifest.read_text(encoding='utf-8'))
            accepted_name = _bundle_id(identity)
        except (KeyError, TypeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if checksums.is_file() and accepted_name == path.name:
            inactive.append(path)
    inactive.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    removed = []
    for path in inactive[keep:]:
        _make_bundle_writable(path)
        shutil.rmtree(path)
        removed.append(path.name)
    if removed:
        _sync_directory(bundles_root)
    return tuple(removed)


def _ensure_audit_log(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PublicationError('audit log must be a regular file')
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _write_audit(
    path,
    outcome,
    manifest,
    bundle_id,
    error=None,
    trusted_identity=False,
):
    identity = manifest if trusted_identity and manifest is not None else {}
    record = {
        'schema_version': AUDIT_SCHEMA_VERSION,
        'recorded_at': _utc_now(),
        'outcome': outcome,
        'bundle_id': bundle_id,
        'profile': identity.get('profile', ''),
        'experiment_id': identity.get('experiment_id', ''),
        'reference_sha': identity.get('reference_sha', ''),
        'candidate_sha': identity.get('candidate_sha', ''),
        'github': identity.get('github', {}),
    }
    if error is not None:
        record['error'] = error
    line = json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n'
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as output:
        output.write(line)
        output.flush()
        os.fsync(output.fileno())


def _delete_source(source, deployment_root):
    protected = {Path('/').resolve(), Path.home().resolve(), deployment_root}
    if source in protected or deployment_root in source.parents:
        raise PublicationError(f'refusing to delete protected source path: {source}')
    if source.is_symlink() or source.is_file():
        source.unlink()
    elif source.is_dir():
        shutil.rmtree(source)


def _make_bundle_read_only(root):
    for path in root.rglob('*'):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_bundle_writable(root):
    root.chmod(0o755)
    for path in root.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
