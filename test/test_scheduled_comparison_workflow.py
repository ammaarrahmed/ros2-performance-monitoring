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
WORKFLOW_PATH = (
    REPOSITORY_ROOT / '.github' / 'workflows' / 'scheduled-rclcpp-comparison.yml'
)
WORKFLOW_TEXT = WORKFLOW_PATH.read_text()
WORKFLOW = yaml.load(WORKFLOW_TEXT, Loader=yaml.BaseLoader)
PINNED_ACTION = re.compile(r'^[^\s@]+@[0-9a-f]{40}$')
PINNED_IMAGE = re.compile(r'^ghcr\.io/[^\s@]+@sha256:[0-9a-f]{64}$')
README_TEXT = (REPOSITORY_ROOT / 'README.md').read_text()
ARCHITECTURE_TEXT = (REPOSITORY_ROOT / 'doc' / 'architecture.md').read_text()


def test_manual_and_gated_off_hours_triggers_never_include_pull_requests():
    triggers = WORKFLOW['on']

    assert set(triggers) == {'workflow_dispatch', 'schedule'}
    assert triggers['workflow_dispatch']['inputs']['bootstrap_sha']['required'] == 'false'
    assert triggers['schedule'] == [{'cron': '37 2 * * 1'}]
    assert "vars.ENABLE_RCLCPP_SCHEDULE == 'true'" in (
        WORKFLOW['jobs']['discover']['if']
    )
    assert 'pull_request' not in WORKFLOW_TEXT


def test_workflow_serializes_producers_and_sets_explicit_runner_timeouts():
    assert WORKFLOW['permissions'] == {}
    assert WORKFLOW['concurrency'] == {
        'group': 'scheduled-rclcpp-comparison',
        'cancel-in-progress': 'false',
    }
    assert {
        name: (job['runs-on'], job['timeout-minutes'])
        for name, job in WORKFLOW['jobs'].items()
    } == {
        'discover': ('ubuntu-26.04', '5'),
        'benchmark': ('ubuntu-26.04', '180'),
        'advance_state': ('ubuntu-26.04', '10'),
    }


def test_jobs_have_minimal_permissions_and_isolate_state_writes():
    jobs = WORKFLOW['jobs']

    assert jobs['discover']['permissions'] == {'contents': 'read'}
    assert jobs['benchmark']['permissions'] == {'contents': 'read'}
    assert jobs['advance_state']['permissions'] == {
        'actions': 'read',
        'contents': 'write',
    }
    assert "needs.benchmark.result == 'success'" in jobs['advance_state']['if']
    assert 'github.event.repository.default_branch' in jobs['advance_state']['if']
    state_text = '\n'.join(
        step.get('run', '') for step in jobs['advance_state']['steps']
    )
    assert 'docker ' not in state_text
    assert 'git/ref/heads/${STATE_BRANCH}' in state_text
    assert 'contents/${STATE_PATH}' in state_text


def test_discovery_skips_unchanged_revisions_before_benchmark_setup():
    discover = WORKFLOW['jobs']['discover']
    benchmark = WORKFLOW['jobs']['benchmark']

    assert discover['outputs']['should_run'] == '${{ steps.plan.outputs.should_run }}'
    assert discover['outputs']['source_dependencies_sha256'] == (
        '${{ steps.plan.outputs.source_dependencies_sha256 }}'
    )
    assert benchmark['needs'] == 'discover'
    assert benchmark['if'] == "needs.discover.outputs.should_run == 'true'"
    assert 'scheduled_comparison discover' in WORKFLOW_TEXT
    assert 'The workflow stopped before pulling the controller or building benchmark images.' in (
        WORKFLOW_TEXT
    )


def test_benchmark_uses_one_digest_pinned_container_controller():
    benchmark = WORKFLOW['jobs']['benchmark']
    steps = benchmark['steps']
    pull = next(
        step['run']
        for step in steps
        if step['name'] == 'Pull and verify the pinned controller image'
    )
    compare = next(
        step['run']
        for step in steps
        if step['name'] == 'Run the exact-ref comparison'
    )

    assert PINNED_IMAGE.fullmatch(WORKFLOW['env']['CONTROLLER_IMAGE'])
    assert WORKFLOW['env']['CONTROLLER_VERSION'] == '0.1.1'
    assert 'docker pull "${CONTROLLER_IMAGE}"' in pull
    assert 'for attempt in 1 2 3' in pull
    assert 'docker image inspect' in pull
    assert '"ros2-performance-monitoring ${CONTROLLER_VERSION}"' in pull
    assert 'pip install' not in WORKFLOW_TEXT
    assert 'docker run --rm' in compare
    assert '--volume /var/run/docker.sock:/var/run/docker.sock' in compare
    assert '--env ROS2_PERFORMANCE_CONTROLLER_MODE=container' in compare
    assert '--env ROS2_PERFORMANCE_CONTROLLER_IMAGE="${CONTROLLER_IMAGE}"' in compare
    assert '--volume "${GITHUB_WORKSPACE}/results:/results"' in compare
    assert '--volume "${GITHUB_WORKSPACE}/.scheduled-cache:/cache"' in compare
    assert (
        '--volume "${SOURCE_DEPENDENCIES_FILE}:/source-dependencies.repos:ro"'
        in compare
    )
    assert '"${CONTROLLER_IMAGE}" experiment compare' in compare


