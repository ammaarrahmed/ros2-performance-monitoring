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
import json
import os
from pathlib import Path
import subprocess

from .version import project_version


CONTROLLER_MODE_ENV = 'ROS2_PERFORMANCE_CONTROLLER_MODE'
HOST_RESULTS_ROOT_ENV = 'ROS2_PERFORMANCE_HOST_RESULTS_ROOT'
CONTROLLER_RESULTS_ROOT_ENV = 'ROS2_PERFORMANCE_CONTROLLER_RESULTS_ROOT'
HOST_CACHE_ROOT_ENV = 'ROS2_PERFORMANCE_HOST_CACHE_ROOT'
CONTROLLER_CACHE_ROOT_ENV = 'ROS2_PERFORMANCE_CONTROLLER_CACHE_ROOT'
HOST_UID_ENV = 'ROS2_PERFORMANCE_HOST_UID'
HOST_GID_ENV = 'ROS2_PERFORMANCE_HOST_GID'
CONTROLLER_IMAGE_ENV = 'ROS2_PERFORMANCE_CONTROLLER_IMAGE'


class ControllerConfigurationError(RuntimeError):
    """Report an invalid or incomplete controller-container configuration."""


@dataclass(frozen=True)
class PathMapping:
    """Map one mounted controller directory to its host-daemon source."""

    controller_root: Path
    host_root: Path

    def controller_path(self, value) -> Path:
        """Resolve a user path inside this mapping's controller root."""
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.controller_root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.controller_root)
        except ValueError as exc:
            raise ControllerConfigurationError(
                f'Path {candidate} is outside the mounted controller root '
                f'{self.controller_root}'
            ) from exc
        return candidate

    def host_path(self, value) -> Path:
        """Translate a controller path to the path understood by the daemon."""
        controller_path = self.controller_path(value)
        return self.host_root / controller_path.relative_to(self.controller_root)


@dataclass(frozen=True)
class ControllerContext:
    """Describe host or container execution and its mounted path boundary."""

    mode: str
    host_uid: int
    host_gid: int
    results: PathMapping | None = None
    cache: PathMapping | None = None

    def resolve_results(self, value) -> Path:
        """Resolve a results path in the controller filesystem."""
        if self.results is None:
            return Path(value).expanduser().resolve()
        return self.results.controller_path(value)

    def resolve_cache(self, value) -> Path:
        """Resolve a cache path in the controller filesystem."""
        if self.cache is None:
            return Path(value).expanduser().resolve()
        return self.cache.controller_path(value)

    def daemon_results_path(self, value) -> Path:
        """Return the results path as seen by the host Docker daemon."""
        if self.results is None:
            return Path(value).expanduser().resolve()
        return self.results.host_path(value)


def controller_context(environ=None) -> ControllerContext:
    """Load and validate the current controller execution configuration."""
    values = os.environ if environ is None else environ
    mode = values.get(CONTROLLER_MODE_ENV, 'host')
    if mode not in ('host', 'container'):
        raise ControllerConfigurationError(
            f'{CONTROLLER_MODE_ENV} must be "host" or "container", got {mode!r}'
        )
    if mode == 'host':
        return ControllerContext(
            mode='host',
            host_uid=os.getuid(),
            host_gid=os.getgid(),
        )

    results = _mapping(values, CONTROLLER_RESULTS_ROOT_ENV, HOST_RESULTS_ROOT_ENV)
    cache = _mapping(values, CONTROLLER_CACHE_ROOT_ENV, HOST_CACHE_ROOT_ENV)
    return ControllerContext(
        mode='container',
        host_uid=_non_negative_id(values, HOST_UID_ENV),
        host_gid=_non_negative_id(values, HOST_GID_ENV),
        results=results,
        cache=cache,
    )


def resolve_results_path(value) -> Path:
    """Resolve a results path for the active controller mode."""
    return controller_context().resolve_results(value)


def resolve_cache_path(value) -> Path:
    """Resolve a cache path for the active controller mode."""
    return controller_context().resolve_cache(value)


def docker_server_identity() -> dict:
    """Return stable identity read from the connected Docker server."""
    result = subprocess.run(
        ['docker', 'info', '--format', '{{json .}}'],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError('Docker server returned invalid identity data') from exc
    fields = {
        'id': info.get('ID'),
        'name': info.get('Name'),
        'version': info.get('ServerVersion'),
        'operating_system': info.get('OperatingSystem'),
        'architecture': info.get('Architecture'),
        'docker_root_dir': info.get('DockerRootDir'),
    }
    missing = sorted(name for name, value in fields.items() if not value)
    if missing:
        raise RuntimeError(
            'Docker server identity is missing required fields: ' + ', '.join(missing)
        )
    return fields


def collect_controller_provenance() -> dict:
    """Inspect and record the controller, Docker client, and Docker server."""
    context = controller_context()
    client_version = subprocess.run(
        ['docker', 'version', '--format', '{{.Client.Version}}'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not client_version:
        raise RuntimeError('Docker client did not report its version')
    return {
        'execution_mode': context.mode,
        'project_version': project_version(),
        'image': _controller_image_identity() if context.mode == 'container' else None,
        'docker_client_version': client_version,
        'docker_server': docker_server_identity(),
    }


def _mapping(values, controller_name, host_name):
    controller_root = _absolute_root(values, controller_name)
    host_root = _absolute_root(values, host_name)
    return PathMapping(controller_root=controller_root, host_root=host_root)


def _absolute_root(values, name):
    raw = values.get(name)
    if not raw:
        raise ControllerConfigurationError(
            f'{name} is required when {CONTROLLER_MODE_ENV}=container'
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ControllerConfigurationError(f'{name} must be an absolute path')
    return path.resolve()


def _non_negative_id(values, name):
    raw = values.get(name)
    if raw is None:
        raise ControllerConfigurationError(
            f'{name} is required when {CONTROLLER_MODE_ENV}=container'
        )
    try:
        identifier = int(raw)
    except ValueError as exc:
        raise ControllerConfigurationError(f'{name} must be an integer') from exc
    if identifier < 0:
        raise ControllerConfigurationError(f'{name} must not be negative')
    return identifier


def _controller_image_identity():
    container_id = os.environ.get('HOSTNAME')
    if not container_id:
        raise RuntimeError('Cannot inspect controller image: HOSTNAME is not set')
    container_result = subprocess.run(
        ['docker', 'container', 'inspect', container_id],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        container = json.loads(container_result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError('Cannot inspect the running controller container') from exc
    image_id = container.get('Image')
    image_reference = container.get('Config', {}).get('Image')
    expected_reference = os.environ.get(CONTROLLER_IMAGE_ENV)
    if expected_reference and image_reference != expected_reference:
        raise RuntimeError(
            f'Running controller image is {image_reference!r}, expected '
            f'{expected_reference!r}'
        )
    if not image_id or not image_reference:
        raise RuntimeError('Controller container inspection has no image identity')

    image_result = subprocess.run(
        ['docker', 'image', 'inspect', image_id],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        image = json.loads(image_result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError('Cannot inspect the controller image') from exc
    if image.get('Id') != image_id:
        raise RuntimeError('Controller image ID does not match Docker inspection')
    digests = image.get('RepoDigests') or ()
    labels = image.get('Config', {}).get('Labels') or {}
    return {
        'reference': image_reference,
        'id': image_id,
        'digest': digests[0].partition('@')[2] if digests else image_id,
        'revision': labels.get('org.opencontainers.image.revision'),
    }
