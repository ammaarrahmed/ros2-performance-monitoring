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

import os
from pathlib import Path
import subprocess


FASTDDS_PROFILE = 'shared_memory_fastdds_preallocated_w_realloc.xml'
CYCLONEDDS_PROFILE = 'shared_memory_cyclonedds.xml'
ZENOH_SESSION_PROFILE = 'ZENOH_DEFAULT_SESSION_CONFIG.json5'
BROKEN_MULTI_PROCESS_COMMAND = (
    'COMMAND="${IROBOT_BENCHMARK} ${TOP1_PATH} ${TOP2_PATH} --executor ${EXECUTOR_ARG} '
    '${THREADS_OPTION} --ipc off -t ${ROS2_BENCHMARK_TEST_DURATION} -s 1000 --csv-out on '
    '--results-dir ${RESULT_FOLDER      echo -e "     Command: \\n       $COMMAND"'
)
FIXED_MULTI_PROCESS_COMMAND = (
    'COMMAND="${IROBOT_BENCHMARK} ${TOP1_PATH} ${TOP2_PATH} --executor ${EXECUTOR_ARG} '
    '${THREADS_OPTION} --ipc off -t ${ROS2_BENCHMARK_TEST_DURATION} -s 1000 --csv-out on '
    '--results-dir ${RESULT_FOLDER}"\n'
    '      echo -e "     Command: \\n       $COMMAND"'
)

REDUCED_PUBSUB_SINGLE_PROCESS_CONFIG = '\n'.join((
    '# Generated reduced Pub/Sub benchmark config',
    'OUTPUT_DIR_NAME="pub-sub_single_process"',
    'TOPOLOGIES=(',
    '  "pub_sub_200hz_10b"',
    '  "pub_sub_200hz_100kb"',
    '  "pub_sub_200hz_1mb"',
    '  "pub_sub_200hz_4mb"',
    ')',
    'TOPOLOGIES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../topologies/pub-sub"',
    'PROFILES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../profiles"',
    'RMW_LIST=("fastrtps" "cyclonedds" "zenoh")',
    'COMMS_fastrtps=("ipc_on" "ipc_off" "loaned")',
    'LOANED_ENV_VARS_fastrtps=(',
    f'  "export FASTRTPS_DEFAULT_PROFILES_FILE=${{PROFILES_DIR}}/{FASTDDS_PROFILE}"',
    '  "export RMW_FASTRTPS_USE_QOS_FROM_XML=1"',
    ')',
    'COMMS_cyclonedds=("ipc_off")',
    'LOANED_ENV_VARS_cyclonedds=(',
    f'  "export CYCLONEDDS_URI=${{PROFILES_DIR}}/{CYCLONEDDS_PROFILE}"',
    ')',
    'COMMS_zenoh=("ipc_on" "ipc_off")',
    f'ZENOH_SESSION_CONFIG_URI=${{PROFILES_DIR}}/{ZENOH_SESSION_PROFILE}',
    '',
))

REDUCED_PUBSUB_MULTI_PROCESS_CONFIG = '\n'.join((
    '# Generated reduced Pub/Sub multi-process config',
    'OUTPUT_DIR_NAME="pub-sub_multi_process"',
    'TOPOLOGIES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../topologies/pub-sub"',
    'PROFILES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../profiles"',
    'RESULTS=("10b" "100kb" "1mb" "4mb")',
    'TOPOLOGY1=("pub_200hz_10b" "pub_200hz_100kb" "pub_200hz_1mb" "pub_200hz_4mb")',
    'TOPOLOGY2=("sub_10b" "sub_100kb" "sub_1mb" "sub_4mb")',
    'RMW_LIST=("fastrtps" "cyclonedds" "zenoh")',
    'COMMS_fastrtps=("ipc_off" "loaned")',
    'LOANED_ENV_VARS_fastrtps=(',
    f'  "export FASTRTPS_DEFAULT_PROFILES_FILE=${{PROFILES_DIR}}/{FASTDDS_PROFILE}"',
    '  "export RMW_FASTRTPS_USE_QOS_FROM_XML=1"',
    ')',
    'COMMS_cyclonedds=("ipc_off")',
    'LOANED_ENV_VARS_cyclonedds=(',
    f'  "export CYCLONEDDS_URI=${{PROFILES_DIR}}/{CYCLONEDDS_PROFILE}"',
    ')',
    'COMMS_zenoh=("ipc_off")',
    f'ZENOH_SESSION_CONFIG_URI=${{PROFILES_DIR}}/{ZENOH_SESSION_PROFILE}',
    '',
))

REDUCED_SERVICE_SINGLE_PROCESS_CONFIG = '\n'.join((
    '# Generated reduced Client/Service config',
    'OUTPUT_DIR_NAME="cli-srv_single_process"',
    'TOPOLOGIES=("cli_srv_10b" "cli_srv_100kb" "cli_srv_1mb" "cli_srv_4mb")',
    'TOPOLOGIES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../topologies/cli-srv"',
    'PROFILES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../profiles"',
    'RMW_LIST=("fastrtps" "cyclonedds" "zenoh")',
    'COMMS_fastrtps=("ipc_on" "ipc_off")',
    'COMMS_cyclonedds=("ipc_off")',
    'COMMS_zenoh=("ipc_on" "ipc_off")',
    f'ZENOH_SESSION_CONFIG_URI=${{PROFILES_DIR}}/{ZENOH_SESSION_PROFILE}',
    '',
))

