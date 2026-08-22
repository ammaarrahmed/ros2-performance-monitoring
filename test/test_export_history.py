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
import json
from pathlib import Path

import pytest
from ros2_performance_monitoring.dataset import build_dataset
import ros2_performance_monitoring.exporters.history as history
from ros2_performance_monitoring.exporters.history import HistoryIndexError
from ros2_performance_monitoring.exporters.history import load_active_history


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / '.github'
    / 'benchmark-profiles'
    / 'rolling-workflow-smoke-v2.json'
)


def test_two_and_three_bundle_histories_preserve_index_order_and_bound(tmp_path):
    entries = [
        _threshold_bundle(tmp_path, f'bundle-{index}', f'run-{index}')
        for index in range(3)
    ]
    index = _write_index(tmp_path, entries, limit=3)

    bundles = load_active_history(index)

    assert [bundle.bundle_id for bundle in bundles] == [
        'bundle-0', 'bundle-1', 'bundle-2',
    ]
    assert [bundle.position for bundle in bundles] == [0, 1, 2]
    _write_index(tmp_path, entries[:2], limit=2)
    assert len(load_active_history(index)) == 2
    _write_index(tmp_path, entries, limit=2)
    with pytest.raises(HistoryIndexError, match='more bundles than its limit'):
        load_active_history(index)


def test_history_checks_every_bundle_before_returning_any_data(tmp_path):
    first = _threshold_bundle(tmp_path, 'first', 'run-first')
    second = _threshold_bundle(tmp_path, 'second', 'run-second')
    index = _write_index(tmp_path, [first, second], limit=2)
    dataset = tmp_path / second['path'] / history.DATASET_PATH
    dataset.write_text(dataset.read_text(encoding='utf-8') + '{}\n', encoding='utf-8')

    with pytest.raises(HistoryIndexError, match="bundle 'second' checksum failed"):
        load_active_history(index)


def test_index_checksum_rejects_rewritten_bundle_checksums(tmp_path):
    entry = _threshold_bundle(tmp_path, 'bundle', 'run-a')
    index = _write_index(tmp_path, [entry], limit=1)
    checksums = tmp_path / entry['path'] / history.CHECKSUM_FILENAME
    checksums.write_text(checksums.read_text(encoding='utf-8') + '\n', encoding='utf-8')

    with pytest.raises(HistoryIndexError, match='does not match the index'):
        load_active_history(index)


def test_history_rejects_unlisted_directory_symlinks(tmp_path):
    entry = _threshold_bundle(tmp_path, 'bundle', 'run-a')
    index = _write_index(tmp_path, [entry], limit=1)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / entry['path'] / 'linked').symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(HistoryIndexError, match='contains a symlink'):
        load_active_history(index)


def test_report_is_loaded_against_its_own_dataset(tmp_path, monkeypatch):
    entries = [
        _report_bundle(tmp_path, 'comparison-one', 'run-one'),
        _report_bundle(tmp_path, 'comparison-two', 'run-two'),
    ]
    index = _write_index(tmp_path, entries, limit=2)
    validated_paths = []

    monkeypatch.setattr(history, 'validate_bundle', _fake_validate_bundle)

    def fake_load_report(report_path, dataset_path, records):
        validated_paths.append((report_path.parent.name, dataset_path.parent.parent.name))
        commit = 'b' * 40 if report_path.parent.name == 'comparison-one' else 'c' * 40
        return _validated_report(report_path.parent.name, commit)

    monkeypatch.setattr(history, 'load_comparison_report', fake_load_report)

    load_active_history(index)

    assert validated_paths == [
        ('comparison-one', 'comparison-one'),
        ('comparison-two', 'comparison-two'),
    ]


def test_history_can_mix_report_and_threshold_only_evidence(tmp_path, monkeypatch):
    report = _report_bundle(tmp_path, 'comparison', 'report-run')
    threshold = _threshold_bundle(tmp_path, 'legacy', 'legacy-run')
    index = _write_index(tmp_path, [report, threshold], limit=2)
    monkeypatch.setattr(history, 'validate_bundle', _fake_validate_bundle)
    monkeypatch.setattr(
        history,
        'load_comparison_report',
        lambda report_path, dataset_path, records: _validated_report(
            report_path.parent.name,
            'b' * 40,
        ),
    )

    bundles = load_active_history(index)

    assert [bundle.evidence for bundle in bundles] == [
        history.REPORT_EVIDENCE,
        history.THRESHOLD_EVIDENCE,
    ]
    assert bundles[0].comparison_id == 'comparison'
    assert bundles[1].comparison_report is None
    assert bundles[1].authoritative is False


