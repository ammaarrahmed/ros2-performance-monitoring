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

from ros2_performance_monitoring import benchmark_layout


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

BENCHMARK_SUITES = {
    'pubsub-rclcpp-minimal': (
        'pub-sub_single_process',
        'pub-sub_multi_process',
    ),
    'service-rclcpp-minimal': (
        'cli-srv_single_process',
        'cli-srv_multi_process',
    ),
}
BENCHMARK_SUITES['rclcpp-minimal'] = (
    BENCHMARK_SUITES['pubsub-rclcpp-minimal'] + BENCHMARK_SUITES['service-rclcpp-minimal']
)


def _benchmark_config(family_name):
    family = benchmark_layout.get_benchmark_family(family_name)
    payloads = tuple(benchmark_layout.PAYLOADS)
    is_pubsub = family.topology == 'pub-sub'
    is_multi_process = family.process_mode == 'multi_process'

    if is_pubsub:
        benchmark_label = 'Pub/Sub'
        topology_dir = 'pub-sub'
    else:
        benchmark_label = 'Client/Service'
        topology_dir = 'cli-srv'
    process_suffix = ' multi-process' if is_multi_process else ''
    benchmark_suffix = ' benchmark' if is_pubsub and not is_multi_process else ''
    lines = [
        f'# Generated reduced {benchmark_label}{process_suffix}{benchmark_suffix} config',
        f'OUTPUT_DIR_NAME="{family.name}"',
    ]

    if not is_multi_process:
        prefix = 'pub_sub_200hz' if is_pubsub else 'cli_srv'
        topologies = tuple(f'{prefix}_{payload}' for payload in payloads)
        if is_pubsub:
            lines.extend(('TOPOLOGIES=(', *(f'  "{name}"' for name in topologies), ')'))
        else:
            lines.append(_bash_array('TOPOLOGIES', topologies))

    lines.extend((
        f'TOPOLOGIES_DIR="${{ROS2_BENCHMARK_SCRIPTS_DIR}}/../topologies/{topology_dir}"',
        'PROFILES_DIR="${ROS2_BENCHMARK_SCRIPTS_DIR}/../profiles"',
    ))

    if is_multi_process:
        first_prefix, second_prefix = ('pub_200hz', 'sub') if is_pubsub else ('cli', 'srv')
        lines.extend((
            _bash_array('RESULTS', payloads),
            _bash_array('TOPOLOGY1', (f'{first_prefix}_{item}' for item in payloads)),
            _bash_array('TOPOLOGY2', (f'{second_prefix}_{item}' for item in payloads)),
        ))

    lines.extend(_rmw_config(family))
    lines.extend((
        f'ZENOH_SESSION_CONFIG_URI=${{PROFILES_DIR}}/{ZENOH_SESSION_PROFILE}',
        '',
    ))
    return '\n'.join(lines)


def _bash_array(name, values):
    quoted_values = ' '.join(f'"{value}"' for value in values)
    return f'{name}=({quoted_values})'


def _rmw_config(family):
    lines = [_bash_array('RMW_LIST', benchmark_layout.RMW_IMPLEMENTATIONS)]
    for short_name in benchmark_layout.RMW_IMPLEMENTATIONS:
        lines.append(_bash_array(f'COMMS_{short_name}', family.communication_modes[short_name]))
        if family.topology != 'pub-sub':
            continue
        if short_name == 'fastrtps':
            lines.extend((
                'LOANED_ENV_VARS_fastrtps=(',
                f'  "export FASTRTPS_DEFAULT_PROFILES_FILE=${{PROFILES_DIR}}/{FASTDDS_PROFILE}"',
                '  "export RMW_FASTRTPS_USE_QOS_FROM_XML=1"',
                ')',
            ))
        elif short_name == 'cyclonedds':
            lines.extend((
                'LOANED_ENV_VARS_cyclonedds=(',
                f'  "export CYCLONEDDS_URI=${{PROFILES_DIR}}/{CYCLONEDDS_PROFILE}"',
                ')',
            ))
    return lines


def _runner_details(family_name):
    family = benchmark_layout.get_benchmark_family(family_name)
    process_label = family.process_mode.replace('_', ' ').title()
    topology_label = 'Pub/Sub' if family.topology == 'pub-sub' else 'Service'
    config_prefix = 'pubsub' if family.topology == 'pub-sub' else 'service'
    return (
        f'{process_label} {topology_label} rclcpp minimal',
        f'run_{family.process_mode}_benchmark.sh',
        f'{config_prefix}_{family.process_mode}_reduced.conf',
        _benchmark_config(family_name),
    )


