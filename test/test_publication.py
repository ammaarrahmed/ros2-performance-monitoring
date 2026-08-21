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

import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import stat
import tarfile
import zipfile

import pytest
import ros2_performance_monitoring.publication as publication
from ros2_performance_monitoring.publication import PublicationError
from ros2_performance_monitoring.publication import publish_dashboard_bundle
from ros2_performance_monitoring.scheduled_comparison import CHECKSUM_FILENAME
from ros2_performance_monitoring.scheduled_comparison import DASHBOARD_BUNDLE_FILES


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / '.github'
    / 'benchmark-profiles'
    / 'rolling-workflow-smoke-v1.json'
)


def test_valid_bundle_activates_only_after_candidate_validation(tmp_path, monkeypatch):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    deployment = tmp_path / 'deployment'
    observed_indexes = []

    def validate(index_path):
        active = deployment / 'active-history.json'
        observed_indexes.append(active.read_bytes() if active.exists() else None)
        return _fake_load_history(index_path)

    monkeypatch.setattr(publication, 'load_active_history', validate)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)

    result = publish_dashboard_bundle(source, PROFILE_PATH, deployment)

    assert result.outcome == 'activated'
    assert source.exists()
    assert observed_indexes == [None, None]
    index = json.loads(result.index_path.read_text(encoding='utf-8'))
    assert index['history_limit'] == 10
    assert [entry['bundle_id'] for entry in index['bundles']] == [result.bundle_id]
    assert (deployment / index['bundles'][0]['path']).is_dir()
    audit = _audit_records(deployment)
    assert audit[-1]['outcome'] == 'activated'
    assert audit[-1]['github']['run_id'] == '100'


def test_tampered_checksum_is_rejected_without_changing_active_state(
    tmp_path,
    monkeypatch,
):
    deployment = tmp_path / 'deployment'
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)
    publish_dashboard_bundle(source, PROFILE_PATH, deployment)
    previous = (deployment / 'active-history.json').read_bytes()
    tampered = _bundle(tmp_path / 'tampered', run_id='101', experiment='experiment-two')
    (tampered / 'comparison-report.json').write_text('tampered\n', encoding='utf-8')

    with pytest.raises(PublicationError, match='checksum failed'):
        publish_dashboard_bundle(tampered, PROFILE_PATH, deployment)

    assert (deployment / 'active-history.json').read_bytes() == previous


@pytest.mark.parametrize(
    ('change', 'message'),
    (
        ({'schema_version': 2}, 'unsupported shape'),
        ({'reference_sha': 'short'}, 'full lowercase commit SHA'),
        ({'comparison_exit_code': 3}, 'completed comparison'),
    ),
)
def test_manifest_schema_identity_and_completion_are_rejected(
    tmp_path,
    change,
    message,
):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    _rewrite_manifest(source, change)

    with pytest.raises(PublicationError, match=message):
        publish_dashboard_bundle(source, PROFILE_PATH, tmp_path / 'deployment')


def test_rejected_manifest_credentials_are_never_written_to_audit(tmp_path):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    deployment = tmp_path / 'deployment'
    _rewrite_manifest(source, {
        'github': {
            'repository': 'owner/repository',
            'run_id': '100',
            'run_attempt': '1',
            'token': 'secret-retrieval-credential',
        },
    })

    with pytest.raises(PublicationError, match='workflow identity is malformed'):
        publish_dashboard_bundle(source, PROFILE_PATH, deployment)

    audit = (deployment / 'publication-audit.jsonl').read_text(encoding='utf-8')
    assert 'secret-retrieval-credential' not in audit
    assert json.loads(audit)['github'] == {}


def test_report_binding_validation_finishes_before_previous_index_changes(
    tmp_path,
    monkeypatch,
):
    deployment = tmp_path / 'deployment'
    first = _bundle(tmp_path / 'first', run_id='100', experiment='experiment-one')
    second = _bundle(tmp_path / 'second', run_id='101', experiment='experiment-two')
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)
    publish_dashboard_bundle(first, PROFILE_PATH, deployment)
    previous = (deployment / 'active-history.json').read_bytes()

    def reject_binding(index_path):
        assert (deployment / 'active-history.json').read_bytes() == previous
        raise ValueError('comparison report dataset binding is invalid')

    monkeypatch.setattr(publication, 'load_active_history', reject_binding)
    with pytest.raises(PublicationError, match='dataset binding'):
        publish_dashboard_bundle(second, PROFILE_PATH, deployment)

    assert (deployment / 'active-history.json').read_bytes() == previous


