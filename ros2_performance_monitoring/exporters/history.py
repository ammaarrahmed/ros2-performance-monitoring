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

"""Load an explicitly indexed history of checksum-verified dashboard bundles."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from ros2_performance_monitoring.comparison_report import load_comparison_report
from ros2_performance_monitoring.dataset import validate_normalized_inputs
from ros2_performance_monitoring.dataset import verify_dataset_bundle
from ros2_performance_monitoring.exporters.prometheus import load_records
from ros2_performance_monitoring.scheduled_comparison import CHECKSUM_FILENAME
from ros2_performance_monitoring.scheduled_comparison import MANIFEST_FILENAME
from ros2_performance_monitoring.scheduled_comparison import validate_bundle


HISTORY_SCHEMA_VERSION = 1
MAX_HISTORY_LIMIT = 100
HISTORY_ORDER = 'oldest-first'
REPORT_EVIDENCE = 'statistical-report'
THRESHOLD_EVIDENCE = 'threshold-only'
DATASET_PATH = 'dataset/dashboard-data.jsonl'
DATASET_MANIFEST_PATH = 'dataset/dashboard-data.manifest.json'
REPORT_PATH = 'comparison-report.json'
_SHA256_PATTERN = re.compile(r'[0-9a-f]{64}')
_BUNDLE_ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


class HistoryIndexError(ValueError):
    """Report an unsafe, malformed, unsupported, or inconsistent history."""


@dataclass(frozen=True)
class HistoryBundle:
    """Pair one validated bundle with the metadata exported for its evidence."""

    bundle_id: str
    position: int
    records: list
    comparison_report: object
    evidence: str
    profile: str
    authoritative: bool
    notice: str
    comparison_id: str
    reference_sha: str
    candidate_sha: str


def load_active_history(index_path):
    """Load every active bundle atomically in the index's declared order."""
    path = Path(index_path).expanduser().resolve()
    index = _read_object(path, 'active history index')
    required = {'schema_version', 'order', 'history_limit', 'bundles'}
    if set(index) != required or index['schema_version'] != HISTORY_SCHEMA_VERSION:
        raise HistoryIndexError('active history index has an unsupported shape')
    if index['order'] != HISTORY_ORDER:
        raise HistoryIndexError(f'active history order must be {HISTORY_ORDER!r}')
    limit = index['history_limit']
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_LIMIT:
        raise HistoryIndexError(
            f'active history limit must be between 1 and {MAX_HISTORY_LIMIT}'
        )
    entries = index['bundles']
    if not isinstance(entries, list) or not entries:
        raise HistoryIndexError('active history must list at least one bundle')
    if len(entries) > limit:
        raise HistoryIndexError('active history contains more bundles than its limit')

    seen_ids = set()
    seen_paths = set()
    bundles = []
    for position, entry in enumerate(entries):
        bundle = _load_entry(path.parent, entry, position)
        if bundle.bundle_id in seen_ids:
            raise HistoryIndexError(f'duplicate active bundle ID: {bundle.bundle_id!r}')
        bundle_path = entry['path']
        if bundle_path in seen_paths:
            raise HistoryIndexError(f'duplicate active bundle path: {bundle_path!r}')
        seen_ids.add(bundle.bundle_id)
        seen_paths.add(bundle_path)
        bundles.append(bundle)
    return tuple(bundles)


