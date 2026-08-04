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
from ros2_performance_monitoring.benchmark_runner import benchmark_runner
from ros2_performance_monitoring.benchmark_runner import BROKEN_MULTI_PROCESS_COMMAND
from ros2_performance_monitoring.benchmark_runner import FIXED_MULTI_PROCESS_COMMAND


def test_runner_executes_service_benchmarks_with_reduced_configs(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    runner = (
        tmp_path
        / 'cache'
        / 'benchmark'
        / 'scripts'
        / 'runners'
        / 'run_multi_process_benchmark.sh'
    )
    runner.parent.mkdir(parents=True)
    runner.write_text(f'before\n{BROKEN_MULTI_PROCESS_COMMAND}\nafter\n')

    benchmark_runner(
        cache_dir=str(tmp_path / 'cache'),
        results_dir=str(tmp_path / 'results'),
        benchmark_option='service-rclcpp-minimal',
        duration=5,
        ros_distro='lyrical',
        executor='EventsCBGExecutor',
    )

    run_command = next(cmd for cmd, _ in calls if cmd[:3] == ['docker', 'run', '-d'])
    assert 'SYSTEM_EXECUTOR=EventsCBGExecutor' in run_command

    config_dir = tmp_path / 'results' / 'benchmark' / 'lyrical' / '.ros2_performance_monitoring'
    single_config = config_dir / 'service_single_process_reduced.conf'
    multi_config = config_dir / 'service_multi_process_reduced.conf'

    assert single_config.is_file()
    assert multi_config.is_file()
    assert FIXED_MULTI_PROCESS_COMMAND in runner.read_text()
    assert BROKEN_MULTI_PROCESS_COMMAND not in runner.read_text()
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
        'docker', 'exec', 'ros2-benchmark-container-lyrical-amd64',
        'chown', '-R', f'{os.getuid()}:{os.getgid()}',
        '/benchmark_results',
    ]


def test_runner_default_suite_executes_all_reduced_topologies(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    benchmark_runner(
        cache_dir=str(tmp_path / 'cache'),
        results_dir=str(tmp_path / 'results'),
        benchmark_option='rclcpp-minimal',
        duration=5,
        ros_distro='lyrical',
        executor='EventsCBGExecutor',
    )

    exec_commands = [cmd for cmd, _ in calls if cmd[:2] == ['docker', 'exec']]
    script_commands = [cmd[-1] for cmd in exec_commands[:-1]]

    assert len(script_commands) == 4
    assert any('pubsub_single_process_reduced.conf' in command for command in script_commands)
    assert any('pubsub_multi_process_reduced.conf' in command for command in script_commands)
    assert any('service_single_process_reduced.conf' in command for command in script_commands)
    assert any('service_multi_process_reduced.conf' in command for command in script_commands)


def test_runner_rejects_unknown_suite(tmp_path):
    with pytest.raises(ValueError) as exc_info:
        benchmark_runner(
            cache_dir=str(tmp_path / 'cache'),
            results_dir=str(tmp_path / 'results'),
            benchmark_option='unknown-suite',
            duration=5,
            ros_distro='lyrical',
            executor='EventsCBGExecutor',
        )

    assert 'Unsupported Benchmark option: unknown-suite' in str(exc_info.value)
    assert 'service-rclcpp-minimal' in str(exc_info.value)


def test_runner_pins_container_to_requested_cpus(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    benchmark_runner(
        cache_dir=str(tmp_path / 'cache'),
        results_dir=str(tmp_path / 'results'),
        benchmark_option='pubsub-rclcpp-minimal',
        duration=60,
        ros_distro='lyrical',
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
    results_dir = tmp_path / 'results' / 'run-2'
    results_root = results_dir.parent.resolve()
    benchmark_root = (tmp_path / 'cache' / 'benchmark').resolve()

    def fake_run(cmd, check, **kwargs):
        calls.append((cmd, check, kwargs))
        if cmd[:3] == ['docker', 'container', 'inspect']:
            if '--format' not in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            label = cmd[cmd.index('--format') + 1]
            if 'results-root' in label:
                value = results_root
            elif 'benchmark-root' in label:
                value = benchmark_root
            else:
                value = ''
            return subprocess.CompletedProcess(cmd, 0, stdout=f'{value}\n')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    benchmark_runner(
        cache_dir=str(tmp_path / 'cache'),
        results_dir=str(results_dir),
        benchmark_option='service-rclcpp-minimal',
        duration=5,
        ros_distro='lyrical',
        executor='EventsCBGExecutor',
        keep_container=True,
    )

    commands = [cmd for cmd, _, _ in calls]
    assert ['docker', 'start', 'ros2-benchmark-container-lyrical-amd64'] in commands
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
    def fake_run(cmd, check, **kwargs):
        if cmd[:3] == ['docker', 'container', 'inspect'] and '--format' not in cmd:
            return subprocess.CompletedProcess(cmd, 0)
        if '--format' in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='/different/root\n')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(RuntimeError, match='Cannot reuse'):
        benchmark_runner(
            cache_dir=str(tmp_path / 'cache'),
            results_dir=str(tmp_path / 'results' / 'run-2'),
            benchmark_option='service-rclcpp-minimal',
            duration=5,
            ros_distro='lyrical',
            executor='EventsCBGExecutor',
            keep_container=True,
        )
