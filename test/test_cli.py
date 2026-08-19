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

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from ros2_performance_monitoring.benchmark_image import VerifiedImage
import ros2_performance_monitoring.cli as cli
from ros2_performance_monitoring.client_target import ClientLibraryTarget
from ros2_performance_monitoring.comparison_report import ComparisonReportError
from ros2_performance_monitoring.config import RunDefaults
from ros2_performance_monitoring.container_provider import get_default_container_repo
from ros2_performance_monitoring.dataset import DatasetBuildResult
from ros2_performance_monitoring.dataset import DatasetError
from ros2_performance_monitoring.experiment import ExperimentError
from ros2_performance_monitoring.statistical_comparison import CANNOT_COMPARE
from ros2_performance_monitoring.statistical_comparison import INSUFFICIENT_EVIDENCE
from ros2_performance_monitoring.statistical_comparison import NO_REGRESSION
from ros2_performance_monitoring.statistical_comparison import REGRESSION
from ros2_performance_monitoring.statistical_comparison import REPORT_SCHEMA_VERSION

pytestmark = pytest.mark.smoke

DEFAULT_CONTAINER_REPO_URL = 'https://github.com/ros2/ros2-benchmark-container'
DEFAULT_CONTAINER_REF = 'rolling'
DEFAULT_CONTAINER_COMMIT = 'a' * 40


def test_default_container_repo_uses_ros2_upstream():
    assert get_default_container_repo() == (
        DEFAULT_CONTAINER_REPO_URL,
        DEFAULT_CONTAINER_REF,
    )


def test_run_command_prints_message(monkeypatch, capsys):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        lambda image_spec, cache_dir: _verified_image(image_spec),
    )
    monkeypatch.setattr(cli, 'benchmark_runner', lambda **kwargs: None)
    monkeypatch.setattr(cli, 'parse_command', lambda args: None)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'run', '--duration', '60'])
    cli.main()
    captured = capsys.readouterr()
    assert 'Running Performance Monitor...' in captured.out


def test_doctor_command(monkeypatch, capsys):
    importlib.reload(cli)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'doctor'])
    cli.main()
    captured = capsys.readouterr()
    assert 'Doctor checks are not implemented yet.' in captured.out


def test_help_command_lists_all_command_usage(monkeypatch, capsys):
    importlib.reload(cli)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'help'])

    cli.main()

    output = capsys.readouterr().out
    assert 'Command usage:' in output
    for command in (
        'run',
        'doctor',
        'build-container',
        'parse',
        'dataset build',
        'experiment run',
        'experiment compare',
        'experiment report',
        'dashboard up',
        'dashboard down',
        'serve-prometheus',
        'help',
    ):
        assert f'ros2-performance-monitoring {command}' in output


@pytest.mark.parametrize(
    ('arguments', 'patched_name'),
    (
        (['dashboard', 'up'], 'dashboard_up'),
        (['serve-prometheus'], 'serve_metrics'),
    ),
)
def test_export_commands_forward_optional_comparison_report(
    tmp_path,
    monkeypatch,
    arguments,
    patched_name,
):
    importlib.reload(cli)
    received = {}

    def fake_export(input_path, port=9108, comparison_report_path=None):
        received.update({
            'input': input_path,
            'port': port,
            'comparison_report': comparison_report_path,
        })

    monkeypatch.setattr(cli, patched_name, fake_export)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        *arguments,
        '--input',
        str(tmp_path / 'dataset.jsonl'),
        '--comparison-report',
        str(tmp_path / 'comparison-report.json'),
    ])

    cli.main()

    assert received == {
        'input': str(tmp_path / 'dataset.jsonl'),
        'port': 9108,
        'comparison_report': str(tmp_path / 'comparison-report.json'),
    }


@pytest.mark.parametrize(
    'arguments',
    (
        ['unknown'],
        ['dashboard', 'unknown'],
        ['dataset', 'unknown'],
        ['experiment', 'unknown'],
    ),
)
def test_unknown_command_suggests_help(monkeypatch, capsys, arguments):
    importlib.reload(cli)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', *arguments],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert (
        "Run 'ros2-performance-monitoring help' to see available commands."
        in capsys.readouterr().err
    )