def benchmark_runner(
    cache_dir: str,
    results_dir: str,
    benchmark_option: str,
    duration: int,
    ros_distro: str,
    executor: str,
    keep_container: bool = False,
    cpuset_cpus: str | None = None,
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

    if keep_container:
        results_mount = results_absolute_path.parent
        container_results_dir = (
            Path('/benchmark_results')
            / results_absolute_path.name
            / 'benchmark'
            / ros_distro
        )
    else:
        results_mount = benchmark_results_dir
        container_results_dir = Path('/benchmark_results')

    cmd = [
        'docker', 'run', '-d',
        '--network=host',
        '--privileged',
        '--shm-size=1000mb',
        '-v', f'{results_mount}:/benchmark_results',
        '-v', f'{benchmark_folder}:/ws/src/ros2_benchmark_container/benchmark',
        '-v', '/var/run/docker.sock:/var/run/docker.sock',
        '-e', 'ROS_DOMAIN_ID=28',
        '-e', f'SYSTEM_EXECUTOR={executor}',
        '--label', f'ros2-performance-monitoring.results-root={results_mount}',
        '--label', f'ros2-performance-monitoring.benchmark-root={benchmark_folder}',
        '--name', container_name,
        f'ros2-benchmark-container:{ros_distro}-amd64',
        'sleep', 'infinity',
    ]
    if cpuset_cpus:
        cmd[3:3] = ['--cpuset-cpus', cpuset_cpus]
        cmd[cmd.index('--name'):cmd.index('--name')] = [
            '--label', f'ros2-performance-monitoring.cpuset-cpus={cpuset_cpus}',
        ]

    reuse_container = keep_container and benchmark_container_exists(ros_distro)
    if reuse_container:
        _validate_retained_container(
            container_name,
            results_mount,
            benchmark_folder,
            cpuset_cpus,
        )
        subprocess.run(['docker', 'start', container_name], check=True)
        print(f'Reusing retained benchmark container: {container_name}')
    else:
        subprocess.run(['docker', 'rm', '-f', container_name], check=False)
        subprocess.run(cmd, check=True)

    try:
        for family_name in selected_benchmarks:
            label, runner_script, config_name, config_text = _runner_details(family_name)
            config_path = config_dir / config_name
            config_path.write_text(config_text)
            exec_cmd = [
                'docker', 'exec',
                '-e',
                'ROS2_BENCHMARK_SCRIPTS_DIR=/ws/src/ros2_benchmark_container/benchmark/scripts',
                '-e', f'ROS2_BENCHMARK_OUTPUT_DIR={container_results_dir}',
                '-e', f'ROS2_BENCHMARK_TEST_DURATION={duration}',
                '-e', f'SYSTEM_EXECUTOR={executor}',
                container_name,
                'bash', '-lc',
                'source /ws/install/setup.bash && /ws/src/ros2_benchmark_container/'
                f'benchmark/scripts/runners/{runner_script} '
                f'{container_results_dir}/.ros2_performance_monitoring/{config_name}',
            ]
            print(f'Starting {label} inside container...')
            subprocess.run(exec_cmd, check=True)
        print('Benchmark Completed Successfully :)')
    finally:
        subprocess.run(
            [
                'docker', 'exec', container_name, 'chown', '-R', host_owner,
                str(container_results_dir),
            ],
            check=False,
        )
        if not keep_container:
            subprocess.run(['docker', 'rm', '-f', container_name], check=False)


def benchmark_container_exists(ros_distro: str) -> bool:
    """Return whether the named benchmark container already exists."""
    container_name = f'ros2-benchmark-container-{ros_distro}-amd64'
    result = subprocess.run(
        ['docker', 'container', 'inspect', container_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def benchmark_image_exists(ros_distro: str) -> bool:
    """Return whether the benchmark image for a ROS distribution exists."""
    image_name = f'ros2-benchmark-container:{ros_distro}-amd64'
    result = subprocess.run(
        ['docker', 'image', 'inspect', image_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _validate_retained_container(
    container_name,
    results_mount,
    benchmark_folder,
    cpuset_cpus,
):
    labels = {
        'results-root': str(results_mount),
        'benchmark-root': str(benchmark_folder),
        'cpuset-cpus': cpuset_cpus or '',
    }
    for label, expected in labels.items():
        result = subprocess.run(
            [
                'docker', 'container', 'inspect',
                '--format',
                f'{{{{ index .Config.Labels "ros2-performance-monitoring.{label}" }}}}',
                container_name,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        actual = result.stdout.strip()
        if actual != expected:
            raise RuntimeError(
                f'Cannot reuse {container_name}: its {label} is {actual!r}, '
                f'expected {expected!r}. Remove the container or use results '
                'directories with the same parent.'
            )


def _patch_known_runner_typo(benchmark_folder):
    runner = benchmark_folder / 'scripts' / 'runners' / 'run_multi_process_benchmark.sh'
    if not runner.is_file():
        return
    text = runner.read_text()
    if BROKEN_MULTI_PROCESS_COMMAND not in text:
        return
    runner.write_text(text.replace(BROKEN_MULTI_PROCESS_COMMAND, FIXED_MULTI_PROCESS_COMMAND))
