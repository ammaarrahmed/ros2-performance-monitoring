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

"""Download completed GitHub Actions artifacts into the local publisher."""

import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.parse import urlsplit
from urllib.request import build_opener
from urllib.request import HTTPRedirectHandler
from urllib.request import Request

from ros2_performance_monitoring.publication import PublicationError
from ros2_performance_monitoring.publication import publish_dashboard_bundle


_REPOSITORY_PATTERN = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
_RUN_ID_PATTERN = re.compile(r'[1-9][0-9]*')


class GitHubArtifactError(PublicationError):
    """Report a credential, discovery, or artifact download failure."""


class GitHubArtifactClient:
    """Read Actions metadata and archives through the GitHub REST API."""

    def __init__(self, token, api_url='https://api.github.com', opener=None):
        """Configure a credential-safe API client with an injectable opener."""
        self._token = token
        self._api_url = api_url.rstrip('/')
        self._opener = opener or build_opener(_CredentialSafeRedirect()).open

    def completed_run_id(self, repository, workflow, run_id=None):
        """Resolve an explicit or latest successful completed workflow run."""
        if run_id is not None:
            run_id = _valid_run_id(str(run_id))
            workflow_path = quote(workflow, safe='')
            workflow_metadata = self._get_json(
                f'repos/{repository}/actions/workflows/{workflow_path}'
            )
            run = self._get_json(f'repos/{repository}/actions/runs/{run_id}')
            workflow_id = (
                workflow_metadata.get('id')
                if isinstance(workflow_metadata, dict)
                else None
            )
            _require_completed_run(run, run_id, workflow_id)
            return run_id
        workflow_path = quote(workflow, safe='')
        response = self._get_json(
            f'repos/{repository}/actions/workflows/{workflow_path}/runs'
            '?status=completed&per_page=20'
        )
        runs = response.get('workflow_runs') if isinstance(response, dict) else None
        if not isinstance(runs, list):
            raise GitHubArtifactError('GitHub workflow run response is malformed')
        for run in runs:
            if (
                isinstance(run, dict)
                and run.get('status') == 'completed'
                and run.get('conclusion') == 'success'
            ):
                return _valid_run_id(str(run.get('id', '')))
        raise GitHubArtifactError('no successful completed workflow run was found')

    def artifact(self, repository, run_id, name_prefix):
        """Select one unexpired artifact from a completed run by name prefix."""
        response = self._get_json(
            f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100'
        )
        artifacts = response.get('artifacts') if isinstance(response, dict) else None
        if not isinstance(artifacts, list):
            raise GitHubArtifactError('GitHub artifact response is malformed')
        matches = [
            artifact for artifact in artifacts
            if isinstance(artifact, dict)
            and isinstance(artifact.get('name'), str)
            and artifact['name'].startswith(name_prefix)
            and artifact.get('expired') is False
        ]
        if len(matches) != 1:
            raise GitHubArtifactError(
                f'expected one unexpired artifact with prefix {name_prefix!r}, '
                f'found {len(matches)}'
            )
        artifact = matches[0]
        if not isinstance(artifact.get('archive_download_url'), str):
            raise GitHubArtifactError('GitHub artifact omitted its archive download URL')
        return artifact

    def download(self, artifact, output_path):
        """Stream one selected ZIP archive to a local file."""
        download_url = artifact['archive_download_url']
        if _origin(download_url) != _origin(self._api_url):
            raise GitHubArtifactError('GitHub artifact download URL has an unexpected origin')
        request = Request(download_url, headers=self._headers())
        try:
            with self._opener(request, timeout=60) as response:
                with Path(output_path).open('xb') as output:
                    shutil.copyfileobj(response, output)
        except HTTPError as exc:
            raise GitHubArtifactError(
                f'GitHub artifact download failed with HTTP {exc.code}'
            ) from exc
        except OSError as exc:
            raise GitHubArtifactError(
                f'GitHub artifact download failed: {type(exc).__name__}'
            ) from exc

    def _get_json(self, path):
        request = Request(
            f'{self._api_url}/{path.lstrip("/")}',
            headers=self._headers(),
        )
        try:
            with self._opener(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            raise GitHubArtifactError(
                f'GitHub API request failed with HTTP {exc.code}'
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubArtifactError(
                f'GitHub API response is invalid: {type(exc).__name__}'
            ) from exc

    def _headers(self):
        return {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {self._token}',
            'User-Agent': 'ros2-performance-monitoring-publisher',
            'X-GitHub-Api-Version': '2022-11-28',
        }


def pull_and_publish_dashboard_bundle(
    repository,
    workflow,
    artifact_prefix,
    token_file,
    profile_path,
    deployment_root,
    *,
    run_id=None,
    api_url='https://api.github.com',
    **publication_options,
):
    """Download one completed Actions artifact and publish it locally."""
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(repository):
        raise GitHubArtifactError('GitHub repository must use owner/name form')
    if not workflow or not artifact_prefix:
        raise GitHubArtifactError('workflow and artifact prefix must be non-empty')
    token = _read_restricted_token(token_file)
    client = GitHubArtifactClient(token, api_url=api_url)
    selected_run = client.completed_run_id(repository, workflow, run_id)
    artifact = client.artifact(repository, selected_run, artifact_prefix)
    with tempfile.TemporaryDirectory(prefix='ros2-dashboard-artifact-') as temporary:
        archive = Path(temporary) / 'artifact.zip'
        client.download(artifact, archive)
        return publish_dashboard_bundle(
            archive,
            profile_path,
            deployment_root,
            **publication_options,
        )


def _read_restricted_token(path):
    path = Path(path).expanduser().absolute()
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise GitHubArtifactError('GitHub token file is not readable') from exc
    if not stat.S_ISREG(mode) or path.is_symlink():
        raise GitHubArtifactError('GitHub token path must be a regular file')
    if stat.S_IMODE(mode) & 0o077:
        raise GitHubArtifactError('GitHub token file permissions must be 0600 or stricter')
    try:
        token = path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise GitHubArtifactError('GitHub token file is not readable') from exc
    if not token or any(character.isspace() for character in token):
        raise GitHubArtifactError('GitHub token file does not contain one valid token')
    return token


def _valid_run_id(value):
    if not _RUN_ID_PATTERN.fullmatch(value):
        raise GitHubArtifactError('GitHub workflow run ID is invalid')
    return value


def _require_completed_run(run, run_id, workflow_id):
    if (
        not isinstance(run, dict)
        or type(workflow_id) is not int
        or str(run.get('id', '')) != run_id
        or run.get('workflow_id') != workflow_id
        or run.get('status') != 'completed'
        or run.get('conclusion') != 'success'
    ):
        raise GitHubArtifactError(
            'explicit GitHub workflow run is not successfully completed'
        )


def _origin(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        return None
    try:
        port = parsed.port or 443
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.lower(), port


class _CredentialSafeRedirect(HTTPRedirectHandler):

    def redirect_request(self, request, fp, code, message, headers, new_url):
        redirected = super().redirect_request(
            request,
            fp,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is not None and _origin(request.full_url) != _origin(new_url):
            redirected.remove_header('Authorization')
        return redirected