def test_build_container_command(monkeypatch, capsys):
    importlib.reload(cli)
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return DEFAULT_CONTAINER_COMMIT

    def fake_build_image(image_spec, cache_dir):
        received['image_name'] = image_spec.image_name
        received['cache_dir'] = cache_dir
        return _verified_image(image_spec)

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        fake_build_image,
    )
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'build-container'])
    cli.main()
    captured = capsys.readouterr()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': RunDefaults().cache_dir,
    }
    assert received['cache_dir'] == RunDefaults().cache_dir
    assert 'Building the container now...' in captured.out
    assert f'Successfully built verified image: {received["image_name"]}' in captured.out


def test_build_container_command_returns_subprocess_error(monkeypatch, capsys):
    importlib.reload(cli)

    def fake_build_container(image_spec, cache_dir):
        raise subprocess.CalledProcessError(7, ['docker/build', '-d', 'lyrical'])

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'build_benchmark_image', fake_build_container)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', 'build-container'])
    assert cli.main() == 7
    captured = capsys.readouterr()
    assert 'Successfully built verified image' not in captured.out
    assert 'Command failed with exit code 7' in captured.err


def test_dataset_build_command_passes_options_and_reports_result(
    tmp_path,
    monkeypatch,
    capsys,
):
    importlib.reload(cli)
    output = tmp_path / 'dashboard-data.jsonl'
    manifest = tmp_path / 'dashboard-data.manifest.json'
    received = {}

    def fake_build_dataset(inputs, output_path, exclude_runs=(), aggregate=None):
        received.update({
            'inputs': inputs,
            'output': output_path,
            'exclude_runs': exclude_runs,
            'aggregate': aggregate,
        })
        return DatasetBuildResult(
            record_count=30,
            run_count=3,
            aggregate_count=1,
            manifest_path=manifest,
            skipped_groups=('Skipped median aggregation for run group [run-c]',),
        )

    monkeypatch.setattr(cli, 'build_dataset', fake_build_dataset)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'dataset',
        'build',
        'run-a.jsonl',
        'run-b.jsonl',
        '--aggregate',
        'median',
        '--exclude-run',
        'warm-up',
        '--exclude-run',
        'bad-run',
        '--output',
        str(output),
    ])

    cli.main()

    captured = capsys.readouterr()
    assert received == {
        'inputs': ['run-a.jsonl', 'run-b.jsonl'],
        'output': str(output),
        'exclude_runs': ['warm-up', 'bad-run'],
        'aggregate': 'median',
    }
    assert f'Wrote 30 normalized metrics across 3 runs to {output}' in captured.out
    assert f'Wrote dataset manifest to {manifest}' in captured.out
    assert 'Skipped median aggregation for run group [run-c]' in captured.err


def test_dataset_build_command_reports_validation_errors(monkeypatch, capsys):
    importlib.reload(cli)

    def fail_build_dataset(*args, **kwargs):
        raise DatasetError('invalid normalized input')

    monkeypatch.setattr(cli, 'build_dataset', fail_build_dataset)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'dataset',
        'build',
        'run.jsonl',
        '--output',
        'dataset.jsonl',
    ])

    with pytest.raises(SystemExit, match='invalid normalized input'):
        cli.main()

    assert 'Wrote' not in capsys.readouterr().out


