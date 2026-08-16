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
import subprocess

import pytest
from ros2_performance_monitoring.benchmark_image import BenchmarkImageSpec
from ros2_performance_monitoring.benchmark_runner import _benchmark_config
from ros2_performance_monitoring.benchmark_runner import benchmark_runner
from ros2_performance_monitoring.client_target import ClientLibraryTarget


@pytest.fixture(autouse=True)
def stub_container_verification(monkeypatch):
    monkeypatch.setattr(
        'ros2_performance_monitoring.benchmark_runner.verify_benchmark_container',
        lambda image_spec: None,
    )


def test_runner_executes_service_benchmarks_with_reduced_configs(tmp_path, monkeypatch):
    calls = []
    image_spec = _image_spec()

    def fake_run(cmd, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    benchmark_runner(
        results_dir=str(tmp_path / 'results'),
        benchmark_option='service-rclcpp-minimal',
        duration=5,
        image_spec=image_spec,
        executor='EventsCBGExecutor',
    )

    run_command = next(cmd for cmd, _ in calls if cmd[:3] == ['docker', 'run', '-d'])
    assert 'SYSTEM_EXECUTOR=EventsCBGExecutor' in run_command

    config_dir = tmp_path / 'results' / 'benchmark' / 'lyrical' / '.ros2_performance_monitoring'
    single_config = config_dir / 'service_single_process_reduced.conf'
    multi_config = config_dir / 'service_multi_process_reduced.conf'

    assert single_config.is_file()
    assert multi_config.is_file()
    assert 'cli_srv_10b' in single_config.read_text()
    assert 'cli_srv_100kb' in single_config.read_text()
    assert 'cli_srv_1mb' in single_config.read_text()
    assert 'cli_srv_4mb' in single_config.read_text()
    assert 'RESULTS=("10b" "100kb" "1mb" "4mb")' in multi_config.read_text()
    assert 'cli_1mb' in multi_config.read_text()
    assert 'srv_4mb' in multi_config.read_text()

    exec_commands = [cmd for cmd, _ in calls if cmd[:2] == ['docker', 'exec']]
    assert len(exec_commands) == 3
    assert 'run_single_process_benchmark.sh' in exec_commands[0][-1]
    assert 'service_single_process_reduced.conf' in exec_commands[0][-1]
    assert 'run_multi_process_benchmark.sh' in exec_commands[1][-1]
    assert 'service_multi_process_reduced.conf' in exec_commands[1][-1]
    assert exec_commands[2] == [
        'docker', 'exec', image_spec.container_name,
        'chown', '-R', f'{os.getuid()}:{os.getgid()}',
        '/benchmark_results',
    ]


def test_runner_default_suite_executes_all_reduced_topologies(tmp_path, monkeypatch):
    calls = []
    image_spec = _image_spec()

    def fake_run(cmd, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    benchmark_runner(
        results_dir=str(tmp_path / 'results'),
        benchmark_option='rclcpp-minimal',
        duration=5,
        image_spec=image_spec,
        executor='EventsCBGExecutor',
    )

    exec_commands = [cmd for cmd, _ in calls if cmd[:2] == ['docker', 'exec']]
    script_commands = [cmd[-1] for cmd in exec_commands[:-1]]

    assert len(script_commands) == 4
    assert any('pubsub_single_process_reduced.conf' in command for command in script_commands)
    assert any('pubsub_multi_process_reduced.conf' in command for command in script_commands)
    assert any('service_single_process_reduced.conf' in command for command in script_commands)
    assert any('service_multi_process_reduced.conf' in command for command in script_commands)


@pytest.mark.parametrize(
    ('family_name', 'expected_mode_lines'),
    (
        (
            'pub-sub_single_process',
            (
                'COMMS_fastrtps=("ipc_on" "ipc_off" "loaned")',
                'COMMS_cyclonedds=("ipc_off")',
                'COMMS_zenoh=("ipc_on" "ipc_off")',
            ),
        ),
        (
            'pub-sub_multi_process',
            (
                'COMMS_fastrtps=("ipc_off" "loaned")',
                'COMMS_cyclonedds=("ipc_off")',
                'COMMS_zenoh=("ipc_off")',
            ),
        ),
        (
            'cli-srv_single_process',
            (
                'COMMS_fastrtps=("ipc_on" "ipc_off")',
                'COMMS_cyclonedds=("ipc_off")',
                'COMMS_zenoh=("ipc_on" "ipc_off")',
            ),
        ),
        (
            'cli-srv_multi_process',
            (
                'COMMS_fastrtps=("ipc_off")',
                'COMMS_cyclonedds=("ipc_off")',
                'COMMS_zenoh=("ipc_off")',
            ),
        ),
    ),
)
def test_reduced_configs_use_family_specific_communication_modes(
    family_name,
    expected_mode_lines,
):
    config = _benchmark_config(family_name)

    assert 'RMW_LIST=("fastrtps" "cyclonedds" "zenoh")' in config
    for line in expected_mode_lines:
        assert line in config


def test_runner_rejects_unknown_suite(tmp_path):
    image_spec = _image_spec()
    with pytest.raises(ValueError) as exc_info:
        benchmark_runner(
            results_dir=str(tmp_path / 'results'),
            benchmark_option='unknown-suite',
            duration=5,
            image_spec=image_spec,
            executor='EventsCBGExecutor',
        )

    assert 'Unsupported Benchmark option: unknown-suite' in str(exc_info.value)
    assert 'service-rclcpp-minimal' in str(exc_info.value)


def test_runner_pins_container_to_requested_cpus(tmp_path, monkeypatch):
    calls = []
    image_spec = _image_spec()

    def fake_run(cmd, check):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    benchmark_runner(
        results_dir=str(tmp_path / 'results'),
        benchmark_option='pubsub-rclcpp-minimal',
        duration=60,
        image_spec=image_spec,
        executor='EventsCBGExecutor',
        cpuset_cpus='0,2,4,6,8,10',
    )

    run_command = next(cmd for cmd in calls if cmd[:3] == ['docker', 'run', '-d'])
    assert run_command[3:5] == ['--cpuset-cpus', '0,2,4,6,8,10']
    assert (
        'ros2-performance-monitoring.cpuset-cpus=0,2,4,6,8,10'
        in run_command
    )


def test_runner_reuses_compatible_retained_container(tmp_path, monkeypatch):
    calls = []
    image_spec = _image_spec()
    results_dir = tmp_path / 'results' / 'run-2'
    results_root = results_dir.parent.resolve()

    def fake_run(cmd, check, **kwargs):
        calls.append((cmd, check, kwargs))
        if cmd[:3] == ['docker', 'container', 'inspect']:
            if '--format' not in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            label = cmd[cmd.index('--format') + 1]
            if 'results-root' in label:
                value = results_root
            else:
                value = ''
            return subprocess.CompletedProcess(cmd, 0, stdout=f'{value}\n')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    benchmark_runner(
        results_dir=str(results_dir),
        benchmark_option='service-rclcpp-minimal',
        duration=5,
        image_spec=image_spec,
        executor='EventsCBGExecutor',
        keep_container=True,
    )

    commands = [cmd for cmd, _, _ in calls]
    assert ['docker', 'start', image_spec.container_name] in commands
    assert not any(cmd[:3] == ['docker', 'run', '-d'] for cmd in commands)
    assert not any(cmd[:3] == ['docker', 'rm', '-f'] for cmd in commands)
    script_commands = [cmd for cmd in commands if cmd[:2] == ['docker', 'exec']][:-1]
    assert all(
        'ROS2_BENCHMARK_OUTPUT_DIR=/benchmark_results/run-2/benchmark/lyrical'
        in cmd
        for cmd in script_commands
    )


def test_runner_rejects_retained_container_with_different_results_root(
    tmp_path,
    monkeypatch,
):
    image_spec = _image_spec()

    def fake_run(cmd, check, **kwargs):
        if cmd[:3] == ['docker', 'container', 'inspect'] and '--format' not in cmd:
            return subprocess.CompletedProcess(cmd, 0)
        if '--format' in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='/different/root\n')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(RuntimeError, match='Cannot reuse'):
        benchmark_runner(
            results_dir=str(tmp_path / 'results' / 'run-2'),
            benchmark_option='service-rclcpp-minimal',
            duration=5,
            image_spec=image_spec,
            executor='EventsCBGExecutor',
            keep_container=True,
        )


def _image_spec():
    return BenchmarkImageSpec(
        ros_distro='lyrical',
        architecture='amd64',
        benchmark_repository_url='https://github.com/ros2/ros2-benchmark-container',
        benchmark_requested_ref='rolling',
        benchmark_resolved_commit='a' * 40,
        client_target=ClientLibraryTarget.packaged('lyrical'),
    )