def _load_entry(index_root, entry, position):
    required = {
        'bundle_id', 'path', 'checksums_sha256', 'evidence', 'profile',
    }
    if not isinstance(entry, dict) or set(entry) != required:
        raise HistoryIndexError(f'active bundle at position {position} has an invalid shape')
    bundle_id = entry['bundle_id']
    if not isinstance(bundle_id, str) or not _BUNDLE_ID_PATTERN.fullmatch(bundle_id):
        raise HistoryIndexError(f'active bundle at position {position} has an invalid ID')
    evidence = entry['evidence']
    if evidence not in (REPORT_EVIDENCE, THRESHOLD_EVIDENCE):
        raise HistoryIndexError(f'active bundle {bundle_id!r} has unsupported evidence')
    expected_checksum = entry['checksums_sha256']
    if not isinstance(expected_checksum, str) or not _SHA256_PATTERN.fullmatch(
        expected_checksum
    ):
        raise HistoryIndexError(f'active bundle {bundle_id!r} has an invalid checksum')
    profile = _validate_profile(entry['profile'], bundle_id)
    root = _relative_path(index_root, entry['path'], f'active bundle {bundle_id!r}')
    if not root.is_dir():
        raise HistoryIndexError(f'active bundle directory does not exist: {root}')
    checksum_path = root / CHECKSUM_FILENAME
    try:
        actual_checksum = _sha256(checksum_path)
    except OSError as exc:
        raise HistoryIndexError(
            f'active bundle {bundle_id!r} checksum manifest is unreadable'
        ) from exc
    if actual_checksum != expected_checksum:
        raise HistoryIndexError(
            f'active bundle {bundle_id!r} checksum manifest does not match the index'
        )
    _validate_checksums(root, bundle_id)

    dataset_path = root / DATASET_PATH
    required_files = {DATASET_PATH, DATASET_MANIFEST_PATH}
    if not all((root / relative).is_file() for relative in required_files):
        raise HistoryIndexError(f'active bundle {bundle_id!r} has no complete dataset')
    try:
        verify_dataset_bundle(dataset_path)
        run_ids = validate_normalized_inputs((dataset_path,))
        records = load_records(dataset_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HistoryIndexError(f'active bundle {bundle_id!r} dataset is invalid: {exc}') from exc

    if evidence == THRESHOLD_EVIDENCE:
        if (root / REPORT_PATH).exists() or (root / MANIFEST_FILENAME).exists():
            raise HistoryIndexError(
                f'threshold-only bundle {bundle_id!r} contains report-backed evidence'
            )
        return HistoryBundle(
            bundle_id=bundle_id,
            position=position,
            records=records,
            comparison_report=None,
            evidence=evidence,
            profile=profile['name'],
            authoritative=profile['authoritative'],
            notice=profile['notice'],
            comparison_id='',
            reference_sha='',
            candidate_sha='',
        )

    if not (root / REPORT_PATH).is_file() or not (root / MANIFEST_FILENAME).is_file():
        raise HistoryIndexError(f'report bundle {bundle_id!r} is incomplete')
    try:
        producer = validate_bundle(root, profile)
        report = load_comparison_report(root / REPORT_PATH, dataset_path, records)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HistoryIndexError(f'report bundle {bundle_id!r} is invalid: {exc}') from exc
    _validate_report_identity(bundle_id, producer, report, run_ids)
    return HistoryBundle(
        bundle_id=bundle_id,
        position=position,
        records=records,
        comparison_report=report,
        evidence=evidence,
        profile=profile['name'],
        authoritative=profile['authoritative'],
        notice=profile['notice'],
        comparison_id=producer['experiment_id'],
        reference_sha=producer['reference_sha'],
        candidate_sha=producer['candidate_sha'],
    )


def _validate_profile(profile, bundle_id):
    required = {'name', 'authoritative', 'notice'}
    if not isinstance(profile, dict) or set(profile) != required:
        raise HistoryIndexError(f'active bundle {bundle_id!r} profile is malformed')
    if (
        not isinstance(profile['name'], str)
        or not profile['name']
        or type(profile['authoritative']) is not bool
        or not isinstance(profile['notice'], str)
        or not profile['notice']
    ):
        raise HistoryIndexError(f'active bundle {bundle_id!r} profile is malformed')
    return profile


def _validate_checksums(root, bundle_id):
    bundle_paths = tuple(root.rglob('*'))
    if any(path.is_symlink() for path in bundle_paths):
        raise HistoryIndexError(f'active bundle {bundle_id!r} contains a symlink')
    try:
        lines = (root / CHECKSUM_FILENAME).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise HistoryIndexError(
            f'active bundle {bundle_id!r} checksum manifest is unreadable'
        ) from exc
    if not lines:
        raise HistoryIndexError(f'active bundle {bundle_id!r} checksum manifest is empty')
    seen = set()
    for line in lines:
        checksum, separator, relative = line.partition('  ')
        if not separator or not _SHA256_PATTERN.fullmatch(checksum) or relative in seen:
            raise HistoryIndexError(
                f'active bundle {bundle_id!r} checksum manifest is malformed'
            )
        path = _relative_path(root, relative, f'active bundle {bundle_id!r} checksum')
        if not path.is_file() or path.is_symlink() or _sha256(path) != checksum:
            raise HistoryIndexError(
                f'active bundle {bundle_id!r} checksum failed for {relative!r}'
            )
        seen.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in bundle_paths
        if path.is_file() and path.name != CHECKSUM_FILENAME
    }
    if seen != actual:
        raise HistoryIndexError(
            f'active bundle {bundle_id!r} checksum manifest has incomplete coverage'
        )


def _validate_report_identity(bundle_id, producer, validated, run_ids):
    report = validated.report
    if producer.get('bundle_kind') != 'dashboard':
        raise HistoryIndexError(f'report bundle {bundle_id!r} is not a compact dashboard bundle')
    if producer.get('experiment_id') != report['experiment_id']:
        raise HistoryIndexError(f'report bundle {bundle_id!r} comparison identity is inconsistent')
    if producer.get('comparison_outcome') != report['overall']['status']:
        raise HistoryIndexError(f'report bundle {bundle_id!r} comparison outcome is inconsistent')
    expected_commits = {
        role: report['targets'][role]['identity']['client_library']['resolved_commit']
        for role in ('reference', 'candidate')
    }
    if (
        producer.get('reference_sha') != expected_commits['reference']
        or producer.get('candidate_sha') != expected_commits['candidate']
    ):
        raise HistoryIndexError(f'report bundle {bundle_id!r} target commits are inconsistent')
    if not set(producer.get('run_ids', ())).issubset(run_ids):
        raise HistoryIndexError(f'report bundle {bundle_id!r} run IDs are inconsistent')


def _relative_path(root, relative, label):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise HistoryIndexError(f'{label} path is unsafe')
    path = root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise HistoryIndexError(f'{label} path escapes its root') from exc
    return resolved


def _read_object(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryIndexError(f'{label} is not valid JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise HistoryIndexError(f'{label} must be a JSON object')
    return value


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