def test_experiment_run_prepares_exact_targets_and_plan(
    tmp_path,
    monkeypatch,
    capsys,
):
    importlib.reload(cli)
    received = {'resolved': [], 'built': []}

    def fake_resolve(repository_url, requested_ref, cache_dir):
        received['resolved'].append((repository_url, requested_ref, cache_dir))
        return ClientLibraryTarget(
            name='rclcpp',
            source='build',
            repository_url=repository_url,
            requested_ref=requested_ref,
            resolved_commit=requested_ref,
            checkout_path=Path(f'/cache/{requested_ref}'),
        )

    def fake_build(image_spec, cache_dir):
        received['built'].append((image_spec, cache_dir))
        return _verified_image(image_spec)

    def fake_run_experiment(experiment_dir, plan, image_specs, verified_images):
        received.update({
            'experiment_dir': experiment_dir,
            'plan': plan,
            'image_specs': image_specs,
            'verified_images': verified_images,
        })
        return argparse.Namespace(
            experiment_id='experiment-cli',
            completed_trials=8,
            reused_trials=2,
            dataset_path=Path(experiment_dir) / 'dataset' / 'dashboard-data.jsonl',
        )

    monkeypatch.setattr(cli, 'resolve_rclcpp_target', fake_resolve)
    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'detect_architecture', lambda: 'amd64')
    monkeypatch.setattr(cli, 'benchmark_image_exists', lambda _spec: False)
    monkeypatch.setattr(cli, 'build_benchmark_image', fake_build)
    monkeypatch.setattr(cli, 'run_experiment', fake_run_experiment)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'run',
        str(tmp_path / 'experiment'),
        '--reference-ref',
        'b' * 40,
        '--candidate-ref',
        'c' * 40,
        '--duration',
        '1',
        '--suite',
        'service-rclcpp-minimal',
        '--executor',
        'EventsExecutor',
        '--cpuset-cpus',
        '0-1',
        '--warmups',
        '1',
        '--repeats',
        '3',
        '--seed',
        '17',
    ])

    assert cli.main() is None

    assert [item[1] for item in received['resolved']] == ['b' * 40, 'c' * 40]
    assert len(received['built']) == 2
    assert received['plan']['configuration'] == {
        'ros_distro': RunDefaults().ros_distro,
        'suite': 'service-rclcpp-minimal',
        'executor': 'EventsExecutor',
        'duration': 1,
        'cpuset_cpus': '0-1',
    }
    assert received['plan']['schedule']['warmup_count'] == 1
    assert received['plan']['schedule']['measured_repeat_count'] == 3
    assert received['plan']['schedule']['seed'] == 17
    assert len(received['plan']['schedule']['trials']) == 8
    assert set(received['image_specs']) == {'reference', 'candidate'}
    assert received['experiment_dir'] == str(tmp_path / 'experiment')
    output = capsys.readouterr().out
    assert 'Experiment experiment-cli is complete: 8 trials (2 reused)' in output


def test_experiment_accepts_explicit_packaged_reference(monkeypatch):
    importlib.reload(cli)
    resolved = []

    def fake_resolve(repository_url, requested_ref, cache_dir):
        resolved.append(requested_ref)
        return ClientLibraryTarget(
            name='rclcpp',
            source='build',
            repository_url=repository_url,
            requested_ref=requested_ref,
            resolved_commit='c' * 40,
            checkout_path=Path('/cache/candidate'),
        )

    monkeypatch.setattr(cli, 'resolve_rclcpp_target', fake_resolve)
    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'detect_architecture', lambda: 'amd64')
    monkeypatch.setattr(cli, 'benchmark_image_exists', lambda _spec: True)
    monkeypatch.setattr(cli, 'verify_benchmark_image', _verified_image)
    monkeypatch.setattr(
        cli,
        'run_experiment',
        lambda experiment_dir, plan, image_specs, verified_images: argparse.Namespace(
            experiment_id='experiment-packaged',
            completed_trials=8,
            reused_trials=8,
            dataset_path=Path(experiment_dir) / 'dataset.jsonl',
        ),
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'run',
        'experiment',
        '--reference-source',
        'packaged',
        '--candidate-ref',
        'candidate',
    ])

    assert cli.main() is None
    assert resolved == ['candidate']


def test_experiment_requires_source_refs_before_repository_setup(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'setup_container_repo',
        lambda **kwargs: pytest.fail('benchmark repository setup must not start'),
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'run',
        'experiment',
        '--candidate-ref',
        'candidate',
    ])

    assert cli.main() == 1


