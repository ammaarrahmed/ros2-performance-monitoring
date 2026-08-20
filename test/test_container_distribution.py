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

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

import pytest

from ros2_performance_monitoring import exporter
from ros2_performance_monitoring.exporters.prometheus import create_metrics_server
from ros2_performance_monitoring.version import project_version


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_defines_shared_cli_and_exporter_targets():
    dockerfile = (REPOSITORY_ROOT / 'Dockerfile').read_text()

    assert ' AS wheel' in dockerfile
    assert ' AS cli' in dockerfile
    assert ' AS exporter' in dockerfile
    assert 'docker:29.1.3-cli' in dockerfile
    assert 'dockerd' not in dockerfile
    exporter_section = dockerfile.split(' AS exporter', 1)[1]
    assert 'USER exporter' in exporter_section
    assert 'ros2-performance-exporter' in exporter_section
    assert 'docker:' not in exporter_section


def test_compose_separates_cli_socket_from_read_only_exporter():
    compose = (REPOSITORY_ROOT / 'compose.yml').read_text()
    cli_section, dashboard_section = compose.split('\n  exporter:', 1)
    exporter_section = dashboard_section.split('\n  prometheus:', 1)[0]

    assert '/var/run/docker.sock' in cli_section
    assert 'ROS2_PERFORMANCE_HOST_UID' in cli_section
    assert 'ROS2_PERFORMANCE_HOST_RESULTS_ROOT' in cli_section
    assert '/var/run/docker.sock' not in exporter_section
    assert 'read_only: true' in exporter_section
    assert 'no-new-privileges:true' in exporter_section
    assert 'cap_drop:' in exporter_section