REDUCED_SERVICE_MULTI_PROCESS_CONFIG = '\n'.join((
    '# Generated reduced Client/Service multi-process config',
    'OUTPUT_DIR_NAME="cli-srv_multi_process"',
    'TOPOLOGIES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../topologies/cli-srv"',
    'PROFILES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../profiles"',
    'RESULTS=("10b" "100kb" "1mb" "4mb")',
    'TOPOLOGY1=("cli_10b" "cli_100kb" "cli_1mb" "cli_4mb")',
    'TOPOLOGY2=("srv_10b" "srv_100kb" "srv_1mb" "srv_4mb")',
    'RMW_LIST=("fastrtps" "cyclonedds" "zenoh")',
    'COMMS_fastrtps=("ipc_off")',
    'COMMS_cyclonedds=("ipc_off")',
    'COMMS_zenoh=("ipc_off")',
    f'ZENOH_SESSION_CONFIG_URI=${{PROFILES_DIR}}/{ZENOH_SESSION_PROFILE}',
    '',
))

BENCHMARK_SUITES = {
    'pubsub-rclcpp-minimal': (
        (
            'Single Process Pub/Sub rclcpp minimal',
            'run_single_process_benchmark.sh',
            'pubsub_single_process_reduced.conf',
            REDUCED_PUBSUB_SINGLE_PROCESS_CONFIG,
        ),
        (
            'Multi Process Pub/Sub rclcpp minimal',
            'run_multi_process_benchmark.sh',
            'pubsub_multi_process_reduced.conf',
            REDUCED_PUBSUB_MULTI_PROCESS_CONFIG,
        ),
    ),
    'service-rclcpp-minimal': (
        (
            'Single Process Service rclcpp minimal',
            'run_single_process_benchmark.sh',
            'service_single_process_reduced.conf',
            REDUCED_SERVICE_SINGLE_PROCESS_CONFIG,
        ),
        (
            'Multi Process Service rclcpp minimal',
            'run_multi_process_benchmark.sh',
            'service_multi_process_reduced.conf',
            REDUCED_SERVICE_MULTI_PROCESS_CONFIG,
        ),
    ),
}
BENCHMARK_SUITES['rclcpp-minimal'] = (
    BENCHMARK_SUITES['pubsub-rclcpp-minimal'] + BENCHMARK_SUITES['service-rclcpp-minimal']
)


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

    selected_benchmarks = BENCHMARK_SUITES.get(benchmark_option)
    if selected_benchmarks is None:
        supported = ', '.join(sorted(BENCHMARK_SUITES))
        raise ValueError(f'Unsupported Benchmark option: {benchmark_option} ({supported})')

    benchmark_results_dir = results_absolute_path / 'benchmark' / ros_distro
    benchmark_results_dir.mkdir(parents=True, exist_ok=True)
    _patch_known_runner_typo(benchmark_folder)
    config_dir = benchmark_results_dir / '.ros2_performance_monitoring'
    config_dir.mkdir(parents=True, exist_ok=True)
    container_name = f'ros2-benchmark-container-{ros_distro}-amd64'
    host_owner = f'{os.getuid()}:{os.getgid()}'

    cmd = [
        'docker', 'run', '-d',
        '--network=host',
        '--privileged',
        '--shm-size=1000mb',
        '-v', f'{benchmark_results_dir}:/benchmark_results',
        '-v', f'{benchmark_folder}:/ws/src/ros2_benchmark_container/benchmark',
        '-v', '/var/run/docker.sock:/var/run/docker.sock',
        '-e', 'ROS_DOMAIN_ID=28',
        '-e', 'SYSTEM_EXECUTOR=EventsCBGExecutor',
        '--name', container_name,
        f'ros2-benchmark-container:{ros_distro}-amd64',
        'sleep', 'infinity',
    ]

    subprocess.run(['docker', 'rm', '-f', container_name], check=False)
    subprocess.run(cmd, check=True)

    try:
        for label, runner_script, config_name, config_text in selected_benchmarks:
            config_path = config_dir / config_name
            config_path.write_text(config_text)
            exec_cmd = [
                'docker', 'exec',
                '-e',
                'ROS2_BENCHMARK_SCRIPTS_DIR=/ws/src/ros2_benchmark_container/benchmark/scripts',
                '-e', 'ROS2_BENCHMARK_OUTPUT_DIR=/benchmark_results',
                '-e', f'ROS2_BENCHMARK_TEST_DURATION={duration}',
                container_name,
                'bash', '-lc',
                'source /ws/install/setup.bash && /ws/src/ros2_benchmark_container/'
                f'benchmark/scripts/runners/{runner_script} '
                f'/benchmark_results/.ros2_performance_monitoring/{config_name}',
            ]
            print(f'Starting {label} inside container...')
            subprocess.run(exec_cmd, check=True)
        print('Benchmark Completed Successfully :)')
    finally:
        subprocess.run(
            ['docker', 'exec', container_name, 'chown', '-R', host_owner, '/benchmark_results'],
            check=False,
        )
        subprocess.run(['docker', 'rm', '-f', container_name], check=False)


def _patch_known_runner_typo(benchmark_folder):
    runner = benchmark_folder / 'scripts' / 'runners' / 'run_multi_process_benchmark.sh'
    if not runner.is_file():
        return
    text = runner.read_text()
    if BROKEN_MULTI_PROCESS_COMMAND not in text:
        return
    runner.write_text(text.replace(BROKEN_MULTI_PROCESS_COMMAND, FIXED_MULTI_PROCESS_COMMAND))