@pytest.mark.parametrize('name', ('../escape', '/absolute/path'))
def test_zip_extraction_rejects_traversing_and_absolute_paths(tmp_path, name):
    archive = tmp_path / 'bundle.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(name, 'unsafe')

    with pytest.raises(PublicationError, match='absolute or traversing'):
        publish_dashboard_bundle(archive, PROFILE_PATH, tmp_path / 'deployment')

    assert not (tmp_path / 'escape').exists()


def test_zip_extraction_rejects_symbolic_links(tmp_path):
    archive = tmp_path / 'bundle.zip'
    link = zipfile.ZipInfo('producer-manifest.json')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(link, 'target')

    with pytest.raises(PublicationError, match='unsafe file'):
        publish_dashboard_bundle(archive, PROFILE_PATH, tmp_path / 'deployment')


@pytest.mark.parametrize('member_type', (tarfile.SYMTYPE, tarfile.LNKTYPE))
def test_tar_extraction_rejects_symbolic_and_hard_links(tmp_path, member_type):
    archive = tmp_path / 'bundle.tar'
    with tarfile.open(archive, 'w') as output:
        member = tarfile.TarInfo('producer-manifest.json')
        member.type = member_type
        member.linkname = 'target'
        output.addfile(member, io.BytesIO())

    with pytest.raises(PublicationError, match='unsafe file'):
        publish_dashboard_bundle(archive, PROFILE_PATH, tmp_path / 'deployment')


def test_unexpected_archive_file_is_rejected(tmp_path):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    archive = tmp_path / 'bundle.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        for path in source.rglob('*'):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())
        output.writestr('credentials.txt', 'must not be accepted')

    with pytest.raises(PublicationError, match='unexpected=.*credentials.txt'):
        publish_dashboard_bundle(archive, PROFILE_PATH, tmp_path / 'deployment')


def test_directory_source_rejects_symbolic_links(tmp_path):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    (source / 'targets' / 'reference.json').unlink()
    (source / 'targets' / 'reference.json').symlink_to('/etc/passwd')

    with pytest.raises(PublicationError, match='unsafe file'):
        publish_dashboard_bundle(source, PROFILE_PATH, tmp_path / 'deployment')


def test_repeated_delivery_is_idempotent_by_workflow_and_experiment(
    tmp_path,
    monkeypatch,
):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    deployment = tmp_path / 'deployment'
    health_calls = []
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(
        publication,
        '_wait_for_health',
        lambda *args: health_calls.append(args),
    )

    first = publish_dashboard_bundle(source, PROFILE_PATH, deployment)
    second = publish_dashboard_bundle(source, PROFILE_PATH, deployment)

    assert first.outcome == 'activated'
    assert second.outcome == 'idempotent'
    assert first.bundle_id == second.bundle_id
    assert len(health_calls) == 1
    assert len(list((deployment / 'bundles').iterdir())) == 1
    assert _audit_records(deployment)[-1]['outcome'] == 'idempotent'


def test_conflicting_reuse_of_workflow_identity_is_rejected(tmp_path, monkeypatch):
    deployment = tmp_path / 'deployment'
    first = _bundle(tmp_path / 'first', run_id='100', experiment='experiment-one')
    conflict = _bundle(tmp_path / 'conflict', run_id='100', experiment='experiment-two')
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)
    publish_dashboard_bundle(first, PROFILE_PATH, deployment)

    with pytest.raises(PublicationError, match='conflicting evidence'):
        publish_dashboard_bundle(conflict, PROFILE_PATH, deployment)


def test_active_window_and_inactive_retention_are_bounded(tmp_path, monkeypatch):
    deployment = tmp_path / 'deployment'
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)
    results = []
    for index in range(3):
        source = _bundle(
            tmp_path / f'source-{index}',
            run_id=str(100 + index),
            experiment=f'experiment-{index}',
            candidate=f'{index + 1}' * 40,
        )
        results.append(publish_dashboard_bundle(
            source,
            PROFILE_PATH,
            deployment,
            history_limit=2,
            inactive_retention=0,
        ))

    active = json.loads((deployment / 'active-history.json').read_text())
    assert [item['bundle_id'] for item in active['bundles']] == [
        results[1].bundle_id,
        results[2].bundle_id,
    ]
    assert not (deployment / 'bundles' / results[0].bundle_id).exists()
    assert results[2].removed_bundle_ids == (results[0].bundle_id,)


