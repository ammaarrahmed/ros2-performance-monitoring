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
import re

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / '.github/workflows/publish-images.yml'
WORKFLOW_TEXT = WORKFLOW_PATH.read_text()
WORKFLOW = yaml.load(WORKFLOW_TEXT, Loader=yaml.BaseLoader)
PINNED_ACTION = re.compile(r'^[^\s@]+@[0-9a-f]{40}$')


def test_publish_triggers_are_explicit_and_never_include_pull_requests():
    triggers = WORKFLOW['on']

    assert set(triggers) == {'release', 'workflow_dispatch'}
    assert triggers['release']['types'] == ['published']
    assert triggers['workflow_dispatch']['inputs']['release_tag']['required'] == 'true'
    assert 'pull_request' not in WORKFLOW_TEXT
    assert 'schedule:' not in WORKFLOW_TEXT


def test_publish_job_has_only_required_permissions_and_images():
    assert WORKFLOW['permissions'] == {}
    publish = WORKFLOW['jobs']['publish']

    assert publish['runs-on'] == 'ubuntu-24.04'
    assert publish['permissions'] == {
        'contents': 'read',
        'packages': 'write',
        'id-token': 'write',
        'attestations': 'write',
    }
    assert publish['env']['CLI_IMAGE'] == (
        'ghcr.io/ammaarrahmed/ros2-performance-monitoring-cli'
    )
    assert publish['env']['EXPORTER_IMAGE'] == (
        'ghcr.io/ammaarrahmed/ros2-performance-monitoring-exporter'
    )
    assert WORKFLOW['jobs']['record_release']['permissions'] == {'contents': 'write'}
    assert 'PERSONAL' not in WORKFLOW_TEXT.upper()


def test_every_action_is_pinned_to_a_full_commit():
    action_steps = [
        step
        for job in WORKFLOW['jobs'].values()
        for step in job['steps']
        if 'uses' in step
    ]

    assert action_steps
    assert all(PINNED_ACTION.fullmatch(step['uses']) for step in action_steps)


def test_both_candidates_are_verified_before_registry_publication():
    steps = WORKFLOW['jobs']['publish']['steps']
    names = [step['name'] for step in steps]
    smoke_index = names.index('Smoke-test both release candidates')
    login_index = names.index('Log in to GitHub Container Registry')
    cli_publish_index = names.index('Publish CLI image')
    exporter_publish_index = names.index('Publish exporter image')
    complete_index = names.index('Report completed two-image release')

    assert names.index('Build CLI release candidate') < smoke_index
    assert names.index('Build exporter release candidate') < smoke_index
    assert smoke_index < login_index < cli_publish_index < exporter_publish_index
    assert complete_index > names.index('Attest CLI manifest digest')
    assert complete_index > names.index('Attest exporter manifest digest')


def test_published_images_share_platform_identity_tags_cache_and_attestations():
    steps = WORKFLOW['jobs']['publish']['steps']
    publish_steps = [
        step for step in steps
        if step['name'] in ('Publish CLI image', 'Publish exporter image')
    ]

    assert len(publish_steps) == 2
    for step in publish_steps:
        inputs = step['with']
        assert inputs['platforms'] == 'linux/amd64'
        assert inputs['push'] == 'true'
        assert 'steps.identity.outputs.version' in inputs['tags']
        assert 'steps.identity.outputs.revision' in inputs['tags']
        assert 'PROJECT_VERSION=${{ steps.identity.outputs.version }}' in (
            inputs['build-args']
        )
        assert 'VCS_REF=${{ steps.identity.outputs.revision }}' in inputs['build-args']
        assert inputs['cache-from'].startswith('type=gha,')
        assert inputs['cache-to'].startswith('type=gha,')
        assert inputs['provenance'] == 'mode=max'
        assert inputs['sbom'] == 'true'

    assert ':latest' not in WORKFLOW_TEXT
    assert WORKFLOW_TEXT.count('push-to-registry: true') == 2
    assert WORKFLOW_TEXT.count('subject-digest: ${{ steps.publish_') == 2


def test_release_contract_and_immutability_run_before_publication():
    steps = WORKFLOW['jobs']['publish']['steps']
    names = [step['name'] for step in steps]
    identity = steps[names.index('Validate release identity')]['run']
    immutable = steps[names.index('Protect immutable release tags')]['run']

    assert 'ros2_performance_monitoring.release_contract' in identity
    assert 'refs/tags/${RELEASE_TAG}' in identity
    assert 'Refusing to overwrite immutable image tag' in immutable
    assert names.index('Protect immutable release tags') < names.index('Publish CLI image')


def test_workflow_records_digest_cost_and_release_information_after_both_pushes():
    publish = WORKFLOW['jobs']['publish']
    release_job = WORKFLOW['jobs']['record_release']

    assert publish['outputs']['cli_digest'] == '${{ steps.publish_cli.outputs.digest }}'
    assert publish['outputs']['exporter_digest'] == (
        '${{ steps.publish_exporter.outputs.digest }}'
    )
    assert release_job['needs'] == 'publish'
    assert release_job['if'] == "github.event_name == 'release'"
    assert 'GITHUB_STEP_SUMMARY' in WORKFLOW_TEXT
    assert 'Compressed content' in WORKFLOW_TEXT
    assert '## Published runtime images' in WORKFLOW_TEXT


def test_build_inputs_and_context_exclude_sensitive_or_host_state():
    dockerfile = (REPOSITORY_ROOT / 'Dockerfile').read_text()
    dockerignore = (REPOSITORY_ROOT / '.dockerignore').read_text().splitlines()

    assert re.search(r'^ARG PYTHON_IMAGE=\S+@sha256:[0-9a-f]{64}$', dockerfile, re.M)
    assert re.search(r'^ARG DOCKER_CLI_IMAGE=\S+@sha256:[0-9a-f]{64}$', dockerfile, re.M)
    for label in (
        'title',
        'description',
        'version',
        'revision',
        'source',
        'licenses',
    ):
        assert dockerfile.count(f'org.opencontainers.image.{label}=') == 2
    for ignored in (
        '.git',
        '.github',
        '.docker',
        '.ssh',
        '.env',
        '.env.*',
        '.pytest_cache',
        '.venv',
        'build',
        'install',
        'log',
        'results',
        'test',
        '*.egg-info',
        '*.key',
        '*.pem',
    ):
        assert ignored in dockerignore
