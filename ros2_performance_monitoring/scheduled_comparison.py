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

"""Plan and package scheduled latest-versus-last-successful comparisons."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .remote_ref import resolve_remote_commit
from .writers.jsonl import write_json


PROFILE_SCHEMA_VERSION = 2
PLAN_SCHEMA_VERSION = 2
STATE_SCHEMA_VERSION = 2
BUNDLE_SCHEMA_VERSION = 2
STATE_BRANCH = 'benchmark-state'
STATE_PATH = '.benchmark-state/rclcpp-last-successful.json'
MANIFEST_FILENAME = 'producer-manifest.json'
CHECKSUM_FILENAME = 'SHA256SUMS'
COMPLETED_EXIT_CODES = frozenset((0, 1, 2))
_SHA_PATTERN = re.compile(r'[0-9a-f]{40}')
_SHA256_PATTERN = re.compile(r'[0-9a-f]{64}')
_FULL_BUNDLE_FILES = (
    'plan.json',
    'targets/reference.json',
    'targets/candidate.json',
    'measured_environment.json',
    'dataset/dashboard-data.jsonl',
    'dataset/dashboard-data.manifest.json',
    'experiment.complete.json',
    'comparison-report.json',
    'comparison.complete.json',
)
_COMPACT_BUNDLE_FILES = (
    'plan.json',
    'targets/reference.json',
    'targets/candidate.json',
    'dataset/dashboard-data.jsonl',
    'dataset/dashboard-data.manifest.json',
    'experiment.complete.json',
    'comparison-report.json',
    'comparison.complete.json',
)
DASHBOARD_BUNDLE_FILES = frozenset((
    *_COMPACT_BUNDLE_FILES,
    MANIFEST_FILENAME,
    CHECKSUM_FILENAME,
))


class ScheduledComparisonError(RuntimeError):
    """Report an invalid producer profile, plan, state, or bundle."""


class GitHubAPI:
    """Read public commit and durable repository state through GitHub's API."""

    def __init__(self, token=None, api_url='https://api.github.com', opener=None):
        """Configure an authenticated API client with an injectable opener."""
        self.token = token
        self.api_url = api_url.rstrip('/')
        self.opener = opener or urlopen

    def get_json(self, path):
        """Read one GitHub API path and return its decoded JSON value."""
        request = Request(
            f'{self.api_url}/{path.lstrip("/")}',
            headers=self._headers(),
        )
        try:
            with self.opener(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise ScheduledComparisonError(
                f'GitHub API request failed with HTTP {exc.code}: {path}'
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScheduledComparisonError(
                f'GitHub API returned an invalid response for {path}: {exc}'
            ) from exc

    def load_state(self, repository, ref=STATE_BRANCH, path=STATE_PATH):
        """Read the last-successful state file without checking out its branch."""
        encoded_path = quote(path, safe='/')
        response = self.get_json(
            f'repos/{repository}/contents/{encoded_path}?ref={quote(ref, safe="")}'
        )
        if response is None:
            return None
        try:
            if response['encoding'] != 'base64':
                raise ValueError('content is not base64 encoded')
            encoded = ''.join(response['content'].split())
            raw = base64.b64decode(encoded, validate=True)
            return json.loads(raw.decode('utf-8'))
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScheduledComparisonError('last-successful state is invalid') from exc

    def first_parent(self, repository, commit):
        """Return the exact first-parent SHA of a GitHub commit."""
        response = self.get_json(f'repos/{repository}/commits/{commit}')
        parents = response.get('parents', []) if isinstance(response, dict) else []
        if not parents:
            raise ScheduledComparisonError(f'candidate commit {commit} has no first parent')
        parent = parents[0].get('sha')
        return _full_sha(parent, 'candidate first parent')

    def commits_ahead(self, repository, reference, candidate):
        """Return how many commits the candidate is ahead of the reference."""
        response = self.get_json(
            f'repos/{repository}/compare/{reference}...{candidate}'
        )
        if not isinstance(response, dict) or response.get('status') not in (
            'ahead', 'identical',
        ):
            status = response.get('status') if isinstance(response, dict) else None
            raise ScheduledComparisonError(
                f'candidate is not a descendant of the selected baseline: {status!r}'
            )
        ahead_by = response.get('ahead_by')
        if type(ahead_by) is not int or ahead_by < 0:
            raise ScheduledComparisonError('GitHub comparison omitted a valid ahead count')
        return ahead_by

    def _headers(self):
        headers = {
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'ros2-performance-monitoring',
            'X-GitHub-Api-Version': '2022-11-28',
        }
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers


def load_profile(path):
    """Load and strictly validate one immutable producer profile."""
    profile = _read_json(path, 'producer profile')
    required = {
        'schema_version', 'name', 'authoritative', 'notice', 'rclcpp',
        'source_dependencies', 'benchmark_container', 'comparison',
    }
    if set(profile) != required or profile['schema_version'] != PROFILE_SCHEMA_VERSION:
        raise ScheduledComparisonError('producer profile has an unsupported shape')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', profile['name']):
        raise ScheduledComparisonError('producer profile name is invalid')
    if profile['authoritative'] is not False or 'not calibrated' not in profile['notice']:
        raise ScheduledComparisonError(
            'scheduled smoke profiles must explicitly remain non-authoritative'
        )
    _validate_repository(profile['rclcpp'], exact_ref=False, label='rclcpp')
    _validate_source_dependencies_profile(profile['source_dependencies'])
    _validate_repository(
        profile['benchmark_container'],
        exact_ref=True,
        label='benchmark container',
    )
    expected_comparison = {
        'ros_distro': 'rolling',
        'suite': 'rclcpp-minimal',
        'executor': 'EventsCBGExecutor',
        'duration': 1,
        'cpuset_cpus': None,
        'warmups': 0,
        'repeats': 3,
        'order': 'balanced',
        'schedule_seed': 0,
        'bootstrap_repeats': 100,
        'bootstrap_seed': 0,
        'minimum_trials': 3,
    }
    if profile['comparison'] != expected_comparison:
        raise ScheduledComparisonError(
            f'{profile["name"]} comparison settings have changed'
        )
    return profile


def plan_comparison(profile, state, bootstrap_sha, github, now=None):
    """Resolve one latest-versus-last-successful comparison or an early skip."""
    rclcpp = profile['rclcpp']
    candidate = resolve_remote_commit(rclcpp['repository'], rclcpp['ref'])
    upstream_repository = _github_repository(rclcpp['repository'])
    if state is not None:
        _validate_state(state, profile)
        reference = state['candidate_sha']
        if reference == candidate:
            return {
                'schema_version': PLAN_SCHEMA_VERSION,
                'profile': profile['name'],
                'discovered_at': now or _utc_now(),
                'skip': True,
                'skip_reason': 'upstream SHA is unchanged',
                'reference_sha': reference,
                'candidate_sha': candidate,
                'baseline_source': 'last-successful',
                'missed_commit_count': 0,
                'source_dependencies': None,
            }
        baseline_source = 'last-successful'
    elif bootstrap_sha:
        reference = resolve_remote_commit(rclcpp['repository'], bootstrap_sha)
        baseline_source = 'configured-bootstrap'
    else:
        reference = github.first_parent(upstream_repository, candidate)
        baseline_source = 'candidate-first-parent'

    if reference == candidate:
        raise ScheduledComparisonError('reference and candidate SHAs are identical')
    missed_commit_count = github.commits_ahead(
        upstream_repository,
        reference,
        candidate,
    )
    if missed_commit_count < 1:
        raise ScheduledComparisonError('candidate is not ahead of the selected baseline')
    source_dependencies = _resolve_source_dependencies(
        profile['source_dependencies']
    )
    return {
        'schema_version': PLAN_SCHEMA_VERSION,
        'profile': profile['name'],
        'discovered_at': now or _utc_now(),
        'skip': False,
        'skip_reason': None,
        'reference_sha': reference,
        'candidate_sha': candidate,
        'baseline_source': baseline_source,
        'missed_commit_count': missed_commit_count,
        'source_dependencies': source_dependencies,
    }


def build_bundles(
    evidence_dir,
    compact_dir,
    profile,
    reference_sha,
    candidate_sha,
    github_repository,
    run_id,
    run_attempt,
    source_dependencies,
):
    """Validate completed evidence and add self-checking upload metadata."""
    evidence_dir = Path(evidence_dir)
    compact_dir = Path(compact_dir)
    identity = _validate_comparison_evidence(
        evidence_dir,
        profile,
        reference_sha,
        candidate_sha,
        source_dependencies,
    )
    if compact_dir.exists() and any(compact_dir.iterdir()):
        raise ScheduledComparisonError(f'compact bundle is not empty: {compact_dir}')
    compact_dir.mkdir(parents=True, exist_ok=True)
    for relative in _COMPACT_BUNDLE_FILES:
        source = evidence_dir / relative
        destination = compact_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    common = {
        'schema_version': BUNDLE_SCHEMA_VERSION,
        'profile': profile['name'],
        'authoritative': profile['authoritative'],
        'notice': profile['notice'],
        'reference_sha': reference_sha,
        'candidate_sha': candidate_sha,
        'source_dependencies': identity['source_dependencies'],
        'experiment_id': identity['experiment_id'],
        'run_ids': identity['run_ids'],
        'comparison_exit_code': identity['comparison_exit_code'],
        'comparison_outcome': identity['comparison_outcome'],
        'github': {
            'repository': github_repository,
            'run_id': str(run_id),
            'run_attempt': str(run_attempt),
        },
        'created_at': _utc_now(),
    }
    for bundle_root, kind in ((evidence_dir, 'full-evidence'), (compact_dir, 'dashboard')):
        manifest = {**common, 'bundle_kind': kind}
        write_json(manifest, bundle_root / MANIFEST_FILENAME)
        _write_checksums(bundle_root)
        validate_bundle(bundle_root, profile)
    return common


def validate_bundle(bundle_dir, profile):
    """Validate producer metadata and every checksum in a downloaded bundle."""
    root = Path(bundle_dir)
    manifest = _read_json(root / MANIFEST_FILENAME, 'producer manifest')
    required = {
        'schema_version', 'profile', 'authoritative', 'notice', 'reference_sha',
        'candidate_sha', 'experiment_id', 'run_ids', 'comparison_exit_code',
        'comparison_outcome', 'source_dependencies', 'github', 'created_at',
        'bundle_kind',
    }
    if set(manifest) != required or manifest['schema_version'] != BUNDLE_SCHEMA_VERSION:
        raise ScheduledComparisonError('producer manifest has an unsupported shape')
    if manifest['profile'] != profile['name']:
        raise ScheduledComparisonError('bundle profile does not match the pinned profile')
    if manifest['authoritative'] is not False or manifest['notice'] != profile['notice']:
        raise ScheduledComparisonError('bundle lost its non-authoritative notice')
    _full_sha(manifest['reference_sha'], 'bundle reference SHA')
    _full_sha(manifest['candidate_sha'], 'bundle candidate SHA')
    _validate_exact_source_dependencies(
        manifest['source_dependencies'],
        profile['source_dependencies'],
    )
    if manifest['comparison_exit_code'] not in COMPLETED_EXIT_CODES:
        raise ScheduledComparisonError('bundle did not contain a completed comparison')
    checksums = (root / CHECKSUM_FILENAME).read_text(encoding='utf-8').splitlines()
    if not checksums:
        raise ScheduledComparisonError('bundle checksum manifest is empty')
    seen = set()
    for line in checksums:
        checksum, separator, relative = line.partition('  ')
        if not separator or not _SHA256_PATTERN.fullmatch(checksum):
            raise ScheduledComparisonError('bundle checksum manifest is malformed')
        path = _safe_bundle_path(root, relative)
        if relative in seen or not path.is_file() or _sha256(path) != checksum:
            raise ScheduledComparisonError(f'bundle checksum failed for {relative!r}')
        seen.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob('*')
        if path.is_file() and path.name != CHECKSUM_FILENAME
    }
    if seen != actual:
        raise ScheduledComparisonError('bundle checksum manifest has incomplete coverage')
    required_files = (
        _FULL_BUNDLE_FILES
        if manifest['bundle_kind'] == 'full-evidence'
        else _COMPACT_BUNDLE_FILES
        if manifest['bundle_kind'] == 'dashboard'
        else ()
    )
    if not required_files or not set(required_files).issubset(seen):
        raise ScheduledComparisonError('bundle kind or required payload is invalid')
    if MANIFEST_FILENAME not in seen:
        raise ScheduledComparisonError('bundle manifest is not checksum-bound')
    return manifest


def build_state(bundle_dir, profile, artifact_name):
    """Create durable state only from a validated completed comparison bundle."""
    manifest = validate_bundle(bundle_dir, profile)
    if manifest['bundle_kind'] != 'dashboard':
        raise ScheduledComparisonError('state must advance from the dashboard bundle')
    return {
        'schema_version': STATE_SCHEMA_VERSION,
        'profile': profile['name'],
        'repository': profile['rclcpp']['repository'],
        'upstream_ref': profile['rclcpp']['ref'],
        'candidate_sha': manifest['candidate_sha'],
        'comparison': {
            'reference_sha': manifest['reference_sha'],
            'exit_code': manifest['comparison_exit_code'],
            'outcome': manifest['comparison_outcome'],
            'experiment_id': manifest['experiment_id'],
            'run_ids': manifest['run_ids'],
            'artifact': artifact_name,
            'source_dependencies': manifest['source_dependencies'],
            **manifest['github'],
        },
        'advanced_at': _utc_now(),
    }


def _validate_comparison_evidence(
    root,
    profile,
    reference_sha,
    candidate_sha,
    source_dependencies,
):
    expected_dependencies = _validate_exact_source_dependencies(
        source_dependencies,
        profile['source_dependencies'],
    )
    for relative in _FULL_BUNDLE_FILES:
        if not (root / relative).is_file():
            raise ScheduledComparisonError(f'comparison evidence is missing {relative}')
    plan = _read_json(root / 'plan.json', 'comparison plan')
    completion = _read_json(root / 'comparison.complete.json', 'comparison completion')
    dataset_manifest = _read_json(
        root / 'dataset' / 'dashboard-data.manifest.json',
        'dataset manifest',
    )
    report = _read_json(root / 'comparison-report.json', 'comparison report')
    targets = {target.get('label'): target for target in plan.get('targets', [])}
    expected_shas = {'reference': reference_sha, 'candidate': candidate_sha}
    for label, expected_sha in expected_shas.items():
        try:
            identity = targets[label]['identity']
            client = identity['client_library']
            benchmark = identity['benchmark_repository']
        except (KeyError, TypeError) as exc:
            raise ScheduledComparisonError(f'plan is missing the {label} identity') from exc
        if client.get('resolved_commit') != expected_sha:
            raise ScheduledComparisonError(f'plan {label} SHA does not match discovery')
        if identity.get('source_dependencies') != expected_dependencies:
            raise ScheduledComparisonError(
                f'plan {label} source dependencies do not match discovery'
            )
        if benchmark != {
            'url': profile['benchmark_container']['repository'],
            'requested_ref': profile['benchmark_container']['ref'],
            'resolved_commit': profile['benchmark_container']['ref'],
        }:
            raise ScheduledComparisonError('plan benchmark container is not pinned')
    comparison = profile['comparison']
    if plan.get('configuration') != {
        key: comparison[key]
        for key in ('ros_distro', 'suite', 'executor', 'duration', 'cpuset_cpus')
    }:
        raise ScheduledComparisonError('plan configuration differs from the smoke profile')
    schedule = plan.get('schedule', {})
    if any(schedule.get(plan_key) != comparison[profile_key] for plan_key, profile_key in (
        ('warmup_count', 'warmups'),
        ('measured_repeat_count', 'repeats'),
        ('order', 'order'),
        ('seed', 'schedule_seed'),
    )):
        raise ScheduledComparisonError('plan schedule differs from the smoke profile')

    expected_hashes = {
        'plan_sha256': 'plan.json',
        'experiment_completion_sha256': 'experiment.complete.json',
        'dataset_manifest_sha256': 'dataset/dashboard-data.manifest.json',
        'report_sha256': 'comparison-report.json',
    }
    for field, relative in expected_hashes.items():
        if completion.get(field) != _sha256(root / relative):
            raise ScheduledComparisonError(f'comparison completion has invalid {field}')
    target_hashes = completion.get('target_manifest_sha256', {})
    if any(
        target_hashes.get(label) != _sha256(root / 'targets' / f'{label}.json')
        for label in ('reference', 'candidate')
    ):
        raise ScheduledComparisonError('comparison completion has invalid target checksums')
    dataset_sha = _sha256(root / 'dataset' / 'dashboard-data.jsonl')
    if completion.get('dataset_sha256') != dataset_sha:
        raise ScheduledComparisonError('comparison completion has an invalid dataset checksum')
    if dataset_manifest.get('dataset_sha256') != dataset_sha:
        raise ScheduledComparisonError('dataset manifest has an invalid dataset checksum')
    exit_code = completion.get('comparison_exit_code')
    if exit_code not in COMPLETED_EXIT_CODES:
        raise ScheduledComparisonError('comparison exit code cannot advance state')
    if completion.get('experiment_id') != plan.get('experiment_id'):
        raise ScheduledComparisonError('comparison completion experiment ID does not match')
    if report.get('experiment_id') != plan.get('experiment_id'):
        raise ScheduledComparisonError('comparison report experiment ID does not match')
    analysis = report.get('analysis', {})
    if any(analysis.get(report_key) != comparison[profile_key] for report_key, profile_key in (
        ('bootstrap_repeats', 'bootstrap_repeats'),
        ('seed', 'bootstrap_seed'),
        ('minimum_measured_trials', 'minimum_trials'),
    )):
        raise ScheduledComparisonError('comparison analysis differs from the smoke profile')
    run_ids = sorted({
        run_id
        for item in dataset_manifest.get('inputs', [])
        for run_id in item.get('run_ids', [])
    })
    if not run_ids:
        raise ScheduledComparisonError('dataset manifest does not record run IDs')
    try:
        outcome = report['overall']['status']
    except (KeyError, TypeError) as exc:
        raise ScheduledComparisonError('comparison report has no overall outcome') from exc
    return {
        'experiment_id': plan['experiment_id'],
        'run_ids': run_ids,
        'comparison_exit_code': exit_code,
        'comparison_outcome': outcome,
        'source_dependencies': expected_dependencies,
    }


def _validate_state(state, profile):
    required = {
        'schema_version', 'profile', 'repository', 'upstream_ref',
        'candidate_sha', 'comparison', 'advanced_at',
    }
    if set(state) != required or state['schema_version'] != STATE_SCHEMA_VERSION:
        raise ScheduledComparisonError('last-successful state has an unsupported shape')
    if (
        state['profile'] != profile['name']
        or state['repository'] != profile['rclcpp']['repository']
        or state['upstream_ref'] != profile['rclcpp']['ref']
    ):
        raise ScheduledComparisonError('last-successful state belongs to another profile')
    _full_sha(state['candidate_sha'], 'last-successful candidate SHA')
    comparison = state['comparison']
    if (
        not isinstance(comparison, dict)
        or comparison.get('exit_code') not in COMPLETED_EXIT_CODES
    ):
        raise ScheduledComparisonError('last-successful state was not completed')
    _validate_exact_source_dependencies(
        comparison.get('source_dependencies'),
        profile['source_dependencies'],
    )


def _validate_repository(repository, exact_ref, label):
    if set(repository) != {'repository', 'ref'}:
        raise ScheduledComparisonError(f'{label} repository settings are invalid')
    _github_repository(repository['repository'])
    if exact_ref:
        _full_sha(repository['ref'], f'{label} ref')
    elif repository['ref'] != 'rolling':
        raise ScheduledComparisonError('rclcpp producer must follow Rolling')


def _validate_source_dependencies_profile(source_dependencies):
    try:
        repositories = source_dependencies['repositories']
    except (KeyError, TypeError) as exc:
        raise ScheduledComparisonError('source dependency settings are invalid') from exc
    if set(source_dependencies) != {'repositories'} or not isinstance(
        repositories, dict
    ) or not repositories:
        raise ScheduledComparisonError('source dependency settings are invalid')
    for path, repository in repositories.items():
        _safe_repository_path(path)
        if not isinstance(repository, dict) or set(repository) != {
            'type', 'url', 'version',
        }:
            raise ScheduledComparisonError('source dependency settings are invalid')
        if repository['type'] != 'git' or repository['version'] != 'rolling':
            raise ScheduledComparisonError(
                'scheduled source dependencies must follow Rolling Git branches'
            )
        _github_repository(repository['url'])


def _resolve_source_dependencies(source_dependencies):
    repositories = {}
    for path, repository in sorted(source_dependencies['repositories'].items()):
        repositories[path] = {
            'type': 'git',
            'url': repository['url'],
            'version': resolve_remote_commit(
                repository['url'],
                repository['version'],
            ),
        }
    return {'repositories': repositories}


def _validate_exact_source_dependencies(value, configured):
    if not isinstance(value, dict) or set(value) != {'repositories'}:
        raise ScheduledComparisonError('exact source dependency snapshot is invalid')
    repositories = value['repositories']
    configured_repositories = configured['repositories']
    if not isinstance(repositories, dict) or set(repositories) != set(
        configured_repositories
    ):
        raise ScheduledComparisonError('exact source dependency snapshot is invalid')
    for path, repository in repositories.items():
        expected = configured_repositories[path]
        if not isinstance(repository, dict) or set(repository) != {
            'type', 'url', 'version',
        }:
            raise ScheduledComparisonError('exact source dependency snapshot is invalid')
        if repository['type'] != expected['type'] or repository['url'] != expected['url']:
            raise ScheduledComparisonError('exact source dependency snapshot is invalid')
        _full_sha(repository['version'], f'source dependency {path!r} version')
    return value


def _safe_repository_path(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ScheduledComparisonError('source dependency path is unsafe')
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in ('', '.', '..', '.git') for part in path.parts
    ):
        raise ScheduledComparisonError('source dependency path is unsafe')
    return value


def _github_repository(repository_url):
    match = re.fullmatch(
        r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?',
        repository_url,
    )
    if not match:
        raise ScheduledComparisonError(f'unsupported GitHub repository URL: {repository_url}')
    return f'{match.group(1)}/{match.group(2)}'


def _write_checksums(root):
    checksum_path = root / CHECKSUM_FILENAME
    paths = sorted(
        path for path in root.rglob('*')
        if path.is_file() and path != checksum_path
    )
    lines = [f'{_sha256(path)}  {path.relative_to(root).as_posix()}' for path in paths]
    checksum_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _safe_bundle_path(root, relative):
    if not relative or Path(relative).is_absolute():
        raise ScheduledComparisonError('bundle checksum path is unsafe')
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ScheduledComparisonError('bundle checksum path escapes its root') from exc
    return path


def _full_sha(value, label):
    if not isinstance(value, str) or not _SHA_PATTERN.fullmatch(value):
        raise ScheduledComparisonError(f'{label} is not a full lowercase commit SHA')
    return value


def _read_json(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduledComparisonError(f'{label} is not valid JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise ScheduledComparisonError(f'{label} must be a JSON object')
    return value


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _write_github_output(path, values):
    with Path(path).open('a', encoding='utf-8') as output:
        for key, value in values.items():
            print(f'{key}={str(value).lower() if isinstance(value, bool) else value}', file=output)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    discover = subparsers.add_parser('discover')
    discover.add_argument('--profile', required=True)
    discover.add_argument('--github-repository', required=True)
    discover.add_argument('--bootstrap-sha')
    discover.add_argument('--output', required=True)
    discover.add_argument('--github-output')
    bundle = subparsers.add_parser('bundle')
    bundle.add_argument('--profile', required=True)
    bundle.add_argument('--evidence-dir', required=True)
    bundle.add_argument('--compact-dir', required=True)
    bundle.add_argument('--reference-sha', required=True)
    bundle.add_argument('--candidate-sha', required=True)
    bundle.add_argument('--github-repository', required=True)
    bundle.add_argument('--run-id', required=True)
    bundle.add_argument('--run-attempt', required=True)
    bundle.add_argument('--source-dependencies', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('--profile', required=True)
    validate.add_argument('--bundle', required=True)
    state = subparsers.add_parser('state')
    state.add_argument('--profile', required=True)
    state.add_argument('--bundle', required=True)
    state.add_argument('--artifact-name', required=True)
    state.add_argument('--output', required=True)
    return parser


def main(argv=None):
    """Run scheduled comparison discovery, packaging, or validation."""
    args = _parser().parse_args(argv)
    try:
        profile = load_profile(args.profile)
        if args.command == 'discover':
            github = GitHubAPI(token=os.environ.get('GITHUB_TOKEN'))
            state = github.load_state(args.github_repository)
            plan = plan_comparison(profile, state, args.bootstrap_sha, github)
            write_json(plan, args.output)
            if args.github_output:
                github_output = {
                    'should_run': not plan['skip'],
                    'reference_sha': plan['reference_sha'],
                    'candidate_sha': plan['candidate_sha'],
                    'baseline_source': plan['baseline_source'],
                    'missed_commit_count': plan['missed_commit_count'],
                }
                if not plan['skip']:
                    serialized = _canonical_json(plan['source_dependencies'])
                    github_output.update({
                        'source_dependencies_b64': base64.b64encode(
                            serialized.encode()
                        ).decode(),
                        'source_dependencies_sha256': hashlib.sha256(
                            serialized.encode()
                        ).hexdigest(),
                    })
                _write_github_output(args.github_output, github_output)
        elif args.command == 'bundle':
            build_bundles(
                args.evidence_dir,
                args.compact_dir,
                profile,
                args.reference_sha,
                args.candidate_sha,
                args.github_repository,
                args.run_id,
                args.run_attempt,
                _read_json(
                    args.source_dependencies,
                    'exact source dependency snapshot',
                ),
            )
        elif args.command == 'validate':
            manifest = validate_bundle(args.bundle, profile)
            print(json.dumps(manifest, sort_keys=True))
        else:
            state = build_state(args.bundle, profile, args.artifact_name)
            write_json(state, args.output)
    except ScheduledComparisonError as exc:
        print(f'Scheduled comparison error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