@pytest.mark.parametrize(
    ('status', 'exit_code'),
    (
        (NO_REGRESSION, 0),
        (REGRESSION, 1),
        (INSUFFICIENT_EVIDENCE, 2),
        (CANNOT_COMPARE, 3),
    ),
)
def test_experiment_compare_writes_report_and_returns_documented_outcome(
    tmp_path,
    monkeypatch,
    capsys,
    status,
    exit_code,
):
    importlib.reload(cli)
    experiment_dir = tmp_path / 'experiment'
    experiment_dir.mkdir()
    measured_trial = argparse.Namespace(
        trial_id='candidate-measured-001',
        records=({'run_id': 'candidate-measured-001'},),
    )
    completed = argparse.Namespace(
        experiment_dir=experiment_dir,
        plan={'experiment_id': 'experiment-cli-compare'},
        measured_trials=(measured_trial,),
        experiment_complete=True,
        dataset_path=experiment_dir / 'dataset' / 'dashboard-data.jsonl',
        dataset_sha256='d' * 64,
    )
    received = {}

    monkeypatch.setattr(cli, 'load_experiment_evidence', lambda path: completed)

    def fake_report(plan, records, **options):
        received.update({'plan': plan, 'records': records, 'options': options})
        return {'schema_version': 1, 'overall': {'status': status}}

    monkeypatch.setattr(cli, 'build_comparison_report', fake_report)
    dataset_records = ({'run_id': 'reference-median'},)
    monkeypatch.setattr(cli, 'load_records', lambda path: dataset_records)

    def fake_validate(report, records=None, dataset_checksum=None):
        received['validation'] = {
            'report': report,
            'records': records,
            'dataset_checksum': dataset_checksum,
        }

    monkeypatch.setattr(cli, 'validate_comparison_report', fake_validate)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'compare',
        str(experiment_dir),
        '--reference',
        'candidate',
        '--candidate',
        'reference',
        '--confidence-level',
        '0.9',
        '--bootstrap-repeats',
        '500',
        '--seed',
        '41',
        '--minimum-trials',
        '4',
    ])

    assert cli.main() == exit_code

    output = experiment_dir / 'comparison-report.json'
    assert json.loads(output.read_text())['overall']['status'] == status
    assert received == {
        'plan': completed.plan,
        'records': {'candidate-measured-001': measured_trial.records},
        'options': {
            'reference': 'candidate',
            'candidate': 'reference',
            'confidence_level': 0.9,
            'bootstrap_repeats': 500,
            'seed': 41,
            'minimum_trials': 4,
            'dataset_sha256': 'd' * 64,
        },
        'validation': {
            'report': {'schema_version': 1, 'overall': {'status': status}},
            'records': dataset_records,
            'dataset_checksum': 'd' * 64,
        },
    }
    captured = capsys.readouterr()
    assert f'Comparison status: {status}' in captured.out
    assert f'Wrote comparison report to {output}' in captured.out


@pytest.mark.parametrize(
    ('error', 'exit_code', 'message'),
    (
        (ComparisonReportError('invalid generated report'), 3, 'Invalid comparison'),
        (OSError('dataset read failed'), 4, 'Comparison failed'),
    ),
)
def test_experiment_compare_distinguishes_invalid_and_operational_failures(
    tmp_path,
    monkeypatch,
    capsys,
    error,
    exit_code,
    message,
):
    importlib.reload(cli)
    experiment_dir = tmp_path / 'experiment'
    experiment_dir.mkdir()
    completed = argparse.Namespace(
        experiment_dir=experiment_dir,
        plan={'experiment_id': 'experiment-cli-compare'},
        measured_trials=(),
        experiment_complete=True,
        dataset_path=experiment_dir / 'dataset' / 'dashboard-data.jsonl',
        dataset_sha256='d' * 64,
    )
    report = {
        'schema_version': REPORT_SCHEMA_VERSION,
        'overall': {'status': INSUFFICIENT_EVIDENCE},
    }
    monkeypatch.setattr(cli, 'load_experiment_evidence', lambda path: completed)
    monkeypatch.setattr(cli, 'build_comparison_report', lambda *args, **kwargs: report)
    monkeypatch.setattr(cli, 'load_records', lambda path: ())

    def fail_validation(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, 'validate_comparison_report', fail_validation)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'compare',
        str(experiment_dir),
    ])

    assert cli.main() == exit_code
    assert not (experiment_dir / 'comparison-report.json').exists()
    assert message in capsys.readouterr().err


