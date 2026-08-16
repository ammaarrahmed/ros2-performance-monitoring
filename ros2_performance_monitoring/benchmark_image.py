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

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess

from .client_target import ClientLibraryTarget


LABEL_PREFIX = 'ros2-performance-monitoring'
MANIFEST_PATH = '/etc/ros2-performance-monitoring/target-manifest.json'
PACKAGED_DOCKERFILE = Path(__file__).with_name('packaged_target.Dockerfile')
SOURCE_DOCKERFILE = Path(__file__).with_name('rclcpp_target.Dockerfile')
SUPPORTED_ARCHITECTURES = {
    'aarch64': 'arm64',
    'amd64': 'amd64',
    'arm64': 'arm64',
    'x86_64': 'amd64',
}


@dataclass(frozen=True)
class BuildConfiguration:
    """Record settings that can affect benchmark image contents."""

    schema_version: int = 1
    cmake_build_type: str = 'Release'
    benchmark_builder: str = 'upstream-dockerfile-v1'
    source_overlay_builder: str = 'colcon-merge-install-v1'

    def __post_init__(self) -> None:
        allowed_build_types = {'Debug', 'MinSizeRel', 'RelWithDebInfo', 'Release'}
        if self.cmake_build_type not in allowed_build_types:
            raise ValueError(
                f'Unsupported CMake build type: {self.cmake_build_type!r}'
            )

    def to_dict(self) -> dict:
        """Return a stable, serializable build configuration."""
        return {
            'schema_version': self.schema_version,
            'cmake_build_type': self.cmake_build_type,
            'benchmark_builder': self.benchmark_builder,
            'source_overlay_builder': self.source_overlay_builder,
        }


@dataclass(frozen=True)
class BenchmarkImageSpec:
    """Identify all inputs that affect a benchmark image."""

    ros_distro: str
    architecture: str
    benchmark_repository_url: str
    benchmark_requested_ref: str
    benchmark_resolved_commit: str
    client_target: ClientLibraryTarget
    build_configuration: BuildConfiguration = BuildConfiguration()

    def __post_init__(self) -> None:
        if self.architecture not in ('amd64', 'arm64'):
            raise ValueError(f'Unsupported container architecture: {self.architecture!r}')
        if not re.fullmatch(r'[0-9a-f]{40}', self.benchmark_resolved_commit):
            raise ValueError('Benchmark repository commit must be a full lowercase SHA')
        if self.client_target.source == 'build' and not re.fullmatch(
            r'[0-9a-f]{40}', self.client_target.resolved_commit
        ):
            raise ValueError('Source-built rclcpp commit must be a full lowercase SHA')

    @property
    def target_key(self) -> str:
        """Return the content identity for the final benchmark target."""
        return _digest(self.identity_payload())

    @property
    def base_key(self) -> str:
        """Return the content identity for the upstream benchmark base image."""
        return _digest({
            'schema_version': 1,
            'ros_distro': self.ros_distro,
            'architecture': self.architecture,
            'benchmark_repository': {
                'url': self.benchmark_repository_url,
                'requested_ref': self.benchmark_requested_ref,
                'resolved_commit': self.benchmark_resolved_commit,
            },
            'build_configuration': {
                'schema_version': self.build_configuration.schema_version,
                'benchmark_builder': self.build_configuration.benchmark_builder,
            },
        })

    @property
    def image_name(self) -> str:
        """Return the exact final image tag for this target."""
        return (
            'ros2-performance-monitoring/benchmark:'
            f'{self.ros_distro}-{self.architecture}-{self.target_key[:16]}'
        )

    @property
    def base_image_name(self) -> str:
        """Return the internal upstream benchmark image tag."""
        return (
            'ros2-performance-monitoring/benchmark-base:'
            f'{self.ros_distro}-{self.architecture}-{self.base_key[:16]}'
        )

    @property
    def container_name(self) -> str:
        """Return the exact retained-container name for this target."""
        return (
            f'ros2-performance-monitoring-{self.ros_distro}-'
            f'{self.architecture}-{self.target_key[:16]}'
        )

    @property
    def expected_rclcpp_prefix(self) -> str:
        """Return the required active rclcpp installation prefix."""
        if self.client_target.source == 'build':
            return '/target_ws/install'
        return f'/opt/ros/{self.ros_distro}'

    def identity_payload(self) -> dict:
        """Return the canonical identity inputs without host cache paths."""
        return {
            'schema_version': 1,
            'ros_distro': self.ros_distro,
            'architecture': self.architecture,
            'benchmark_repository': {
                'url': self.benchmark_repository_url,
                'requested_ref': self.benchmark_requested_ref,
                'resolved_commit': self.benchmark_resolved_commit,
            },
            'client_library': {
                'name': self.client_target.name,
                'source': self.client_target.source,
                'repository_url': self.client_target.repository_url,
                'requested_ref': self.client_target.requested_ref,
                'resolved_commit': self.client_target.resolved_commit,
            },
            'build_configuration': self.build_configuration.to_dict(),
        }

    def manifest(self) -> dict:
        """Return the target manifest stored inside the final image."""
        return {
            **self.identity_payload(),
            'target_key': self.target_key,
            'rclcpp_install_prefix': self.expected_rclcpp_prefix,
        }

    def labels(self) -> dict[str, str]:
        """Return labels used to reject unsafe image and container reuse."""
        manifest_json = _canonical_json(self.manifest())
        return {
            f'{LABEL_PREFIX}.target-key': self.target_key,
            f'{LABEL_PREFIX}.ros-distro': self.ros_distro,
            f'{LABEL_PREFIX}.architecture': self.architecture,
            f'{LABEL_PREFIX}.benchmark-commit': self.benchmark_resolved_commit,
            f'{LABEL_PREFIX}.client-source': self.client_target.source,
            f'{LABEL_PREFIX}.client-commit': self.client_target.resolved_commit,
            f'{LABEL_PREFIX}.manifest-sha256': hashlib.sha256(
                manifest_json.encode()
            ).hexdigest(),
        }