def test_compose_configuration_is_valid():
    if shutil.which('docker') is None:
        pytest.skip('Docker CLI is not installed')
    result = subprocess.run(
        [
            'docker', 'compose', '--profile', 'cli', '--profile', 'dashboard',
            '-f', str(REPOSITORY_ROOT / 'compose.yml'), 'config', '--quiet',
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if 'compose is not a docker command' in result.stderr:
        pytest.skip('Docker Compose plugin is not installed')
    assert result.returncode == 0, result.stderr


def test_exporter_entrypoint_reads_environment(monkeypatch):
    received = {}
    monkeypatch.setenv('ROS2_PERFORMANCE_EXPORTER_INPUT', '/data/input.jsonl')
    monkeypatch.setenv('ROS2_PERFORMANCE_EXPORTER_REPORT', '/data/report.json')
    monkeypatch.setenv('ROS2_PERFORMANCE_EXPORTER_PORT', '9200')
    monkeypatch.setattr(
        exporter,
        'serve_metrics',
        lambda *args, **kwargs: received.update(args=args, kwargs=kwargs),
    )

    exporter.main()

    assert received == {
        'args': ('/data/input.jsonl',),
        'kwargs': {
            'port': 9200,
            'comparison_report_path': '/data/report.json',
        },
    }


def test_exporter_entrypoint_prefers_history_environment(monkeypatch):
    received = {}
    monkeypatch.setenv('ROS2_PERFORMANCE_EXPORTER_INPUT', '/data/input.jsonl')
    monkeypatch.setenv(
        'ROS2_PERFORMANCE_EXPORTER_HISTORY_INDEX',
        '/data/active-history.json',
    )
    monkeypatch.setattr(
        exporter,
        'serve_metrics',
        lambda *args, **kwargs: received.update(args=args, kwargs=kwargs),
    )

    exporter.main()

    assert received == {
        'args': (None,),
        'kwargs': {
            'port': 9108,
            'comparison_report_path': None,
            'history_index_path': '/data/active-history.json',
        },
    }


def test_exporter_serves_health_and_metrics(tmp_path):
    dataset = tmp_path / 'dashboard-data.jsonl'
    dataset.write_text(json.dumps(_record()) + '\n')
    server = create_metrics_server(dataset, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    port = server.server_address[1]
    try:
        with urlopen(f'http://127.0.0.1:{port}/healthz') as response:
            assert response.status == 200
            assert response.read() == b'ok\n'
        with urlopen(f'http://127.0.0.1:{port}/metrics') as response:
            assert response.status == 200
            assert b'ros2_perf_latency_us' in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.docker_integration
def test_runtime_images_and_sibling_daemon_integration(tmp_path):
    if os.environ.get('ROS2_PERFORMANCE_RUN_CONTAINER_IMAGE_TESTS') != '1':
        pytest.skip('set ROS2_PERFORMANCE_RUN_CONTAINER_IMAGE_TESTS=1 to build images')
    suffix = uuid.uuid4().hex[:12]
    cli_image = f'ros2-performance-monitoring-cli-test:{suffix}'
    exporter_image = f'ros2-performance-monitoring-exporter-test:{suffix}'
    controller_name = f'ros2-performance-controller-test-{suffix}'
    sibling_name = f'ros2-performance-sibling-test-{suffix}'
    exporter_name = f'ros2-performance-exporter-test-{suffix}'
    cache = tmp_path / 'cache'
    results = tmp_path / 'results'
    cache.mkdir()
    results.mkdir()
    socket_gid = Path('/var/run/docker.sock').stat().st_gid
    try:
        _build_image('cli', cli_image)
        _build_image('exporter', exporter_image)
        _assert_runtime_contents(cli_image, exporter_image)
        _assert_exporter_runtime(exporter_image, exporter_name, tmp_path)

        subprocess.run(
            [
                'docker', 'run', '-d', '--name', sibling_name,
                'alpine:3.22', 'sleep', 'infinity',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                'docker', 'run', '-d', '--name', controller_name,
                '--user', f'{os.getuid()}:{os.getgid()}',
                '--group-add', str(socket_gid),
                '--volume', '/var/run/docker.sock:/var/run/docker.sock',
                '--volume', f'{results}:/results',
                '--volume', f'{cache}:/cache',
                '--entrypoint', 'sh', cli_image, '-c', 'sleep infinity',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        host_daemon = _docker_output(['docker', 'info', '--format', '{{.ID}}'])
        controller_daemon = _docker_output([
            'docker', 'exec', controller_name,
            'docker', 'info', '--format', '{{.ID}}',
        ])
        controller_view = _docker_output([
            'docker', 'exec', controller_name,
            'docker', 'container', 'inspect', '--format', '{{.Name}}', sibling_name,
        ])
        assert controller_daemon == host_daemon
        assert controller_view == f'/{sibling_name}'
        assert _docker_output([
            'docker', 'container', 'inspect', '--format', '{{.Name}}', controller_name,
        ]) == f'/{controller_name}'
    finally:
        subprocess.run(
            ['docker', 'rm', '-f', controller_name, sibling_name, exporter_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['docker', 'image', 'rm', '-f', cli_image, exporter_image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@pytest.mark.docker_integration
def test_container_controller_runs_short_upstream_benchmark(tmp_path):
    if os.environ.get('ROS2_PERFORMANCE_RUN_CONTAINER_BENCHMARK_TEST') != '1':
        pytest.skip(
            'set ROS2_PERFORMANCE_RUN_CONTAINER_BENCHMARK_TEST=1 to run a benchmark'
        )
    suffix = uuid.uuid4().hex[:12]
    cli_image = f'ros2-performance-monitoring-cli-test:{suffix}'
    controller_name = f'ros2-performance-controller-benchmark-test-{suffix}'
    results = tmp_path / 'results'
    cache = Path(
        os.environ.get(
            'ROS2_PERFORMANCE_CONTAINER_BENCHMARK_CACHE',
            tmp_path / 'cache',
        )
    ).resolve()
    results.mkdir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / 'home').mkdir(exist_ok=True)
    socket_gid = Path('/var/run/docker.sock').stat().st_gid
    images_before = _docker_image_references()
    try:
        _build_image('cli', cli_image)
        command = [
            'docker', 'run', '--rm', '--name', controller_name,
            '--user', f'{os.getuid()}:{os.getgid()}',
            '--group-add', str(socket_gid),
            '--read-only', '--tmpfs', '/tmp',
            '--volume', '/var/run/docker.sock:/var/run/docker.sock',
            '--volume', f'{results}:/results',
            '--volume', f'{cache}:/cache',
            '--env', 'HOME=/cache/home',
            '--env', 'ROS2_PERFORMANCE_CONTROLLER_MODE=container',
            '--env', 'ROS2_PERFORMANCE_CONTROLLER_RESULTS_ROOT=/results',
            '--env', f'ROS2_PERFORMANCE_HOST_RESULTS_ROOT={results}',
            '--env', 'ROS2_PERFORMANCE_CONTROLLER_CACHE_ROOT=/cache',
            '--env', f'ROS2_PERFORMANCE_HOST_CACHE_ROOT={cache}',
            '--env', f'ROS2_PERFORMANCE_HOST_UID={os.getuid()}',
            '--env', f'ROS2_PERFORMANCE_HOST_GID={os.getgid()}',
            '--env', f'ROS2_PERFORMANCE_CONTROLLER_IMAGE={cli_image}',
            cli_image,
            'run',
            '--client-library-source', 'build',
            '--client-library-repo-url', 'https://github.com/ros2/rclcpp.git',
            '--client-library-ref', os.environ.get(
                'ROS2_PERFORMANCE_CONTAINER_RCLCPP_REF', 'rolling'
            ),
            '--container-ref', os.environ.get(
                'ROS2_PERFORMANCE_CONTAINER_BENCHMARK_REF', 'rolling'
            ),
            '--ros-distro', os.environ.get(
                'ROS2_PERFORMANCE_CONTAINER_ROS_DISTRO', 'rolling'
            ),
            '--suite', 'service-rclcpp-minimal',
            '--duration', '1',
            '--cache-dir', '/cache/benchmark',
            '/results/smoke',
        ]
        cpuset = os.environ.get('ROS2_PERFORMANCE_CONTAINER_CPUSET')
        if cpuset:
            command[-1:-1] = ['--cpuset-cpus', cpuset]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

        normalized = results / 'smoke' / 'normalized_metrics.jsonl'
        metadata_path = next((results / 'smoke').glob('metadata_*.json'))
        metadata = json.loads(metadata_path.read_text())
        assert normalized.stat().st_size > 0
        assert metadata['controller']['execution_mode'] == 'container'
        assert metadata['controller']['image']['reference'] == cli_image
        assert metadata['controller']['docker_server']['id']
        assert metadata['benchmark_image']['id']
        assert len(metadata['benchmark_repo']['resolved_commit_hash']) == 40
        assert len(
            metadata['client_library_under_test']['resolved_commit_hash']
        ) == 40
        assert normalized.stat().st_uid == os.getuid()
        assert normalized.stat().st_gid == os.getgid()
    finally:
        subprocess.run(
            ['docker', 'rm', '-f', controller_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['docker', 'image', 'rm', '-f', cli_image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for image_reference in _docker_image_references() - images_before:
            subprocess.run(
                ['docker', 'image', 'rm', image_reference],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _build_image(target, image):
    subprocess.run(
        [
            'docker', 'build', '--target', target, '--tag', image,
            '--build-arg', f'PROJECT_VERSION={project_version()}',
            '--build-arg', 'VCS_REF=test-revision', str(REPOSITORY_ROOT),
        ],
        check=True,
    )


def _assert_runtime_contents(cli_image, exporter_image):
    cli_check = subprocess.run(
        [
            'docker', 'run', '--rm', '--entrypoint', 'sh', cli_image, '-c',
            'test "$(id -u)" != 0 && command -v docker && '
            'docker buildx version && docker compose version && '
            'command -v vcs && ! command -v dockerd && '
            'ros2-performance-monitoring help >/dev/null',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert cli_check.returncode == 0, cli_check.stderr
    exporter_check = subprocess.run(
        [
            'docker', 'run', '--rm', '--entrypoint', 'sh', exporter_image, '-c',
            'test "$(id -u)" != 0 && ! command -v docker && '
            'test ! -e /var/run/docker.sock && command -v ros2-performance-exporter',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert exporter_check.returncode == 0, exporter_check.stderr


def _assert_exporter_runtime(image, container_name, root):
    dataset = root / 'dashboard-data.jsonl'
    dataset.write_text(json.dumps(_record()) + '\n')
    subprocess.run(
        [
            'docker', 'run', '-d', '--name', container_name,
            '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true',
            '--volume', f'{dataset}:/data/dashboard-data.jsonl:ro',
            '--publish', '127.0.0.1::9108', image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    port = _docker_output([
        'docker', 'port', container_name, '9108/tcp',
    ]).rsplit(':', 1)[1]
    health_url = f'http://127.0.0.1:{port}/healthz'
    deadline = time.monotonic() + 10
    while True:
        try:
            health_response = urlopen(health_url)
            break
        except (OSError, URLError):
            if time.monotonic() >= deadline:
                logs = subprocess.run(
                    ['docker', 'logs', container_name],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                pytest.fail(
                    'exporter did not become healthy:\n'
                    f'{logs.stdout}{logs.stderr}'
                )
            time.sleep(0.1)
    with health_response as response:
        assert response.read() == b'ok\n'
    with urlopen(f'http://127.0.0.1:{port}/metrics') as response:
        assert b'ros2_perf_latency_us' in response.read()


def _docker_output(command):
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _docker_image_references():
    result = subprocess.run(
        [
            'docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}',
            'ros2-performance-monitoring/benchmark',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.splitlines())


def _record():
    return {
        'schema_version': 5,
        'run_id': 'container-smoke',
        'timestamp': '2026-08-20T00:00:00Z',
        'benchmark_ref': 'rolling',
        'benchmark_commit': 'a' * 40,
        'client_library': 'rclcpp',
        'client_library_ref': 'rolling',
        'client_library_commit': 'b' * 40,
        'client_library_source': 'build',
        'platform': 'x86_64',
        'ros_distro': 'rolling',
        'rmw_implementation': 'rmw_fastrtps_cpp',
        'executor': 'EventsExecutor',
        'communication_mode': 'ipc_off',
        'topology': 'service',
        'payload_size': 10,
        'process_mode': 'single_process',
        'node_role': 'client',
        'frequency': 0.0,
        'metric_name': 'service_client_latency',
        'numeric_value': 10.0,
        'unit': 'us',
        'aggregation': 'mean',
        'source_file': 'latency_all.txt',
    }