def test_experiment_compare_rejects_unverified_bundle_without_writing_report(
    tmp_path,
    monkeypatch,
    capsys,
):
    importlib.reload(cli)
    experiment_dir = tmp_path / 'experiment'

    def fail_load(_path):
        raise ExperimentError('experiment is incomplete')

    monkeypatch.setattr(cli, 'load_experiment_evidence', fail_load)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'compare',
        str(experiment_dir),
    ])

    assert cli.main() == 3
    assert not (experiment_dir / 'comparison-report.json').exists()
    assert 'Invalid comparison: experiment is incomplete' in capsys.readouterr().err


def test_experiment_compare_passes_every_workflow_option(monkeypatch, tmp_path):
    importlib.reload(cli)
    received = {}

    def fake_workflow(options):
        received['options'] = options
        return argparse.Namespace(
            dry_run=False,
            exit_code=2,
            dataset_path=tmp_path / 'comparison' / 'dataset' / 'dashboard-data.jsonl',
            report_path=tmp_path / 'comparison' / 'comparison-report.json',
        )

    monkeypatch.setattr(cli, 'run_comparison_workflow', fake_workflow)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'compare',
        '--reference-ref',
        'reference-branch',
        '--candidate-ref',
        'candidate-branch',
        '--rclcpp-repo-url',
        'https://example.test/rclcpp.git',
        '--ros-distro',
        'rolling',
        '--duration',
        '2',
        '--executor',
        'EventsExecutor',
        '--suite',
        'service-rclcpp-minimal',
        '--cpuset-cpus',
        '0-1',
        '--warmups',
        '2',
        '--repeats',
        '5',
        '--order',
        'balanced',
        '--seed',
        '17',
        '--cache-dir',
        str(tmp_path / 'cache'),
        '--container-repo-url',
        'https://example.test/benchmarks.git',
        '--container-ref',
        'stable',
        '--skip-build',
        '--dry-run',
        '--confidence-level',
        '0.9',
        '--bootstrap-repeats',
        '500',
        '--bootstrap-seed',
        '41',
        '--minimum-trials',
        '4',
        '--results-dir',
        str(tmp_path / 'comparison'),
    ])

    assert cli.main() == 2
    assert received['options'] == cli.ComparisonWorkflowOptions(
        results_dir=str(tmp_path / 'comparison'),
        reference_ref='reference-branch',
        candidate_ref='candidate-branch',
        ros_distro='rolling',
        suite='service-rclcpp-minimal',
        executor='EventsExecutor',
        duration=2,
        cpuset_cpus='0-1',
        warmups=2,
        repeats=5,
        order='balanced',
        schedule_seed=17,
        cache_dir=str(tmp_path / 'cache'),
        rclcpp_repository_url='https://example.test/rclcpp.git',
        container_repository_url='https://example.test/benchmarks.git',
        container_ref='stable',
        skip_build=True,
        dry_run=True,
        confidence_level=0.9,
        bootstrap_repeats=500,
        bootstrap_seed=41,
        minimum_trials=4,
        start_dashboard=False,
    )


def test_experiment_compare_rejects_interleaved_schedule(
    monkeypatch,
    tmp_path,
    capsys,
):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'run_comparison_workflow',
        lambda _options: pytest.fail('workflow must not start'),
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'compare',
        '--reference-ref',
        'reference-branch',
        '--candidate-ref',
        'candidate-branch',
        '--order',
        'interleaved',
        '--results-dir',
        str(tmp_path / 'comparison'),
    ])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert "invalid choice: 'interleaved'" in capsys.readouterr().err