@dataclass(frozen=True)
class VerifiedImage:
    """Report immutable image provenance after all verification checks pass."""

    image_name: str
    image_id: str
    image_digest: str
    target_key: str


def detect_architecture() -> str:
    """Translate the host machine name into a Docker architecture."""
    machine = platform.machine().lower()
    try:
        return SUPPORTED_ARCHITECTURES[machine]
    except KeyError as exc:
        raise RuntimeError(f'Unsupported host architecture: {machine!r}') from exc


def build_benchmark_image(spec: BenchmarkImageSpec, cache_dir: str) -> VerifiedImage:
    """Build an exact packaged or source-overlay benchmark image and verify it."""
    if shutil.which('docker') is None:
        raise RuntimeError('Docker executable was not found on PATH')
    benchmark_context = Path(cache_dir).expanduser().resolve()
    if not (benchmark_context / 'Dockerfile').is_file():
        raise RuntimeError(
            f'Benchmark repository at {benchmark_context} has no Dockerfile'
        )
    _select_builder(spec.architecture)
    _build_base_image(spec, benchmark_context)
    _build_final_image(spec)
    return verify_benchmark_image(spec)


def benchmark_image_exists(spec: BenchmarkImageSpec) -> bool:
    """Return whether the exact target image exists locally."""
    result = subprocess.run(
        ['docker', 'image', 'inspect', spec.image_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def benchmark_container_exists(spec: BenchmarkImageSpec) -> bool:
    """Return whether the exact retained target container exists locally."""
    result = subprocess.run(
        ['docker', 'container', 'inspect', spec.container_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def verify_benchmark_image(spec: BenchmarkImageSpec) -> VerifiedImage:
    """Verify labels, manifest, prefix, and linked rclcpp for an exact image."""
    result = subprocess.run(
        ['docker', 'image', 'inspect', spec.image_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'Benchmark image {spec.image_name} does not exist for target '
            f'{spec.target_key[:16]}'
        )
    try:
        image_data = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f'Cannot inspect benchmark image {spec.image_name}') from exc
    _verify_labels(spec, image_data.get('Config', {}).get('Labels') or {}, 'image')
    _verify_manifest(
        spec,
        ['docker', 'run', '--rm', '--entrypoint', 'cat', spec.image_name, MANIFEST_PATH],
    )
    _verify_runtime(
        spec,
        ['docker', 'run', '--rm', '--entrypoint', 'bash', spec.image_name, '-lc'],
    )
    image_id = image_data.get('Id')
    if not image_id:
        raise RuntimeError(f'Benchmark image {spec.image_name} has no image ID')
    repo_digests = image_data.get('RepoDigests') or ()
    image_digest = repo_digests[0].partition('@')[2] if repo_digests else image_id
    return VerifiedImage(
        image_name=spec.image_name,
        image_id=image_id,
        image_digest=image_digest,
        target_key=spec.target_key,
    )


def verify_benchmark_container(spec: BenchmarkImageSpec) -> VerifiedImage:
    """Verify a retained container and its active rclcpp installation."""
    result = subprocess.run(
        ['docker', 'container', 'inspect', spec.container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f'Benchmark container {spec.container_name} does not exist')
    try:
        container_data = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f'Cannot inspect benchmark container {spec.container_name}') from exc
    _verify_labels(
        spec,
        container_data.get('Config', {}).get('Labels') or {},
        'container',
    )
    expected_image = verify_benchmark_image(spec)
    actual_image_id = container_data.get('Image')
    if actual_image_id != expected_image.image_id:
        raise RuntimeError(
            f'Cannot reuse container {spec.container_name}: image ID is '
            f'{actual_image_id!r}, expected {expected_image.image_id!r}'
        )
    _verify_manifest(
        spec,
        ['docker', 'exec', spec.container_name, 'cat', MANIFEST_PATH],
    )
    _verify_runtime(
        spec,
        ['docker', 'exec', spec.container_name, 'bash', '-lc'],
    )
    return expected_image


def _select_builder(architecture: str) -> None:
    builder_name = f'ros2-performance-monitoring-{architecture}-builder'
    result = subprocess.run(
        ['docker', 'buildx', 'inspect', builder_name],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        subprocess.run(['docker', 'buildx', 'use', builder_name], check=True)
        return
    subprocess.run(
        ['docker', 'buildx', 'create', '--name', builder_name, '--use'],
        check=True,
    )


def _build_base_image(spec: BenchmarkImageSpec, benchmark_context: Path) -> None:
    labels = {
        f'{LABEL_PREFIX}.base-key': spec.base_key,
        f'{LABEL_PREFIX}.ros-distro': spec.ros_distro,
        f'{LABEL_PREFIX}.architecture': spec.architecture,
        f'{LABEL_PREFIX}.benchmark-commit': spec.benchmark_resolved_commit,
    }
    command = [
        'docker', 'buildx', 'build', '--load',
        '--platform', f'linux/{spec.architecture}',
        '--target', 'ros2-benchmark-container',
        '--build-arg', f'ROS_DISTRO={spec.ros_distro}',
        '--build-arg', f'BASE_IMAGE=osrf/ros:{spec.ros_distro}-desktop',
        '--tag', spec.base_image_name,
    ]
    command.extend(_label_arguments(labels))
    command.append(str(benchmark_context))
    subprocess.run(command, check=True)


def _build_final_image(spec: BenchmarkImageSpec) -> None:
    manifest_b64 = base64.b64encode(_canonical_json(spec.manifest()).encode()).decode()
    if spec.client_target.source == 'build':
        if spec.client_target.checkout_path is None:
            raise ValueError('A source-built rclcpp target requires a checkout path')
        dockerfile = SOURCE_DOCKERFILE
        context = spec.client_target.checkout_path
    elif spec.client_target.source == 'packaged':
        dockerfile = PACKAGED_DOCKERFILE
        context = PACKAGED_DOCKERFILE.parent
    else:
        raise ValueError(f'Unsupported client-library source: {spec.client_target.source!r}')
    command = [
        'docker', 'buildx', 'build', '--load',
        '--platform', f'linux/{spec.architecture}',
        '--file', str(dockerfile),
        '--build-arg', f'BASE_IMAGE={spec.base_image_name}',
        '--build-arg', f'ROS_DISTRO={spec.ros_distro}',
        '--build-arg', f'CMAKE_BUILD_TYPE={spec.build_configuration.cmake_build_type}',
        '--build-arg', f'TARGET_MANIFEST_B64={manifest_b64}',
        '--tag', spec.image_name,
    ]
    command.extend(_label_arguments(spec.labels()))
    command.append(str(context))
    subprocess.run(command, check=True)


def _verify_labels(spec: BenchmarkImageSpec, actual_labels: dict, kind: str) -> None:
    for label, expected in spec.labels().items():
        actual = actual_labels.get(label)
        if actual != expected:
            raise RuntimeError(
                f'Cannot reuse {kind} for target {spec.target_key[:16]}: label '
                f'{label!r} is {actual!r}, expected {expected!r}'
            )


def _verify_manifest(spec: BenchmarkImageSpec, command: list[str]) -> None:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        actual_manifest = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError('Benchmark target manifest is not valid JSON') from exc
    if actual_manifest != spec.manifest():
        raise RuntimeError(
            f'Benchmark target manifest does not match target {spec.target_key[:16]}'
        )


def _verify_runtime(spec: BenchmarkImageSpec, command_prefix: list[str]) -> None:
    script = (
        'source "$RCLCPP_TARGET_PREFIX/setup.bash" && '
        'source /ws/install/setup.bash && '
        'ros2 pkg prefix rclcpp && '
        "printf '\\n__RCLCPP_LINKS__\\n' && "
        'ldd "$PERF_FRAMEWORK_INSTALL_DIR/irobot_benchmark/irobot_benchmark"'
    )
    result = subprocess.run(
        [*command_prefix, script],
        check=True,
        capture_output=True,
        text=True,
    )
    prefix_output, marker, links_output = result.stdout.partition('__RCLCPP_LINKS__')
    active_prefix = prefix_output.strip()
    if not marker or active_prefix != spec.expected_rclcpp_prefix:
        raise RuntimeError(
            f'Active rclcpp prefix is {active_prefix!r}, expected '
            f'{spec.expected_rclcpp_prefix!r}'
        )
    rclcpp_links = [
        line.strip() for line in links_output.splitlines()
        if 'librclcpp.so' in line
    ]
    expected_path_prefix = f'{spec.expected_rclcpp_prefix}/'
    if not rclcpp_links or any(expected_path_prefix not in line for line in rclcpp_links):
        details = '; '.join(rclcpp_links) if rclcpp_links else 'no librclcpp dependency'
        raise RuntimeError(
            f'Benchmark executable does not resolve rclcpp from '
            f'{spec.expected_rclcpp_prefix}: {details}'
        )


def _label_arguments(labels: dict[str, str]) -> list[str]:
    arguments = []
    for name, value in sorted(labels.items()):
        arguments.extend(('--label', f'{name}={value}'))
    return arguments


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))
