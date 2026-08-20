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

import base64
import hashlib
import json
from pathlib import Path

import pytest
import ros2_performance_monitoring.scheduled_comparison as scheduled


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / '.github'
    / 'benchmark-profiles'
    / 'rolling-workflow-smoke-v2.json'
)
REFERENCE_SHA = 'a' * 40
CANDIDATE_SHA = 'b' * 40
BENCHMARK_SHA = '7980edb4781249398a9cf490f73f8985de5cb95a'
DEPENDENCY_SHA = 'c' * 40


class FakeGitHub:

    def __init__(self, parent=REFERENCE_SHA, ahead=1):
        self.parent = parent
        self.ahead = ahead
        self.parent_calls = []
        self.compare_calls = []

    def first_parent(self, repository, candidate):
        self.parent_calls.append((repository, candidate))
        return self.parent

    def commits_ahead(self, repository, reference, candidate):
        self.compare_calls.append((repository, reference, candidate))
        return self.ahead


class FakeResponse:

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.value).encode()


def test_pinned_profile_is_non_authoritative_and_exact():
    profile = scheduled.load_profile(PROFILE_PATH)

    assert profile['name'] == 'rolling-workflow-smoke-v2'
    assert profile['authoritative'] is False
    assert 'not calibrated' in profile['notice']
    assert profile['benchmark_container']['ref'] == BENCHMARK_SHA
    assert profile['source_dependencies'] == {
        'repositories': {
            'ros2/rcl': {
                'type': 'git',
                'url': 'https://github.com/ros2/rcl.git',
                'version': 'rolling',
            },
        },
    }
    assert profile['comparison'] == {
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


def test_github_state_response_is_decoded_without_checkout():
    state = {'schema_version': 1, 'candidate_sha': CANDIDATE_SHA}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    encoded = f'{encoded[:12]}\n{encoded[12:]}'
    requests = []

    def opener(request, timeout):
        requests.append((request.full_url, timeout, request.headers))
        return FakeResponse({'encoding': 'base64', 'content': encoded})

    github = scheduled.GitHubAPI(token='test-token', opener=opener)

    assert github.load_state('owner/repository') == state
    assert requests[0][0].endswith(
        '/repos/owner/repository/contents/'
        '.benchmark-state/rclcpp-last-successful.json?ref=benchmark-state'
    )
    assert requests[0][1] == 30
    assert requests[0][2]['Authorization'] == 'Bearer test-token'


def test_first_run_uses_candidate_parent_when_bootstrap_is_not_configured(
    monkeypatch,
):
    profile = scheduled.load_profile(PROFILE_PATH)
    github = FakeGitHub(ahead=1)
    monkeypatch.setattr(
        scheduled,
        'resolve_remote_commit',
        lambda repository, ref: (
            DEPENDENCY_SHA if repository.endswith('/rcl.git') else CANDIDATE_SHA
        ),
    )

    plan = scheduled.plan_comparison(
        profile,
        state=None,
        bootstrap_sha=None,
        github=github,
        now='2026-08-21T00:00:00Z',
    )

    assert plan == {
        'schema_version': 2,
        'profile': 'rolling-workflow-smoke-v2',
        'discovered_at': '2026-08-21T00:00:00Z',
        'skip': False,
        'skip_reason': None,
        'reference_sha': REFERENCE_SHA,
        'candidate_sha': CANDIDATE_SHA,
        'baseline_source': 'candidate-first-parent',
        'missed_commit_count': 1,
        'source_dependencies': _exact_dependencies(),
    }
    assert github.parent_calls == [('ros2/rclcpp', CANDIDATE_SHA)]


def test_first_run_uses_and_verifies_explicit_bootstrap(monkeypatch):
    profile = scheduled.load_profile(PROFILE_PATH)
    github = FakeGitHub(ahead=4)
    resolved = []

    def resolve(repository, ref):
        resolved.append((repository, ref))
        if repository.endswith('/rcl.git'):
            return DEPENDENCY_SHA
        return CANDIDATE_SHA if ref == 'rolling' else REFERENCE_SHA

    monkeypatch.setattr(scheduled, 'resolve_remote_commit', resolve)

    plan = scheduled.plan_comparison(
        profile,
        state=None,
        bootstrap_sha=REFERENCE_SHA,
        github=github,
    )

    assert plan['reference_sha'] == REFERENCE_SHA
    assert plan['baseline_source'] == 'configured-bootstrap'
    assert plan['missed_commit_count'] == 4
    assert resolved == [
        ('https://github.com/ros2/rclcpp.git', 'rolling'),
        ('https://github.com/ros2/rclcpp.git', REFERENCE_SHA),
        ('https://github.com/ros2/rcl.git', 'rolling'),
    ]
    assert github.parent_calls == []


def test_unchanged_upstream_skips_before_comparison(monkeypatch):
    profile = scheduled.load_profile(PROFILE_PATH)
    state = _state(candidate_sha=CANDIDATE_SHA)
    github = FakeGitHub()
    monkeypatch.setattr(
        scheduled,
        'resolve_remote_commit',
        lambda repository, ref: CANDIDATE_SHA,
    )

    plan = scheduled.plan_comparison(profile, state, None, github)

    assert plan['skip'] is True
    assert plan['skip_reason'] == 'upstream SHA is unchanged'
    assert plan['missed_commit_count'] == 0
    assert plan['source_dependencies'] is None
    assert github.parent_calls == []
    assert github.compare_calls == []


def test_last_successful_coalesces_all_missed_commits_into_one_plan(monkeypatch):
    profile = scheduled.load_profile(PROFILE_PATH)
    github = FakeGitHub(ahead=7)
    monkeypatch.setattr(
        scheduled,
        'resolve_remote_commit',
        lambda repository, ref: (
            DEPENDENCY_SHA if repository.endswith('/rcl.git') else CANDIDATE_SHA
        ),
    )

    plan = scheduled.plan_comparison(
        profile,
        _state(candidate_sha=REFERENCE_SHA),
        None,
        github,
    )

    assert plan['reference_sha'] == REFERENCE_SHA
    assert plan['candidate_sha'] == CANDIDATE_SHA
    assert plan['baseline_source'] == 'last-successful'
    assert plan['missed_commit_count'] == 7
    assert 'commits' not in plan
    assert plan['source_dependencies'] == _exact_dependencies()
    assert github.compare_calls == [('ros2/rclcpp', REFERENCE_SHA, CANDIDATE_SHA)]


@pytest.mark.parametrize('exit_code', (0, 1, 2))
def test_completed_comparison_bundles_validate_and_advance_state(tmp_path, exit_code):
    profile = scheduled.load_profile(PROFILE_PATH)
    evidence = _completed_evidence(tmp_path / 'evidence', profile, exit_code)
    compact = tmp_path / 'compact'

    identity = scheduled.build_bundles(
        evidence,
        compact,
        profile,
        REFERENCE_SHA,
        CANDIDATE_SHA,
        'owner/repository',
        '1234',
        '2',
        _exact_dependencies(),
    )
    full_manifest = scheduled.validate_bundle(evidence, profile)
    compact_manifest = scheduled.validate_bundle(compact, profile)
    state = scheduled.build_state(
        compact,
        profile,
        f'rclcpp-dashboard-{CANDIDATE_SHA}',
    )

    assert identity['comparison_exit_code'] == exit_code
    assert full_manifest['bundle_kind'] == 'full-evidence'
    assert compact_manifest['bundle_kind'] == 'dashboard'
    assert compact_manifest['reference_sha'] == REFERENCE_SHA
    assert compact_manifest['candidate_sha'] == CANDIDATE_SHA
    assert compact_manifest['source_dependencies'] == _exact_dependencies()
    assert compact_manifest['run_ids'] == ['candidate-run', 'reference-run']
    assert compact_manifest['github'] == {
        'repository': 'owner/repository',
        'run_id': '1234',
        'run_attempt': '2',
    }
    assert state['candidate_sha'] == CANDIDATE_SHA
    assert state['comparison']['exit_code'] == exit_code
    assert state['comparison']['artifact'] == f'rclcpp-dashboard-{CANDIDATE_SHA}'
    assert state['comparison']['source_dependencies'] == _exact_dependencies()
    assert not (compact / 'measured_environment.json').exists()


@pytest.mark.parametrize('exit_code', (3, 4))
def test_failed_or_invalid_comparison_never_produces_publishable_bundles(
    tmp_path,
    exit_code,
):
    profile = scheduled.load_profile(PROFILE_PATH)
    evidence = _completed_evidence(tmp_path / 'evidence', profile, exit_code)

    with pytest.raises(
        scheduled.ScheduledComparisonError,
        match='cannot advance state',
    ):
        scheduled.build_bundles(
            evidence,
            tmp_path / 'compact',
            profile,
            REFERENCE_SHA,
            CANDIDATE_SHA,
            'owner/repository',
            '1234',
            '1',
            _exact_dependencies(),
        )

    assert not (evidence / scheduled.MANIFEST_FILENAME).exists()


def test_downloaded_bundle_rejects_tampered_payload(tmp_path):
    profile = scheduled.load_profile(PROFILE_PATH)
    evidence = _completed_evidence(tmp_path / 'evidence', profile, 0)
    compact = tmp_path / 'compact'
    scheduled.build_bundles(
        evidence,
        compact,
        profile,
        REFERENCE_SHA,
        CANDIDATE_SHA,
        'owner/repository',
        '1234',
        '1',
        _exact_dependencies(),
    )
    (compact / 'comparison-report.json').write_text('{}\n', encoding='utf-8')

    with pytest.raises(scheduled.ScheduledComparisonError, match='checksum failed'):
        scheduled.validate_bundle(compact, profile)


def test_downloaded_bundle_rejects_incomplete_checksum_coverage(tmp_path):
    profile = scheduled.load_profile(PROFILE_PATH)
    evidence = _completed_evidence(tmp_path / 'evidence', profile, 0)
    compact = tmp_path / 'compact'
    scheduled.build_bundles(
        evidence,
        compact,
        profile,
        REFERENCE_SHA,
        CANDIDATE_SHA,
        'owner/repository',
        '1234',
        '1',
        _exact_dependencies(),
    )
    checksum_path = compact / scheduled.CHECKSUM_FILENAME
    checksums = checksum_path.read_text(encoding='utf-8').splitlines()
    checksum_path.write_text('\n'.join(checksums[1:]) + '\n', encoding='utf-8')

    with pytest.raises(scheduled.ScheduledComparisonError, match='incomplete coverage'):
        scheduled.validate_bundle(compact, profile)


def test_state_refuses_the_full_evidence_artifact(tmp_path):
    profile = scheduled.load_profile(PROFILE_PATH)
    evidence = _completed_evidence(tmp_path / 'evidence', profile, 0)
    scheduled.build_bundles(
        evidence,
        tmp_path / 'compact',
        profile,
        REFERENCE_SHA,
        CANDIDATE_SHA,
        'owner/repository',
        '1234',
        '1',
        _exact_dependencies(),
    )

    with pytest.raises(scheduled.ScheduledComparisonError, match='dashboard bundle'):
        scheduled.build_state(evidence, profile, 'full-artifact')


def _state(candidate_sha):
    return {
        'schema_version': 2,
        'profile': 'rolling-workflow-smoke-v2',
        'repository': 'https://github.com/ros2/rclcpp.git',
        'upstream_ref': 'rolling',
        'candidate_sha': candidate_sha,
        'comparison': {
            'reference_sha': '0' * 40,
            'exit_code': 0,
            'outcome': 'No regression',
            'experiment_id': 'experiment-previous',
            'run_ids': ['previous-run'],
            'artifact': 'previous-artifact',
            'source_dependencies': _exact_dependencies(),
            'repository': 'owner/repository',
            'run_id': '1000',
            'run_attempt': '1',
        },
        'advanced_at': '2026-08-20T00:00:00Z',
    }


def _completed_evidence(root, profile, exit_code):
    root.mkdir()
    files = {
        'targets/reference.json': {'label': 'reference'},
        'targets/candidate.json': {'label': 'candidate'},
        'measured_environment.json': {'architecture': 'amd64'},
        'experiment.complete.json': {'schema_version': 2},
    }
    for relative, value in files.items():
        _write_json(root / relative, value)
    dataset_path = root / 'dataset' / 'dashboard-data.jsonl'
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        '{"run_id":"reference-run"}\n{"run_id":"candidate-run"}\n',
        encoding='utf-8',
    )
    dataset_manifest = {
        'manifest_version': 2,
        'dataset': 'dashboard-data.jsonl',
        'dataset_sha256': _sha256(dataset_path),
        'inputs': [
            {'path': 'reference.jsonl', 'run_ids': ['reference-run'], 'sha256': 'c' * 64},
            {'path': 'candidate.jsonl', 'run_ids': ['candidate-run'], 'sha256': 'd' * 64},
        ],
    }
    _write_json(root / 'dataset' / 'dashboard-data.manifest.json', dataset_manifest)
    plan = {
        'schema_version': 1,
        'experiment_id': 'experiment-scheduled',
        'configuration': {
            key: profile['comparison'][key]
            for key in ('ros_distro', 'suite', 'executor', 'duration', 'cpuset_cpus')
        },
        'targets': [
            _target('reference', REFERENCE_SHA, profile),
            _target('candidate', CANDIDATE_SHA, profile),
        ],
        'schedule': {
            'warmup_count': profile['comparison']['warmups'],
            'measured_repeat_count': profile['comparison']['repeats'],
            'order': profile['comparison']['order'],
            'seed': profile['comparison']['schedule_seed'],
            'trials': [],
        },
    }
    _write_json(root / 'plan.json', plan)
    report = {
        'schema_version': 2,
        'experiment_id': plan['experiment_id'],
        'analysis': {
            'bootstrap_repeats': profile['comparison']['bootstrap_repeats'],
            'seed': profile['comparison']['bootstrap_seed'],
            'minimum_measured_trials': profile['comparison']['minimum_trials'],
        },
        'overall': {'status': _outcome(exit_code)},
    }
    _write_json(root / 'comparison-report.json', report)
    completion = {
        'schema_version': 2,
        'experiment_id': plan['experiment_id'],
        'plan_sha256': _sha256(root / 'plan.json'),
        'target_manifest_sha256': {
            label: _sha256(root / 'targets' / f'{label}.json')
            for label in ('reference', 'candidate')
        },
        'experiment_completion_sha256': _sha256(root / 'experiment.complete.json'),
        'dataset_sha256': _sha256(dataset_path),
        'dataset_manifest_sha256': _sha256(
            root / 'dataset' / 'dashboard-data.manifest.json'
        ),
        'report_sha256': _sha256(root / 'comparison-report.json'),
        'comparison_exit_code': exit_code,
    }
    _write_json(root / 'comparison.complete.json', completion)
    return root


def _target(label, commit, profile):
    return {
        'label': label,
        'identity': {
            'client_library': {'resolved_commit': commit},
            'benchmark_repository': {
                'url': profile['benchmark_container']['repository'],
                'requested_ref': BENCHMARK_SHA,
                'resolved_commit': BENCHMARK_SHA,
            },
            'source_dependencies': _exact_dependencies(),
        },
    }


def _exact_dependencies():
    return {
        'repositories': {
            'ros2/rcl': {
                'type': 'git',
                'url': 'https://github.com/ros2/rcl.git',
                'version': DEPENDENCY_SHA,
            },
        },
    }


def _outcome(exit_code):
    return {
        0: 'No regression',
        1: 'Regression',
        2: 'Inconclusive',
        3: 'Cannot compare',
        4: 'Operational failure',
    }[exit_code]


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