def test_experiment_compare_returns_separate_operational_outcome(
    monkeypatch,
    capsys,
):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'run_comparison_workflow',
        lambda options: (_ for _ in ()).throw(
            cli.ComparisonWorkflowError('Docker daemon is not accessible')
        ),
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'compare',
        '--reference-ref',
        'reference',
        '--candidate-ref',
        'candidate',
        '--results-dir',
        'comparison',
    ])

    assert cli.main() == 4
    assert 'Docker daemon is not accessible' in capsys.readouterr().err


def test_experiment_report_keeps_report_stage_available(monkeypatch, tmp_path):
    importlib.reload(cli)
    experiment_dir = tmp_path / 'experiment'
    experiment_dir.mkdir()
    completed = argparse.Namespace(
        experiment_dir=experiment_dir,
        plan={'experiment_id': 'experiment-report'},
        measured_trials=(),
        dataset_sha256='d' * 64,
    )
    monkeypatch.setattr(cli, 'load_experiment_evidence', lambda path: completed)
    monkeypatch.setattr(
        cli,
        'build_comparison_report',
        lambda *args, **kwargs: {
            'schema_version': 2,
            'overall': {'status': NO_REGRESSION},
        },
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'experiment',
        'report',
        str(experiment_dir),
    ])

    assert cli.main() == 0
    assert (experiment_dir / 'comparison-report.json').is_file()


def test_run_with_default_smoke(monkeypatch):
    importlib.reload(cli)
    defaults = RunDefaults()
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return DEFAULT_CONTAINER_COMMIT

    def fake_benchmark_runner(**kwargs):
        received['benchmark_kwargs'] = kwargs

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        lambda image_spec, cache_dir: _verified_image(image_spec),
    )
    monkeypatch.setattr(cli, 'benchmark_runner', fake_benchmark_runner)
    monkeypatch.setattr(
        cli,
        'parse_command',
        lambda args: received.update(parse_args=args),
    )
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', '--duration', str(defaults.duration)],
    )
    cli.main()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': defaults.cache_dir,
    }
    benchmark_kwargs = received['benchmark_kwargs']
    assert benchmark_kwargs == {
        'results_dir': defaults.results_dir,
        'benchmark_option': defaults.default_benchmark,
        'duration': defaults.duration,
        'image_spec': benchmark_kwargs['image_spec'],
        'executor': defaults.executor,
        'keep_container': False,
        'cpuset_cpus': None,
    }
    assert benchmark_kwargs['image_spec'].client_target.source == 'packaged'
    assert received['parse_args'].results_dir == defaults.results_dir
    assert received['parse_args'].output == Path(defaults.results_dir) / 'normalized_metrics.jsonl'


def test_run_with_explicit_arguments(monkeypatch):
    importlib.reload(cli)
    received = {}

    def fake_setup_container_repo(**kwargs):
        received['container_kwargs'] = kwargs
        return DEFAULT_CONTAINER_COMMIT

    def fake_benchmark_runner(**kwargs):
        received['benchmark_kwargs'] = kwargs

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup_container_repo)
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        lambda image_spec, cache_dir: _verified_image(image_spec),
    )
    monkeypatch.setattr(cli, 'benchmark_runner', fake_benchmark_runner)
    monkeypatch.setattr(
        cli,
        'parse_command',
        lambda args: received.update(parse_args=args),
    )
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'ros2-performance-monitoring',
            'run',
            '--duration',
            '120',
            '--ros-distro',
            'rolling',
            '--executor',
            'MultiThreadedExecutor',
            './custom-results',
            '--cache-dir',
            '~/.cache/custom-ros2-performance-monitoring',
            '--container-repo-url',
            DEFAULT_CONTAINER_REPO_URL,
            '--container-ref',
            DEFAULT_CONTAINER_REF,
            '--suite',
            'service-rclcpp-minimal',
        ],
    )
    cli.main()
    assert received['container_kwargs'] == {
        'container_repo_url': DEFAULT_CONTAINER_REPO_URL,
        'container_ref': DEFAULT_CONTAINER_REF,
        'cache_dir': '~/.cache/custom-ros2-performance-monitoring',
    }
    benchmark_kwargs = received['benchmark_kwargs']
    assert benchmark_kwargs == {
        'results_dir': './custom-results',
        'benchmark_option': 'service-rclcpp-minimal',
        'duration': 120,
        'image_spec': benchmark_kwargs['image_spec'],
        'executor': 'MultiThreadedExecutor',
        'keep_container': False,
        'cpuset_cpus': None,
    }
    assert benchmark_kwargs['image_spec'].ros_distro == 'rolling'
    assert received['parse_args'].results_dir == './custom-results'
    assert received['parse_args'].output == Path('./custom-results/normalized_metrics.jsonl')


