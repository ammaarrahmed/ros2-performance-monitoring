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
import sys
from typing import Any

from .config import RunDefaults
from .container_build import build_container
from .container_provider import get_default_container_repo, setup_container_repo
from .run_metadata import generation_rundata


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


def doctor_command(args: argparse.Namespace) -> None:
    print('Checking environment...')


def build_container_command(args: argparse.Namespace) -> None:
    print('Building the container now...')
    rel_path = build_container(
        ros_distro=args.ros_distro,
        cache_dir=args.cache_dir,
    )
    print(f'successfully built container at : {rel_path}')


def main() -> Any:
    defaults = RunDefaults()
    parser = argparse.ArgumentParser(prog='ros2-performance-monitoring')
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

    run_parser.add_argument(
        'duration', nargs='?', type=int, default=defaults.duration,
        help='Duration in Seconds',
    )
    run_parser.add_argument(
        'ros_distro', nargs='?', default=defaults.ros_distro,
        help='ROS Distro',
    )
    run_parser.add_argument(
        'executor', nargs='?', default=defaults.executor,
        help='Executor',
    )
    run_parser.add_argument(
        'results_dir', nargs='?', default=defaults.results_dir,
        help='Results directory for Container Run Results',
    )
    run_parser.add_argument(
        'cache_dir', nargs='?', default=defaults.cache_dir,
        help='Cache Directory for Container repo',
    )
    run_parser.add_argument(
        'container_repo_url', nargs='?',
        help='Container Repo URL',
    )
    run_parser.add_argument(
        'container_ref', nargs='?',
        help='Container Repository Ref',
    )
    build_container_parser.add_argument(
        'ros_distro', nargs='?', default=defaults.ros_distro,
        help='ROS Distro',
    )
    build_container_parser.add_argument(
        'cache_dir', nargs='?', default=defaults.cache_dir,
        help='Cache Directory where fetched repo code is',
    )
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
