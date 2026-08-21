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

import io
import json
from pathlib import Path
from urllib.request import Request

import pytest
import ros2_performance_monitoring.github_artifacts as github_artifacts
from ros2_performance_monitoring.github_artifacts import GitHubArtifactClient
from ros2_performance_monitoring.github_artifacts import GitHubArtifactError
from ros2_performance_monitoring.github_artifacts import pull_and_publish_dashboard_bundle
from ros2_performance_monitoring.publication import PublicationResult


class FakeResponse:

    def __init__(self, value):
        self._body = (
            value if isinstance(value, bytes) else json.dumps(value).encode('utf-8')
        )
        self._stream = io.BytesIO(self._body)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size=-1):
        return self._stream.read(size)


def test_latest_completed_run_and_artifact_are_selected_without_exposing_token():
    responses = [
        {'workflow_runs': [
            {'id': 99, 'status': 'completed', 'conclusion': 'failure'},
            {'id': 100, 'status': 'completed', 'conclusion': 'success'},
        ]},
        {'artifacts': [{
            'name': 'rclcpp-dashboard-abc-100',
            'expired': False,
            'archive_download_url': 'https://api.github.test/artifacts/1/zip',
        }]},
    ]
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(responses.pop(0))

    client = GitHubArtifactClient(
        'secret-token',
        api_url='https://api.github.test',
        opener=opener,
    )
    run_id = client.completed_run_id('owner/repository', 'producer.yml')
    artifact = client.artifact('owner/repository', run_id, 'rclcpp-dashboard-')

    assert run_id == '100'
    assert artifact['name'] == 'rclcpp-dashboard-abc-100'
    assert all(
        request.headers['Authorization'] == 'Bearer secret-token'
        for request, _timeout in requests
    )
    assert all('secret-token' not in request.full_url for request, _timeout in requests)


def test_explicit_run_must_be_successfully_completed():
    responses = [
        {'id': 50},
        {
            'id': 100,
            'workflow_id': 50,
            'status': 'completed',
            'conclusion': 'failure',
        },
    ]
    client = GitHubArtifactClient(
        'token',
        opener=lambda request, timeout: FakeResponse(responses.pop(0)),
    )

    with pytest.raises(GitHubArtifactError, match='not successfully completed'):
        client.completed_run_id('owner/repository', 'producer.yml', '100')


def test_token_file_must_have_restricted_permissions(tmp_path):
    token_file = tmp_path / 'github-token'
    token_file.write_text('secret-token\n', encoding='utf-8')
    token_file.chmod(0o644)

    with pytest.raises(GitHubArtifactError, match='0600 or stricter'):
        pull_and_publish_dashboard_bundle(
            'owner/repository',
            'producer.yml',
            'rclcpp-dashboard-',
            token_file,
            tmp_path / 'profile.json',
            tmp_path / 'deployment',
        )

    assert 'secret-token' not in str(_safe_error(token_file, tmp_path))


def test_token_file_symbolic_links_are_rejected(tmp_path):
    target = tmp_path / 'target-token'
    target.write_text('secret-token\n', encoding='utf-8')
    target.chmod(0o600)
    token_file = tmp_path / 'github-token'
    token_file.symlink_to(target)

    with pytest.raises(GitHubArtifactError, match='regular file'):
        pull_and_publish_dashboard_bundle(
            'owner/repository',
            'producer.yml',
            'rclcpp-dashboard-',
            token_file,
            tmp_path / 'profile.json',
            tmp_path / 'deployment',
        )


def test_downloaded_archive_passes_through_the_local_publisher(tmp_path, monkeypatch):
    token_file = tmp_path / 'github-token'
    token_file.write_text('secret-token\n', encoding='utf-8')
    token_file.chmod(0o600)
    received = {}

    class FakeClient:

        def __init__(self, token, api_url):
            assert token == 'secret-token'
            assert api_url == 'https://api.github.com'

        def completed_run_id(self, repository, workflow, run_id):
            assert (repository, workflow, run_id) == (
                'owner/repository', 'producer.yml', None,
            )
            return '100'

        def artifact(self, repository, run_id, prefix):
            assert (repository, run_id, prefix) == (
                'owner/repository', '100', 'rclcpp-dashboard-',
            )
            return {'archive_download_url': 'unused'}

        def download(self, artifact, output_path):
            Path(output_path).write_bytes(b'PK\x05\x06' + b'\x00' * 18)

    def publish(source, profile, deployment, **options):
        received.update({
            'source_exists': Path(source).is_file(),
            'profile': profile,
            'deployment': deployment,
            'options': options,
        })
        return PublicationResult(
            'activated',
            'bundle-id',
            Path(deployment) / 'active-history.json',
            (),
        )

    monkeypatch.setattr(github_artifacts, 'GitHubArtifactClient', FakeClient)
    monkeypatch.setattr(github_artifacts, 'publish_dashboard_bundle', publish)

    result = pull_and_publish_dashboard_bundle(
        'owner/repository',
        'producer.yml',
        'rclcpp-dashboard-',
        token_file,
        tmp_path / 'profile.json',
        tmp_path / 'deployment',
        history_limit=5,
    )

    assert result.outcome == 'activated'
    assert received == {
        'source_exists': True,
        'profile': tmp_path / 'profile.json',
        'deployment': tmp_path / 'deployment',
        'options': {'history_limit': 5},
    }


def test_api_failures_never_include_bearer_credentials():
    def opener(request, timeout):
        raise OSError('transport failed with secret-token')

    client = GitHubArtifactClient('secret-token', opener=opener)

    with pytest.raises(GitHubArtifactError) as caught:
        client.completed_run_id('owner/repository', 'producer.yml')

    assert 'secret-token' not in str(caught.value)


def test_artifact_download_rejects_an_unexpected_origin(tmp_path):
    client = GitHubArtifactClient('secret-token')

    with pytest.raises(GitHubArtifactError, match='unexpected origin'):
        client.download(
            {'archive_download_url': 'https://attacker.invalid/artifact.zip'},
            tmp_path / 'artifact.zip',
        )


def test_cross_origin_redirect_removes_authorization_header():
    request = Request(
        'https://api.github.com/repos/owner/repository/actions/artifacts/1/zip',
        headers={'Authorization': 'Bearer secret-token'},
    )

    redirected = github_artifacts._CredentialSafeRedirect().redirect_request(
        request,
        None,
        302,
        'Found',
        {},
        'https://objects.githubusercontent.com/signed-artifact',
    )

    assert redirected is not None
    assert not redirected.has_header('Authorization')


def _safe_error(token_file, tmp_path):
    try:
        pull_and_publish_dashboard_bundle(
            'owner/repository',
            'producer.yml',
            'rclcpp-dashboard-',
            token_file,
            tmp_path / 'profile.json',
            tmp_path / 'deployment',
        )
    except GitHubArtifactError as exc:
        return exc
    raise AssertionError('expected a token permission error')
