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

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import socket
import subprocess

from .benchmark_image import detect_architecture
from .controller import controller_context
from .controller import docker_server_identity
from .experiment import validate_cpuset_cpus


DEFAULT_MINIMUM_FREE_BYTES = 10 * 1024 ** 3
DEFAULT_DASHBOARD_PORTS = (3000, 9090, 9108)


class PreflightError(RuntimeError):
    """Report an unmet requirement before target preparation starts."""


@dataclass(frozen=True)
class PreflightResult:
    """Record the host properties used while planning a comparison."""

    architecture: str
    docker_root: str
    docker_server: dict
    result_filesystem_free_bytes: int
    docker_filesystem_free_bytes: int


def run_comparison_preflight(
    results_dir,
    cpuset_cpus=None,
    *,
    dashboard_requested=False,
    dashboard_port=9108,
    minimum_free_bytes=DEFAULT_MINIMUM_FREE_BYTES,
):
    """Check local comparison requirements before any repository checkout."""
    for executable in ('git', 'vcs', 'docker'):
        _require_executable(executable)
    if dashboard_requested:
        _check_command(
            ['docker', 'compose', 'version'],
            'Docker Compose is not available',
        )

    try:
        docker_server = docker_server_identity()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise PreflightError(f'Docker daemon is not accessible: {exc}') from exc
    docker_root = docker_server['docker_root_dir']

    _check_command(
        ['docker', 'buildx', 'version'],
        'Docker Buildx is not available',
    )
    architecture = detect_architecture()
    builder = _check_command(
        ['docker', 'buildx', 'inspect'],
        'Docker Buildx builder is not usable',
    )
    platform_name = f'linux/{architecture}'
    if platform_name not in f'{builder.stdout}\n{builder.stderr}':
        raise PreflightError(
            f'Docker Buildx does not support the required platform {platform_name}'
        )

    try:
        validate_cpuset_cpus(cpuset_cpus)
    except RuntimeError as exc:
        raise PreflightError(str(exc)) from exc

    context = controller_context()
    results_path = context.resolve_results(results_dir)
    _check_results_access(results_path)
    results_filesystem = _existing_ancestor(results_path)
    result_free = shutil.disk_usage(results_filesystem).free
    if context.mode == 'container':
        docker_free = _daemon_filesystem_free_bytes(docker_root)
        docker_filesystem = docker_root
        same_filesystem = False
    else:
        docker_filesystem = _existing_ancestor(Path(docker_root).expanduser().resolve())
        docker_free = shutil.disk_usage(docker_filesystem).free
        same_filesystem = docker_filesystem.stat().st_dev == results_filesystem.stat().st_dev
    _check_free_space('result', results_filesystem, result_free, minimum_free_bytes)
    if not same_filesystem:
        _check_free_space('Docker', docker_filesystem, docker_free, minimum_free_bytes)

    if dashboard_requested:
        ports = tuple(dict.fromkeys((*DEFAULT_DASHBOARD_PORTS[:2], dashboard_port)))
        for port in ports:
            _check_port(port)

    return PreflightResult(
        architecture=architecture,
        docker_root=docker_root,
        docker_server=docker_server,
        result_filesystem_free_bytes=result_free,
        docker_filesystem_free_bytes=docker_free,
    )


def _daemon_filesystem_free_bytes(docker_root):
    probe_path = f'/host{docker_root}'
    result = _check_command(
        [
            'docker', 'run', '--rm', '--network=none', '--read-only',
            '--pull=missing', '--volume', '/:/host:ro',
            'busybox:1.36.1', 'df', '-Pk', probe_path,
        ],
        'Cannot inspect free space on the Docker host filesystem',
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise PreflightError('Docker host filesystem probe returned no disk usage')
    fields = lines[-1].split()
    try:
        available_kib = int(fields[-3])
    except (IndexError, ValueError) as exc:
        raise PreflightError('Docker host filesystem probe returned invalid disk usage') from exc
    return available_kib * 1024


def _require_executable(executable):
    if shutil.which(executable) is None:
        raise PreflightError(f'Required executable {executable!r} was not found on PATH')


def _check_command(command, message):
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise PreflightError(f'{message}: {exc}') from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f': {detail}' if detail else ''
        raise PreflightError(f'{message}{suffix}')
    return result


def _check_results_access(path):
    if path.exists() and not path.is_dir():
        raise PreflightError(f'Result path is not a directory: {path}')
    ancestor = _existing_ancestor(path)
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise PreflightError(f'Result directory is not writable: {ancestor}')


def _existing_ancestor(path):
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            raise PreflightError(f'No accessible parent exists for {path}')
        candidate = candidate.parent
    return candidate


def _check_free_space(label, path, available, required):
    if available < required:
        available_gib = available / 1024 ** 3
        required_gib = required / 1024 ** 3
        raise PreflightError(
            f'Insufficient free space on the {label} filesystem at {path}: '
            f'{available_gib:.1f} GiB available, {required_gib:.1f} GiB required'
        )


def _check_port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise PreflightError(f'Invalid dashboard port: {port!r}')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        try:
            server.bind(('127.0.0.1', port))
        except OSError as exc:
            raise PreflightError(f'Required dashboard port {port} is unavailable') from exc
