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
from pathlib import Path
import subprocess
import sys
from typing import Any

from .artifacts import ArtifactError
from .artifacts import discover_benchmark_artifacts
from .benchmark_image import benchmark_container_exists
from .benchmark_image import benchmark_image_exists
from .benchmark_image import BenchmarkImageSpec
from .benchmark_image import build_benchmark_image
from .benchmark_image import detect_architecture
from .benchmark_image import validate_benchmark_container
from .benchmark_image import verify_benchmark_image
from .benchmark_runner import benchmark_runner
from .client_target import ClientLibraryTarget
from .client_target import DEFAULT_RCLCPP_REPOSITORY
from .client_target import resolve_rclcpp_target
from .config import RunDefaults
from .config import SUPPORTED_ROS_DISTROS
from .container_provider import get_default_container_repo, setup_container_repo
from .dashboard import dashboard_down
from .dashboard import dashboard_up
from .dataset import build_dataset
from .dataset import DatasetError
from .experiment import build_experiment_plan
from .experiment import run_experiment
from .exporters.prometheus import serve_metrics
from .parsers.ros2_benchmark_container import latest_run_metadata
from .parsers.ros2_benchmark_container import parse_artifact
from .run_metadata import generation_rundata
from .writers.jsonl import write_jsonl


class CommandArgumentParser(argparse.ArgumentParser):

    def error(self, message: str) -> None:
        unknown_command = (
            'invalid choice' in message
            and (
                'argument command:' in message
                or 'argument dashboard_command:' in message
                or 'argument dataset_command:' in message
                or 'argument experiment_command:' in message
            )
        )
        if unknown_command:
            message += "\nRun 'ros2-performance-monitoring help' to see available commands."
        super().error(message)


def run_command(args: argparse.Namespace) -> None:
    print('Running Performance Monitor...')
    image_spec = _prepare_image_spec(args)
    print(
        'Benchmark repository is ready at commit: '
        f'{image_spec.benchmark_resolved_commit}'
    )
    reuse_container = (
        args.keep_container and benchmark_container_exists(image_spec)
    )
    if args.skip_build:
        if not benchmark_image_exists(image_spec):
            raise RuntimeError(
                f'Cannot skip build: exact target image {image_spec.image_name} '
                'does not exist.'
            )
        if reuse_container:
            verified_image = validate_benchmark_container(image_spec)
            print(f'Using verified retained container: {image_spec.container_name}')
        else:
            verified_image = verify_benchmark_image(image_spec)
            print(f'Using verified benchmark image: {image_spec.image_name}')
    elif reuse_container:
        verified_image = validate_benchmark_container(image_spec)
        print(
            'Verified retained benchmark container; skipping image build: '
            f'{image_spec.container_name}'
        )
    else:
        verified_image = build_benchmark_image(image_spec, args.cache_dir)
        print(f'Successfully built verified image: {verified_image.image_name}')
    generation_rundata(args, args.results_dir, image_spec, verified_image)
    benchmark_runner(
        results_dir=args.results_dir,
        benchmark_option=args.suite,
        duration=args.duration,
        image_spec=image_spec,
        executor=args.executor,
        keep_container=args.keep_container,
        cpuset_cpus=args.cpuset_cpus,
    )
    parse_command(argparse.Namespace(
        results_dir=args.results_dir,
        output=Path(args.results_dir) / 'normalized_metrics.jsonl',
    ))


def parse_command(args: argparse.Namespace) -> None:
    try:
        run_metadata = latest_run_metadata(args.results_dir)
        ros_distro = run_metadata.get('run_configuration', {}).get('ros_distro')
        artifacts = discover_benchmark_artifacts(args.results_dir, ros_distro=ros_distro)
        records = []
        for artifact in artifacts:
            records.extend(parse_artifact(artifact, run_metadata))
        count = write_jsonl(records, args.output)
    except (ArtifactError, FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f'Wrote {count} normalized metrics to {args.output}')


