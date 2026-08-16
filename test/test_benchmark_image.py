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

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
from ros2_performance_monitoring.benchmark_image import BenchmarkImageSpec
from ros2_performance_monitoring.benchmark_image import BROKEN_MULTI_PROCESS_COMMAND
from ros2_performance_monitoring.benchmark_image import build_benchmark_image
from ros2_performance_monitoring.benchmark_image import BuildConfiguration
from ros2_performance_monitoring.benchmark_image import FIXED_MULTI_PROCESS_COMMAND
from ros2_performance_monitoring.benchmark_image import MANIFEST_PATH
from ros2_performance_monitoring.benchmark_image import validate_benchmark_container
from ros2_performance_monitoring.benchmark_image import verify_benchmark_image
from ros2_performance_monitoring.client_target import ClientLibraryTarget


@pytest.mark.parametrize(
    'changed_spec',
    (
        lambda spec: replace(spec, ros_distro='rolling'),
        lambda spec: replace(spec, architecture='arm64'),
        lambda spec: replace(spec, benchmark_resolved_commit='b' * 40),
        lambda spec: replace(
            spec,
            client_target=replace(spec.client_target, resolved_commit='c' * 40),
        ),
        lambda spec: replace(
            spec,
            build_configuration=BuildConfiguration(cmake_build_type='RelWithDebInfo'),
        ),
    ),
)
def test_every_build_input_changes_target_identity(changed_spec):
    spec = _source_spec()

    assert changed_spec(spec).target_key != spec.target_key
    assert changed_spec(spec).image_name != spec.image_name
    assert changed_spec(spec).container_name != spec.container_name


def test_matching_image_is_verified_and_returns_provenance(monkeypatch):
    spec = _source_spec()
    calls = _mock_image_verification(monkeypatch, spec)

    verified = verify_benchmark_image(spec)

    assert verified.image_name == spec.image_name
    assert verified.image_id == f'sha256:{"d" * 64}'
    assert verified.image_digest == f'sha256:{"e" * 64}'
    assert calls[1] == [
        'docker', 'run', '--rm', '--entrypoint', 'cat', spec.image_name, MANIFEST_PATH,
    ]
    assert calls[2][0:6] == [
        'docker', 'run', '--rm', '--entrypoint', 'bash', spec.image_name,
    ]


def test_image_label_mismatch_is_rejected_before_runtime(monkeypatch):
    spec = _source_spec()
    image_data = _image_data(spec)
    image_data['Config']['Labels'][
        'ros2-performance-monitoring.client-commit'
    ] = 'f' * 40

    monkeypatch.setattr(
        subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps([image_data])
        ),
    )

    with pytest.raises(RuntimeError, match='Cannot reuse image'):
        verify_benchmark_image(spec)


def test_retained_container_rejects_target_label_mismatch(monkeypatch):
    spec = _source_spec()
    labels = spec.labels()
    labels['ros2-performance-monitoring.target-key'] = 'wrong-target'
    container_data = {
        'Image': f'sha256:{"d" * 64}',
        'Config': {'Labels': labels},
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([container_data]),
        )

    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(RuntimeError, match='Cannot reuse container'):
        validate_benchmark_container(spec)

    assert calls == [['docker', 'container', 'inspect', spec.container_name]]


@pytest.mark.parametrize(
    'runtime_output',
    (
        (
            '/opt/ros/lyrical\n__RCLCPP_LINKS__\n'
            'librclcpp.so => /opt/ros/lyrical/lib/librclcpp.so (0x1)\n'
        ),
        (
            '/target_ws/install\n__RCLCPP_LINKS__\n'
            'librclcpp.so => /opt/ros/lyrical/lib/librclcpp.so (0x1)\n'
        ),
    ),
)
def test_source_image_rejects_packaged_prefix_or_dynamic_library(
    tmp_path,
    monkeypatch,
    runtime_output,
):
    spec = _source_spec(tmp_path)
    _mock_image_verification(monkeypatch, spec, runtime_output=runtime_output)

    with pytest.raises(RuntimeError, match='rclcpp'):
        verify_benchmark_image(spec)