def test_run_reuses_retained_container_without_building(monkeypatch):
    importlib.reload(cli)
    received = {}

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(cli, 'benchmark_container_exists', lambda distro: True)
    monkeypatch.setattr(
        cli,
        'validate_benchmark_container',
        lambda image_spec: _verified_image(image_spec),
    )
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        lambda *args: pytest.fail('a retained container must skip the image build'),
    )
    monkeypatch.setattr(
        cli,
        'benchmark_runner',
        lambda **kwargs: received.update(benchmark_kwargs=kwargs),
    )
    monkeypatch.setattr(cli, 'parse_command', lambda args: None)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', '--keep-container'],
    )

    cli.main()

    assert received['benchmark_kwargs']['keep_container'] is True


def test_run_can_skip_build_when_image_exists(monkeypatch):
    importlib.reload(cli)
    received = {}

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(cli, 'benchmark_container_exists', lambda distro: False)
    monkeypatch.setattr(cli, 'benchmark_image_exists', lambda distro: True)
    monkeypatch.setattr(
        cli,
        'verify_benchmark_image',
        lambda image_spec: _verified_image(image_spec),
    )
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        lambda *args: pytest.fail('--skip-build must not invoke Buildx'),
    )
    monkeypatch.setattr(
        cli,
        'benchmark_runner',
        lambda **kwargs: received.update(benchmark_kwargs=kwargs),
    )
    monkeypatch.setattr(cli, 'parse_command', lambda args: None)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', '--skip-build'],
    )

    cli.main()

    assert received['benchmark_kwargs']['keep_container'] is False


def test_run_cannot_skip_missing_image(monkeypatch):
    importlib.reload(cli)

    monkeypatch.setattr(
        cli,
        'get_default_container_repo',
        lambda: (DEFAULT_CONTAINER_REPO_URL, DEFAULT_CONTAINER_REF),
    )
    monkeypatch.setattr(
        cli, 'setup_container_repo', lambda **kwargs: DEFAULT_CONTAINER_COMMIT
    )
    monkeypatch.setattr(cli, 'generation_rundata', lambda *args: None)
    monkeypatch.setattr(cli, 'benchmark_container_exists', lambda distro: False)
    monkeypatch.setattr(cli, 'benchmark_image_exists', lambda distro: False)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', '--skip-build'],
    )

    assert cli.main() == 1