def test_smoke_command_uses_exact_refs_and_every_pinned_profile_setting():
    steps = WORKFLOW['jobs']['benchmark']['steps']
    compare = next(
        step['run']
        for step in steps
        if step['name'] == 'Run the exact-ref comparison'
    )

    expected_arguments = (
        '--reference-ref "${REFERENCE_SHA}"',
        '--candidate-ref "${CANDIDATE_SHA}"',
        '--rclcpp-repo-url https://github.com/ros2/rclcpp.git',
        '--source-dependencies /source-dependencies.repos',
        '--container-repo-url https://github.com/ros2/ros2-benchmark-container.git',
        '--container-ref "${benchmark_ref}"',
        '--ros-distro rolling',
        '--suite rclcpp-minimal',
        '--executor EventsCBGExecutor',
        '--duration 1',
        '--warmups 0',
        '--repeats 3',
        '--order balanced',
        '--seed 0',
        '--bootstrap-repeats 100',
        '--bootstrap-seed 0',
        '--minimum-trials 3',
    )
    assert all(argument in compare for argument in expected_arguments)
    assert '--cpuset-cpus' not in compare
    assert '0|1|2)' in compare
    assert '3|4) exit' in compare


def test_rolling_dependencies_are_resolved_once_and_checksum_bound():
    benchmark = WORKFLOW['jobs']['benchmark']
    materialize = next(
        step['run']
        for step in benchmark['steps']
        if step['name'] == 'Materialize the exact Rolling dependency snapshot'
    )
    bundle = next(
        step['run']
        for step in benchmark['steps']
        if step['name'] == 'Validate and assemble both publication bundles'
    )

    assert 'source_dependencies_b64' in WORKFLOW['jobs']['discover']['outputs']
    assert 'base64 --decode' in materialize
    assert 'sha256sum "${SOURCE_DEPENDENCIES_FILE}"' in materialize
    assert 'test("^[0-9a-f]{40}$")' in materialize
    assert '--source-dependencies "${SOURCE_DEPENDENCIES_FILE}"' in bundle
    assert 'Rolling dependency snapshot:' in WORKFLOW_TEXT


def test_both_checksum_bound_bundles_are_short_lived_and_state_uses_compact_one():
    benchmark = WORKFLOW['jobs']['benchmark']
    uploads = [
        step for step in benchmark['steps']
        if step.get('uses', '').startswith('actions/upload-artifact@')
    ]

    assert len(uploads) == 2
    assert {step['with']['name'] for step in uploads} == {
        '${{ env.FULL_ARTIFACT_NAME }}',
        '${{ env.DASHBOARD_ARTIFACT_NAME }}',
    }
    assert all(step['with']['retention-days'] == '14' for step in uploads)
    assert all(step['with']['if-no-files-found'] == 'error' for step in uploads)
    assert 'scheduled_comparison bundle' in WORKFLOW_TEXT
    assert 'scheduled_comparison state' in WORKFLOW_TEXT
    assert 'scheduled_comparison validate' in WORKFLOW_TEXT
    assert 'docker push' not in WORKFLOW_TEXT
    assert benchmark['outputs']['dashboard_artifact_name'] == (
        '${{ steps.bundle.outputs.dashboard_artifact_name }}'
    )


def test_summary_and_failure_diagnostics_report_required_cost_and_identity():
    names = [step['name'] for step in WORKFLOW['jobs']['benchmark']['steps']]

    assert 'Record initial runtime and storage diagnostics' in names
    assert 'Collect failure diagnostics' in names
    assert 'Clean runner containers and temporary images' in names
    for value in (
        'Reference:',
        'Candidate:',
        'Outcome:',
        'Artifacts:',
        'Elapsed:',
        'uncompressed upload inputs:',
        'gh run download',
        'not calibrated for authoritative performance claims',
    ):
        assert value in WORKFLOW_TEXT


def test_every_external_action_is_pinned_to_a_full_commit():
    action_steps = [
        step
        for job in WORKFLOW['jobs'].values()
        for step in job['steps']
        if 'uses' in step
    ]

    assert action_steps
    assert all(PINNED_ACTION.fullmatch(step['uses']) for step in action_steps)


def test_documentation_keeps_smoke_results_non_authoritative_and_schedule_gated():
    normalized_readme = ' '.join(README_TEXT.split())
    for text in (README_TEXT, ARCHITECTURE_TEXT):
        assert 'non-authoritative' in text
        assert 'benchmark-state' in text
        assert '14' in text
    assert 'not calibrated for authoritative performance claims' in README_TEXT
    assert 'ENABLE_RCLCPP_SCHEDULE' in README_TEXT
    assert 'exit codes `3` or `4` fail without changing the baseline' in normalized_readme
