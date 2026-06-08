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
import subprocess


def benchmark_runner(
    cache_dir: str,
    results_dir: str,
    benchmark_option: str,
    duration: int,
    ros_distro: str,
) -> None:
    relative_path = Path(cache_dir)
    absolute_path = relative_path.expanduser().resolve()
    container_repo = absolute_path
    benchmark_folder = container_repo / 'benchmark'

    results_absolute_path = Path(results_dir).expanduser().resolve()

    if benchmark_option != 'pubsub-rclcpp-minimal':
        raise ValueError('Unsupported Benchmark option')

    benchmark_results_dir = results_absolute_path / 'benchmark' / ros_distro
    benchmark_results_dir.mkdir(parents=True, exist_ok=True)
    container_name = f'ros2-benchmark-container-{ros_distro}-amd64'

    cmd = [
        'docker', 'run', '-d',
        '--network=host',
        '--privileged',
        '--shm-size=1000mb',
        '-v', f'{benchmark_results_dir}:/benchmark_results',
        '-v', f'{benchmark_folder}:/ws/src/ros2_benchmark_container/benchmark',
        '-v', '/var/run/docker.sock:/var/run/docker.sock',
        '-e', 'ROS_DOMAIN_ID=28',
        '-e', 'SYSTEM_EXECUTOR=EventsExecutor',
        '--name', container_name,
        f'ros2-benchmark-container:{ros_distro}-amd64',
        'sleep', 'infinity',
    ]

    subprocess.run(['docker', 'rm', '-f', container_name], check=False)
    subprocess.run(cmd, check=True)

    exec_cmd = [
        'docker', 'exec',
        '-e', 'ROS2_BENCHMARK_SCRIPTS_DIR=/ws/src/ros2_benchmark_container/benchmark/scripts',
        '-e', 'ROS2_BENCHMARK_OUTPUT_DIR=/benchmark_results',
        '-e', f'ROS2_BENCHMARK_TEST_DURATION={duration}',
        container_name,
        'bash', '-lc',
        'source /ws/install/setup.bash && /ws/src/ros2_benchmark_container/'
        'benchmark/scripts/runners/run_single_process_benchmark.sh /ws/src/'
        'ros2_benchmark_container/benchmark/test-matrix/single_process_pub_sub.conf',
    ]

    try:
        print('Starting Single Process Pub/Sub rclcpp minimal inside container...')
        subprocess.run(exec_cmd, check=True)
        print('Benchmark Completed Successfully :)')
    finally:
        subprocess.run(['docker', 'rm', '-f', container_name], check=False)