def bring_up_dashboard(args: argparse.Namespace) -> None:
    try:
        dashboard_up(args.input)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def bring_down_dashboard(args: argparse.Namespace) -> None:
    try:
        dashboard_down()
    except (FileNotFoundError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


def serve_prometheus(args: argparse.Namespace) -> None:
    try:
        serve_metrics(args.input, args.port)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def doctor_command(args: argparse.Namespace) -> None:
    # TODO: Implement environment checks for the doctor command.
    print('Doctor checks are not implemented yet.')


def help_command(args: argparse.Namespace) -> None:
    args.root_parser.print_help()
    print('\nCommand usage:')
    for command_parser in args.command_parsers:
        print(command_parser.format_usage().strip())


def build_container_command(args: argparse.Namespace) -> None:
    print('Building the container now...')
    image_spec = _prepare_image_spec(args)
    verified_image = build_benchmark_image(image_spec, args.cache_dir)
    print(f'Successfully built verified image: {verified_image.image_name}')


def _prepare_image_spec(args: argparse.Namespace) -> BenchmarkImageSpec:
    client_target = _prepare_client_target(args)
    default_repo_url, default_ref = get_default_container_repo()
    container_repo_url = args.container_repo_url or default_repo_url
    container_ref = args.container_ref or default_ref
    benchmark_commit = setup_container_repo(
        container_repo_url=container_repo_url,
        container_ref=container_ref,
        cache_dir=args.cache_dir,
    )
    return BenchmarkImageSpec(
        ros_distro=args.ros_distro,
        architecture=detect_architecture(),
        benchmark_repository_url=container_repo_url,
        benchmark_requested_ref=container_ref,
        benchmark_resolved_commit=benchmark_commit,
        client_target=client_target,
    )


def _prepare_client_target(args: argparse.Namespace) -> ClientLibraryTarget:
    if args.client_library_source == 'packaged':
        if args.client_library_repo_url or args.client_library_ref:
            raise RuntimeError(
                '--client-library-repo-url and --client-library-ref require '
                '--client-library-source build'
            )
        return ClientLibraryTarget.packaged(args.ros_distro)
    if not args.client_library_ref:
        raise RuntimeError(
            '--client-library-ref is required with --client-library-source build'
        )
    repository_url = args.client_library_repo_url or DEFAULT_RCLCPP_REPOSITORY
    return resolve_rclcpp_target(
        repository_url=repository_url,
        requested_ref=args.client_library_ref,
        cache_dir=args.cache_dir,
    )


def dataset_build_command(args: argparse.Namespace) -> None:
    try:
        result = build_dataset(
            args.inputs,
            args.output,
            exclude_runs=args.exclude_run,
            aggregate=args.aggregate,
        )
    except (DatasetError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    for message in result.skipped_groups:
        print(message, file=sys.stderr)
    print(
        f'Wrote {result.record_count} normalized metrics across '
        f'{result.run_count} runs to {args.output}'
    )
    print(f'Wrote dataset manifest to {result.manifest_path}')


def experiment_run_command(args: argparse.Namespace) -> None:
    """Prepare two exact targets and execute or resume an experiment bundle."""
    client_targets = {
        label: _prepare_experiment_client_target(args, label)
        for label in ('reference', 'candidate')
    }
    default_repo_url, default_ref = get_default_container_repo()
    container_repo_url = args.container_repo_url or default_repo_url
    container_ref = args.container_ref or default_ref
    benchmark_commit = setup_container_repo(
        container_repo_url=container_repo_url,
        container_ref=container_ref,
        cache_dir=args.cache_dir,
    )
    architecture = detect_architecture()
    image_specs = {
        label: BenchmarkImageSpec(
            ros_distro=args.ros_distro,
            architecture=architecture,
            benchmark_repository_url=container_repo_url,
            benchmark_requested_ref=container_ref,
            benchmark_resolved_commit=benchmark_commit,
            client_target=client_targets[label],
        )
        for label in ('reference', 'candidate')
    }
    if image_specs['reference'].target_key == image_specs['candidate'].target_key:
        raise RuntimeError('reference and candidate targets must be different')

    verified_images = {}
    for label in ('reference', 'candidate'):
        image_spec = image_specs[label]
        if benchmark_image_exists(image_spec):
            verified_image = verify_benchmark_image(image_spec)
            print(f'Using verified {label} image: {image_spec.image_name}')
        elif args.skip_build:
            raise RuntimeError(
                f'Cannot skip build: exact {label} target image '
                f'{image_spec.image_name} does not exist.'
            )
        else:
            verified_image = build_benchmark_image(image_spec, args.cache_dir)
            print(f'Built verified {label} image: {image_spec.image_name}')
        verified_images[label] = verified_image

    plan = build_experiment_plan(
        image_specs,
        verified_images,
        suite=args.suite,
        executor=args.executor,
        duration=args.duration,
        cpuset_cpus=args.cpuset_cpus,
        warmup_count=args.warmups,
        measured_repeat_count=args.repeats,
        order=args.order,
        seed=args.seed,
    )
    result = run_experiment(
        args.experiment_dir,
        plan,
        image_specs,
        verified_images,
    )
    print(
        f'Experiment {result.experiment_id} is complete: '
        f'{result.completed_trials} trials ({result.reused_trials} reused)'
    )
    print(f'Comparison dataset: {result.dataset_path}')


def _prepare_experiment_client_target(args, label):
    source = getattr(args, f'{label}_source')
    requested_ref = getattr(args, f'{label}_ref')
    repository_url = getattr(args, f'{label}_repo_url')
    if source == 'packaged':
        if requested_ref or repository_url:
            raise RuntimeError(
                f'--{label}-ref and --{label}-repo-url require '
                f'--{label}-source build'
            )
        return ClientLibraryTarget.packaged(args.ros_distro)
    if not requested_ref:
        raise RuntimeError(f'--{label}-ref is required with --{label}-source build')
    return resolve_rclcpp_target(
        repository_url=repository_url or DEFAULT_RCLCPP_REPOSITORY,
        requested_ref=requested_ref,
        cache_dir=args.cache_dir,
    )


def _positive_integer(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError('value must be a positive integer')
    return parsed


def _non_negative_integer(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('value must be a non-negative integer')
    return parsed


def main() -> Any:
    defaults = RunDefaults()
    parser = CommandArgumentParser(prog='ros2-performance-monitoring')
    subparsers = parser.add_subparsers(dest='command', required=True)

    run_parser = subparsers.add_parser('run', help='Start monitoring')
    run_parser.set_defaults(func=run_command)

    doctor_parser = subparsers.add_parser('doctor', help='Check setup')
    doctor_parser.set_defaults(func=doctor_command)

    build_container_parser = subparsers.add_parser(
        'build-container',
        help='Builds the container',
    )
    build_container_parser.set_defaults(func=build_container_command)

    parse_parser = subparsers.add_parser('parse', help='Parse raw benchmark artifacts')
    parse_parser.set_defaults(func=parse_command)

    dashboard_parser = subparsers.add_parser('dashboard', help='Manage local dashboard')
    dashboard_subparsers = dashboard_parser.add_subparsers(
        dest='dashboard_command',
        required=True,
    )
    dashboard_up_parser = dashboard_subparsers.add_parser('up', help='Start local dashboard')
    dashboard_up_parser.set_defaults(func=bring_up_dashboard)
    dashboard_down_parser = dashboard_subparsers.add_parser('down', help='Stop local dashboard')
    dashboard_down_parser.set_defaults(func=bring_down_dashboard)

    dataset_parser = subparsers.add_parser(
        'dataset',
        help='Build comparison datasets',
    )
    dataset_subparsers = dataset_parser.add_subparsers(
        dest='dataset_command',
        required=True,
    )
    dataset_build_parser = dataset_subparsers.add_parser(
        'build',
        help='Combine normalized runs into one comparison dataset',
    )
    dataset_build_parser.set_defaults(func=dataset_build_command)

    experiment_parser = subparsers.add_parser(
        'experiment',
        help='Run controlled repeated comparisons',
    )
    experiment_subparsers = experiment_parser.add_subparsers(
        dest='experiment_command',
        required=True,
    )
    experiment_run_parser = experiment_subparsers.add_parser(
        'run',
        help='Create or safely resume an experiment bundle',
    )
    experiment_run_parser.set_defaults(func=experiment_run_command)

    serve_prometheus_parser = subparsers.add_parser(
        'serve-prometheus',
        help='Serve normalized metrics for Prometheus',
    )
    serve_prometheus_parser.set_defaults(func=serve_prometheus)

    help_parser = subparsers.add_parser('help', help='Show commands and usage')

    run_parser.add_argument(
        '-t', '--duration', type=int, default=defaults.duration,
        help='Duration in Seconds',
    )
    run_parser.add_argument(
        '-d', '--ros-distro', choices=SUPPORTED_ROS_DISTROS, default=defaults.ros_distro,
        help='ROS Distro',
    )
    run_parser.add_argument(
        '-x', '--executor', default=defaults.executor,
        help='Executor',
    )
    run_parser.add_argument(
        'results_dir', nargs='?', default=defaults.results_dir,
        help='Results directory for Container Run Results',
    )
    run_parser.add_argument(
        '--cache-dir', default=defaults.cache_dir,
        help='Cache directory for the container repository',
    )
    run_parser.add_argument(
        '--container-repo-url',
        help='Container Repo URL',
    )
    run_parser.add_argument(
        '--container-ref',
        help='Container Repository Ref',
    )
    run_parser.add_argument(
        '--suite', default=defaults.default_benchmark,
        help='Benchmark suite to run',
    )
    run_parser.add_argument(
        '--keep-container', action='store_true',
        help='Keep and reuse the exact-target benchmark container between runs',
    )
    run_parser.add_argument(
        '--skip-build', action='store_true',
        help='Use the verified exact-target image instead of invoking Buildx',
    )
    run_parser.add_argument(
        '--cpuset-cpus',
        help='Restrict the benchmark container to a Docker CPU-set expression',
    )
    run_parser.add_argument(
        '--client-library', default=defaults.client_library,
        choices=('rclcpp',),
        help='Client library under test',
    )
    run_parser.add_argument(
        '--client-library-ref', default=defaults.client_library_ref,
        help='rclcpp branch, tag, or commit to resolve and build',
    )
    run_parser.add_argument(
        '--client-library-source', default=defaults.client_library_source,
        choices=('build', 'packaged'),
        help='Build rclcpp from a ref or use ROS packages',
    )
    run_parser.add_argument(
        '--client-library-repo-url',
        help=f'rclcpp repository URL (build default: {DEFAULT_RCLCPP_REPOSITORY})',
    )
    build_container_parser.add_argument(
        'ros_distro', nargs='?', choices=SUPPORTED_ROS_DISTROS, default=defaults.ros_distro,
        help='ROS Distro',
    )
    build_container_parser.add_argument(
        'cache_dir', nargs='?', default=defaults.cache_dir,
        help='Cache Directory where fetched repo code is',
    )
    build_container_parser.add_argument(
        '--container-repo-url',
        help='Container Repo URL',
    )
    build_container_parser.add_argument(
        '--container-ref',
        help='Container Repository Ref',
    )
    build_container_parser.add_argument(
        '--client-library', default=defaults.client_library,
        choices=('rclcpp',),
        help='Client library under test',
    )
    build_container_parser.add_argument(
        '--client-library-ref', default=defaults.client_library_ref,
        help='rclcpp branch, tag, or commit to build',
    )
    build_container_parser.add_argument(
        '--client-library-source', default=defaults.client_library_source,
        choices=('build', 'packaged'),
        help='Build rclcpp from a ref or use ROS packages',
    )
    build_container_parser.add_argument(
        '--client-library-repo-url',
        help=f'rclcpp repository URL (build default: {DEFAULT_RCLCPP_REPOSITORY})',
    )
    parse_parser.add_argument('results_dir', help='Results directory created by run')
    parse_parser.add_argument('--output', required=True, help='JSONL output path')
    dataset_build_parser.add_argument(
        'inputs',
        nargs='+',
        help='Normalized JSONL input paths',
    )
    dataset_build_parser.add_argument(
        '--output',
        required=True,
        help='Comparison dataset JSONL output path',
    )
    dataset_build_parser.add_argument(
        '--aggregate',
        choices=('median',),
        help='Add aggregate runs for compatible repeated measurements',
    )
    dataset_build_parser.add_argument(
        '--exclude-run',
        action='append',
        default=[],
        help='Run ID to omit; may be repeated',
    )
    experiment_run_parser.add_argument(
        'experiment_dir',
        help='Experiment bundle directory to create or resume',
    )
    experiment_run_parser.add_argument(
        '-t', '--duration', type=_positive_integer, default=defaults.duration,
        help='Duration in seconds for every trial scenario',
    )
    experiment_run_parser.add_argument(
        '-d', '--ros-distro', choices=SUPPORTED_ROS_DISTROS, default=defaults.ros_distro,
        help='ROS distribution shared by both targets',
    )
    experiment_run_parser.add_argument(
        '-x', '--executor', default=defaults.executor,
        help='Executor shared by every trial',
    )
    experiment_run_parser.add_argument(
        '--suite', default=defaults.default_benchmark,
        help='Benchmark suite shared by every trial',
    )
    experiment_run_parser.add_argument(
        '--cpuset-cpus',
        help='Restrict every trial to a Docker CPU-set expression',
    )
    experiment_run_parser.add_argument(
        '--warmups', type=_non_negative_integer, default=1,
        help='Warm-up trials per target (default: 1)',
    )
    experiment_run_parser.add_argument(
        '--repeats', type=_positive_integer, default=3,
        help='Measured trials per target (default: 3)',
    )
    experiment_run_parser.add_argument(
        '--order', choices=('balanced', 'interleaved'), default='balanced',
        help='Target scheduling policy (default: balanced)',
    )
    experiment_run_parser.add_argument(
        '--seed', type=int, default=0,
        help='Deterministic scheduling seed (default: 0)',
    )
    experiment_run_parser.add_argument(
        '--cache-dir', default=defaults.cache_dir,
        help='Cache directory for benchmark and target repositories',
    )
    experiment_run_parser.add_argument('--container-repo-url')
    experiment_run_parser.add_argument('--container-ref')
    experiment_run_parser.add_argument(
        '--skip-build', action='store_true',
        help='Require both exact verified target images to exist locally',
    )
    for label in ('reference', 'candidate'):
        experiment_run_parser.add_argument(
            f'--{label}-source',
            choices=('build', 'packaged'),
            default='build',
            help=f'Use a source-built or packaged rclcpp {label} target',
        )
        experiment_run_parser.add_argument(
            f'--{label}-ref',
            help=f'rclcpp branch, tag, or commit for the {label} source target',
        )
        experiment_run_parser.add_argument(
            f'--{label}-repo-url',
            help=f'rclcpp repository URL for the {label} source target',
        )
    dashboard_up_parser.add_argument(
        '--input',
        required=True,
        help='Normalized metrics JSONL path',
    )
    serve_prometheus_parser.add_argument(
        '--input',
        required=True,
        help='Normalized metrics JSONL path',
    )
    serve_prometheus_parser.add_argument('--port', type=int, default=9108, help='Exporter port')
    help_parser.set_defaults(
        func=help_command,
        root_parser=parser,
        command_parsers=(
            run_parser,
            doctor_parser,
            build_container_parser,
            parse_parser,
            dataset_build_parser,
            experiment_run_parser,
            dashboard_up_parser,
            dashboard_down_parser,
            serve_prometheus_parser,
            help_parser,
        ),
    )
    args = parser.parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as error:
        print(f'Command failed with exit code {error.returncode}: {error.cmd}', file=sys.stderr)
        return error.returncode
    except RuntimeError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