def test_source_build_uses_complete_buildx_argument_lists(tmp_path, monkeypatch):
    spec = _source_spec(tmp_path)
    spec.client_target.checkout_path.mkdir()
    (tmp_path / 'cache').mkdir()
    (tmp_path / 'cache' / 'Dockerfile').write_text('FROM scratch\n')
    runner = (
        tmp_path / 'cache' / 'benchmark' / 'scripts' / 'runners'
        / 'run_multi_process_benchmark.sh'
    )
    runner.parent.mkdir(parents=True)
    runner.write_text(f'before\n{BROKEN_MULTI_PROCESS_COMMAND}\nafter\n')
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == 'git':
            if 'rev-parse' in command:
                commit = (
                    spec.benchmark_resolved_commit
                    if command[2] == str((tmp_path / 'cache').resolve())
                    else spec.client_target.resolved_commit
                )
                return subprocess.CompletedProcess(command, 0, stdout=f'{commit}\n')
            return subprocess.CompletedProcess(command, 0, stdout='')
        if command[:3] == ['docker', 'buildx', 'inspect']:
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr('shutil.which', lambda executable: f'/usr/bin/{executable}')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(
        'ros2_performance_monitoring.benchmark_image.verify_benchmark_image',
        lambda received_spec: received_spec,
    )

    assert build_benchmark_image(spec, str(tmp_path / 'cache')) is spec

    commands = [command for command, _ in calls if command[0] == 'docker']
    assert commands[0] == [
        'docker', 'buildx', 'inspect', 'ros2-performance-monitoring-amd64-builder',
    ]
    assert commands[1] == [
        'docker', 'buildx', 'create', '--name',
        'ros2-performance-monitoring-amd64-builder', '--use',
    ]
    base_build = commands[2]
    assert base_build[:12] == [
        'docker', 'buildx', 'build', '--load', '--platform', 'linux/amd64',
        '--target', 'ros2-benchmark-container', '--build-arg', 'ROS_DISTRO=lyrical',
        '--build-arg', 'BASE_IMAGE=osrf/ros:lyrical-desktop',
    ]
    assert base_build[-1] == str((tmp_path / 'cache').resolve())
    source_build = commands[3]
    assert source_build[:7] == [
        'docker', 'buildx', 'build', '--load', '--platform', 'linux/amd64', '--file',
    ]
    assert '--label' in source_build
    assert f'BASE_IMAGE={spec.base_image_name}' in source_build
    assert f'ros2-performance-monitoring.target-key={spec.target_key}' in source_build
    assert f'rclcpp={spec.client_target.checkout_path}' in source_build
    benchmark_context = next(
        item.removeprefix('benchmark=')
        for item in source_build
        if item.startswith('benchmark=')
    )
    patched_runner = Path(benchmark_context) / runner.relative_to(tmp_path / 'cache' / 'benchmark')
    assert FIXED_MULTI_PROCESS_COMMAND in patched_runner.read_text()
    assert BROKEN_MULTI_PROCESS_COMMAND not in patched_runner.read_text()
    assert source_build[-1].endswith('ros2_performance_monitoring')
    assert all('shell' not in kwargs for _, kwargs in calls)


def _source_spec(tmp_path: Path | None = None):
    checkout = (tmp_path or Path('/tmp')) / 'rclcpp-checkout'
    return BenchmarkImageSpec(
        ros_distro='lyrical',
        architecture='amd64',
        benchmark_repository_url='https://github.com/ros2/ros2-benchmark-container',
        benchmark_requested_ref='rolling',
        benchmark_resolved_commit='a' * 40,
        client_target=ClientLibraryTarget(
            name='rclcpp',
            source='build',
            repository_url='https://github.com/ros2/rclcpp.git',
            requested_ref='rolling',
            resolved_commit='b' * 40,
            checkout_path=checkout,
        ),
    )


def _image_data(spec):
    return {
        'Id': f'sha256:{"d" * 64}',
        'RepoDigests': [f'benchmark@sha256:{"e" * 64}'],
        'Config': {'Labels': spec.labels()},
    }


def _mock_image_verification(monkeypatch, spec, runtime_output=None):
    calls = []
    if runtime_output is None:
        runtime_output = (
            f'{spec.expected_rclcpp_prefix}\n__RCLCPP_LINKS__\n'
            f'librclcpp.so => {spec.expected_rclcpp_prefix}/lib/librclcpp.so (0x1)\n'
        )

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            stdout = json.dumps([_image_data(spec)])
        elif command[-1] == MANIFEST_PATH:
            stdout = json.dumps(spec.manifest())
        else:
            stdout = runtime_output
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return calls