def test_report_history_rejects_a_reduced_profile_before_bundle_validation(tmp_path):
    report = _report_bundle(tmp_path, 'comparison', 'report-run')
    report['profile'] = {
        key: report['profile'][key]
        for key in ('name', 'authoritative', 'notice')
    }
    index = _write_index(tmp_path, [report], limit=1)

    with pytest.raises(HistoryIndexError, match='producer profile is malformed'):
        load_active_history(index)


def _threshold_bundle(root, bundle_id, run_id):
    bundle = root / bundle_id
    source = bundle / 'source.jsonl'
    source.parent.mkdir()
    source.write_text(json.dumps(_record(run_id), sort_keys=True) + '\n', encoding='utf-8')
    build_dataset((source,), bundle / history.DATASET_PATH)
    return _finish_bundle(root, bundle, bundle_id, history.THRESHOLD_EVIDENCE)


def _report_bundle(root, bundle_id, run_id):
    _threshold_bundle(root, bundle_id, run_id)
    bundle = root / bundle_id
    (bundle / history.REPORT_PATH).write_text('{}\n', encoding='utf-8')
    (bundle / history.MANIFEST_FILENAME).write_text('{}\n', encoding='utf-8')
    entry = _finish_bundle(root, bundle, bundle_id, history.REPORT_EVIDENCE)
    entry['profile'] = json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
    return entry


def _finish_bundle(root, bundle, bundle_id, evidence):
    checksum_path = bundle / history.CHECKSUM_FILENAME
    files = sorted(path for path in bundle.rglob('*') if path.is_file() and path != checksum_path)
    checksum_path.write_text(
        ''.join(
            f'{_sha256(path)}  {path.relative_to(bundle).as_posix()}\n'
            for path in files
        ),
        encoding='utf-8',
    )
    return {
        'bundle_id': bundle_id,
        'path': bundle.relative_to(root).as_posix(),
        'checksums_sha256': _sha256(checksum_path),
        'evidence': evidence,
        'profile': {
            'name': 'rolling-workflow-smoke-v1',
            'authoritative': False,
            'notice': 'Smoke evidence is not calibrated.',
        },
    }


def _write_index(root, entries, limit):
    path = root / 'active-history.json'
    path.write_text(json.dumps({
        'schema_version': history.HISTORY_SCHEMA_VERSION,
        'order': history.HISTORY_ORDER,
        'history_limit': limit,
        'bundles': entries,
    }, sort_keys=True) + '\n', encoding='utf-8')
    return path


def _fake_validate_bundle(root, profile):
    assert profile['source_dependencies']['repositories']['ros2/rcl']['version'] == (
        'rolling'
    )
    commit = 'b' * 40 if root.name in ('comparison-one', 'comparison') else 'c' * 40
    run_id = (
        'report-run'
        if root.name == 'comparison'
        else f'run-{root.name.rsplit("-", 1)[-1]}'
    )
    return {
        'bundle_kind': 'dashboard',
        'experiment_id': root.name,
        'comparison_outcome': 'No regression',
        'reference_sha': commit,
        'candidate_sha': commit,
        'run_ids': [run_id],
    }


def _validated_report(experiment_id, commit):
    return type('ValidatedReport', (), {
        'report': {
            'experiment_id': experiment_id,
            'overall': {'status': 'No regression'},
            'targets': {
                role: {
                    'identity': {'client_library': {'resolved_commit': commit}},
                }
                for role in ('reference', 'candidate')
            },
        },
    })()


def _record(run_id):
    return {
        'schema_version': 5,
        'run_id': run_id,
        'timestamp': '2026-08-21T00:00:00Z',
        'benchmark_ref': 'rolling',
        'benchmark_commit': 'a' * 40,
        'client_library_ref': 'rolling',
        'client_library_commit': 'b' * 40,
        'client_library': 'rclcpp',
        'client_library_source': 'build',
        'platform': 'x86_64',
        'ros_distro': 'rolling',
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'topology': 'pub-sub',
        'process_mode': 'single_process',
        'communication_mode': 'ipc_off',
        'payload_size': 10,
        'frequency': 200.0,
        'node_role': '',
        'metric_name': 'subscription_latency',
        'numeric_value': 100.0,
        'unit': 'us',
        'aggregation': 'mean',
        'source_file': 'latency_total.txt',
        'run_kind': 'measured',
        'aggregation_method': 'none',
        'repeat_count': 1,
    }


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