def test_failed_health_check_restores_previous_index_and_runs_rollback_hook(
    tmp_path,
    monkeypatch,
):
    deployment = tmp_path / 'deployment'
    first = _bundle(tmp_path / 'first', run_id='100', experiment='experiment-one')
    second = _bundle(tmp_path / 'second', run_id='101', experiment='experiment-two')
    hook = tmp_path / 'restart-hook'
    hook.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    hook.chmod(0o700)
    calls = []
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)
    monkeypatch.setattr(
        publication,
        '_run_restart_hook',
        lambda configured, index: calls.append((configured, index.read_bytes())),
    )
    publish_dashboard_bundle(first, PROFILE_PATH, deployment, restart_hook=hook)
    previous = (deployment / 'active-history.json').read_bytes()

    def fail_health(*args):
        raise PublicationError('unhealthy')

    monkeypatch.setattr(publication, '_wait_for_health', fail_health)
    with pytest.raises(PublicationError, match='rolled back'):
        publish_dashboard_bundle(second, PROFILE_PATH, deployment, restart_hook=hook)

    assert (deployment / 'active-history.json').read_bytes() == previous
    assert calls[-1][1] == previous
    assert _audit_records(deployment)[-1]['outcome'] == 'failed'


def test_source_deletion_requires_explicit_successful_policy(tmp_path, monkeypatch):
    source = _bundle(tmp_path / 'source', run_id='100', experiment='experiment-one')
    monkeypatch.setattr(publication, 'load_active_history', _fake_load_history)
    monkeypatch.setattr(publication, '_wait_for_health', lambda *args: None)

    publish_dashboard_bundle(
        source,
        PROFILE_PATH,
        tmp_path / 'deployment',
        delete_source=True,
    )

    assert not source.exists()


def test_interprocess_lock_rejects_overlapping_publishers(tmp_path):
    lock_path = tmp_path / '.publish.lock'
    context = multiprocessing.get_context('fork')
    queue = context.Queue()

    def contend():
        try:
            with publication._publication_lock(lock_path):
                queue.put('acquired')
        except PublicationError as exc:
            queue.put(str(exc))

    with publication._publication_lock(lock_path):
        process = context.Process(target=contend)
        process.start()
        process.join(timeout=5)

    assert process.exitcode == 0
    assert queue.get(timeout=1) == 'another dashboard publication is already running'


def _bundle(root, *, run_id, experiment, candidate='b' * 40):
    root.mkdir()
    manifest = {
        'schema_version': 1,
        'profile': 'rolling-workflow-smoke-v1',
        'authoritative': False,
        'notice': (
            'Pipeline smoke evidence only; this profile is not calibrated for '
            'authoritative performance claims.'
        ),
        'reference_sha': 'a' * 40,
        'candidate_sha': candidate,
        'experiment_id': experiment,
        'run_ids': [f'{experiment}-reference', f'{experiment}-candidate'],
        'comparison_exit_code': 0,
        'comparison_outcome': 'No regression',
        'github': {
            'repository': 'owner/repository',
            'run_id': run_id,
            'run_attempt': '1',
        },
        'created_at': '2026-08-21T00:00:00Z',
        'bundle_kind': 'dashboard',
    }
    for relative in DASHBOARD_BUNDLE_FILES - {CHECKSUM_FILENAME}:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        value = manifest if relative == 'producer-manifest.json' else {'file': relative}
        path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
    files = sorted(path for path in root.rglob('*') if path.is_file())
    (root / CHECKSUM_FILENAME).write_text(
        ''.join(
            f'{_sha256(path)}  {path.relative_to(root).as_posix()}\n'
            for path in files
        ),
        encoding='utf-8',
    )
    return root


def _fake_load_history(index_path):
    index_path = Path(index_path)
    index = json.loads(index_path.read_text(encoding='utf-8'))
    assert index['schema_version'] == 1
    assert index['order'] == 'oldest-first'
    assert 0 < len(index['bundles']) <= index['history_limit']
    for entry in index['bundles']:
        bundle = index_path.parent / entry['path']
        assert bundle.is_dir()
        assert _sha256(bundle / CHECKSUM_FILENAME) == entry['checksums_sha256']
    return ()


def _rewrite_manifest(root, changes):
    manifest_path = root / 'producer-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.update(changes)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + '\n', encoding='utf-8')
    files = sorted(
        path for path in root.rglob('*')
        if path.is_file() and path.name != CHECKSUM_FILENAME
    )
    (root / CHECKSUM_FILENAME).write_text(
        ''.join(
            f'{_sha256(path)}  {path.relative_to(root).as_posix()}\n'
            for path in files
        ),
        encoding='utf-8',
    )


def _audit_records(deployment):
    return [
        json.loads(line)
        for line in (deployment / 'publication-audit.jsonl').read_text().splitlines()
    ]


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