def test_run_resolves_source_target_before_build_and_metadata(monkeypatch):
    importlib.reload(cli)
    calls = []
    source_target = ClientLibraryTarget(
        name='rclcpp',
        source='build',
        repository_url='https://github.com/example/rclcpp.git',
        requested_ref='feature/test',
        resolved_commit='b' * 40,
        checkout_path=Path('/cache/rclcpp'),
    )

    def fake_resolve(**kwargs):
        calls.append(('resolve', kwargs))
        return source_target

    def fake_setup(**kwargs):
        calls.append(('benchmark', kwargs))
        return DEFAULT_CONTAINER_COMMIT

    def fake_build(image_spec, cache_dir):
        calls.append(('build', image_spec.client_target))
        return _verified_image(image_spec)

    def fake_metadata(args, results_dir, image_spec, verified_image):
        calls.append(('metadata', image_spec.client_target, verified_image))

    monkeypatch.setattr(cli, 'resolve_rclcpp_target', fake_resolve)
    monkeypatch.setattr(cli, 'setup_container_repo', fake_setup)
    monkeypatch.setattr(cli, 'build_benchmark_image', fake_build)
    monkeypatch.setattr(cli, 'generation_rundata', fake_metadata)
    monkeypatch.setattr(cli, 'benchmark_runner', lambda **kwargs: None)
    monkeypatch.setattr(cli, 'parse_command', lambda args: None)
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'run',
        '--client-library-source',
        'build',
        '--client-library-repo-url',
        'https://github.com/example/rclcpp.git',
        '--client-library-ref',
        'feature/test',
    ])

    assert cli.main() is None

    assert [name for name, *_ in calls] == [
        'resolve', 'benchmark', 'build', 'metadata',
    ]
    assert calls[0][1] == {
        'repository_url': 'https://github.com/example/rclcpp.git',
        'requested_ref': 'feature/test',
        'cache_dir': RunDefaults().cache_dir,
    }
    assert calls[2][1] is source_target
    assert calls[3][1] is source_target


def test_source_build_requires_ref_before_docker_or_metadata(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'setup_container_repo',
        lambda **kwargs: pytest.fail('benchmark setup must not start'),
    )
    monkeypatch.setattr(
        cli,
        'build_benchmark_image',
        lambda *args: pytest.fail('Docker build must not start'),
    )
    monkeypatch.setattr(
        cli,
        'generation_rundata',
        lambda *args: pytest.fail('metadata must not be created'),
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'run',
        '--client-library-source',
        'build',
    ])

    assert cli.main() == 1


def test_user_supplied_client_commit_is_not_accepted(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'setup_container_repo',
        lambda **kwargs: pytest.fail('repository setup must not start'),
    )
    monkeypatch.setattr(sys, 'argv', [
        'ros2-performance-monitoring',
        'run',
        '--client-library-commit',
        'claimed-commit',
    ])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2


def test_run_with_invalid_duration_exits(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', '--duration', 'not-a-number'],
    )
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.parametrize('ros_distro', ('humble', 'kilted'))
def test_run_rejects_unsupported_ros_distro_before_setup(monkeypatch, ros_distro):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'setup_container_repo',
        lambda **kwargs: pytest.fail('repository setup must not run'),
    )
    monkeypatch.setattr(
        cli,
        'generation_rundata',
        lambda *args: pytest.fail('run metadata must not be created'),
    )
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'run', '--ros-distro', ros_distro],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2


def test_build_container_rejects_incompatible_ros_distro_before_setup(monkeypatch):
    importlib.reload(cli)
    monkeypatch.setattr(
        cli,
        'setup_container_repo',
        lambda **kwargs: pytest.fail('repository setup must not run'),
    )
    monkeypatch.setattr(
        sys,
        'argv',
        ['ros2-performance-monitoring', 'build-container', 'kilted'],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2


def test_parse_scopes_artifacts_to_metadata_distribution(tmp_path, monkeypatch):
    received = {}

    monkeypatch.setattr(
        cli,
        'latest_run_metadata',
        lambda _results_dir: {'run_configuration': {'ros_distro': 'rolling'}},
    )

    def fake_discover(results_dir, ros_distro=None):
        received['results_dir'] = results_dir
        received['ros_distro'] = ros_distro
        return ()

    monkeypatch.setattr(cli, 'discover_benchmark_artifacts', fake_discover)
    monkeypatch.setattr(cli, 'write_jsonl', lambda _records, _output: 0)

    cli.parse_command(argparse.Namespace(
        results_dir=str(tmp_path),
        output=str(tmp_path / 'metrics.jsonl'),
    ))

    assert received == {
        'results_dir': str(tmp_path),
        'ros_distro': 'rolling',
    }


def _verified_image(image_spec):
    return VerifiedImage(
        image_name=image_spec.image_name,
        image_id=f'sha256:{"d" * 64}',
        image_digest=f'sha256:{"e" * 64}',
        target_key=image_spec.target_key,
    )
