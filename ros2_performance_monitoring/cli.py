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
from .benchmark_runner import benchmark_container_exists
from .benchmark_runner import benchmark_image_exists
from .benchmark_runner import benchmark_runner
from .config import RunDefaults
from .config import SUPPORTED_ROS_DISTROS
from .container_build import build_container
from .container_provider import get_default_container_repo, setup_container_repo
from .dashboard import dashboard_down
from .dashboard import dashboard_up
from .dataset import build_dataset
from .dataset import DatasetError
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
            )
        )
        if unknown_command:
            message += "\nRun 'ros2-performance-monitoring help' to see available commands."
        super().error(message)


def run_command(args: argparse.Namespace) -> None:
    print('Running Performance Monitor...')
    container_repo_url, container_ref = get_default_container_repo()
    if args.container_repo_url is None:
        args.container_repo_url = container_repo_url
    if args.container_ref is None:
        args.container_ref = container_ref
    commit_hash = setup_container_repo(
        container_repo_url=args.container_repo_url,
        container_ref=args.container_ref,
        cache_dir=args.cache_dir,
    )
    print(f'Container Repo Loaded is ready now! checked out commit : {commit_hash}')
    generation_rundata(args, args.results_dir, commit_hash)
    reuse_container = (
        args.keep_container and benchmark_container_exists(args.ros_distro)
    )
    if args.skip_build:
        if not benchmark_image_exists(args.ros_distro):
            raise RuntimeError(
                f'Cannot skip build: ros2-benchmark-container:'
                f'{args.ros_distro}-amd64 does not exist.'
            )
        print('Using existing benchmark image; skipping image build.')
    elif reuse_container:
        print('Retained benchmark container found; skipping image build.')
    else:
        rel_path = build_container(
            ros_distro=args.ros_distro,
            cache_dir=args.cache_dir,
        )
        print(f'successfully built container at : {rel_path}')
    benchmark_runner(
        cache_dir=args.cache_dir,
        results_dir=args.results_dir,
        benchmark_option=args.suite,
        duration=args.duration,
        ros_distro=args.ros_distro,
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
    container_repo_url, container_ref = get_default_container_repo()
    commit_hash = setup_container_repo(
        container_repo_url=container_repo_url,
        container_ref=container_ref,
        cache_dir=args.cache_dir,
    )
    print(f'Container Repo Loaded is ready now! checked out commit : {commit_hash}')
    rel_path = build_container(
        ros_distro=args.ros_distro,
        cache_dir=args.cache_dir,
    )
    print(f'successfully built container at : {rel_path}')


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
        help='Keep and reuse the distro benchmark container between runs',
    )
    run_parser.add_argument(
        '--skip-build', action='store_true',
        help='Use the existing distro image instead of invoking Buildx',
    )
    run_parser.add_argument(
        '--cpuset-cpus',
        help='Restrict the benchmark container to a Docker CPU-set expression',
    )
    run_parser.add_argument(
        '--client-library', default=defaults.client_library,
        help='Client library under test',
    )
    run_parser.add_argument(
        '--client-library-ref', default=defaults.client_library_ref,
        help='Client library branch or ref under test',
    )
    run_parser.add_argument(
        '--client-library-commit', default=defaults.client_library_commit,
        help='Resolved client library commit under test',
    )
    run_parser.add_argument(
        '--client-library-source', default=defaults.client_library_source,
        help='Whether the client library under test is a build or packaged',
    )
    build_container_parser.add_argument(
        'ros_distro', nargs='?', choices=SUPPORTED_ROS_DISTROS, default=defaults.ros_distro,
        help='ROS Distro',
    )
    build_container_parser.add_argument(
        'cache_dir', nargs='?', default=defaults.cache_dir,
        help='Cache Directory where fetched repo code is',
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
