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
from .controller import resolve_cache_path
from .source_dependencies import SourceDependencySnapshot


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


@dataclass(frozen=True)
class BuildConfiguration:
    """Record settings that can affect benchmark image contents."""

    schema_version: int = 1
    cmake_build_type: str = 'Release'
    benchmark_builder: str = 'upstream-combined-dockerfile-v1'
    source_overlay_builder: str = 'colcon-merge-install-v4'
    source_overlay_parallel_workers: int = 2
    benchmark_runner_patch: str = 'multi-process-results-dir-v1'

    def __post_init__(self) -> None:
        allowed_build_types = {'Debug', 'MinSizeRel', 'RelWithDebInfo', 'Release'}
        if self.cmake_build_type not in allowed_build_types:
            raise ValueError(
                f'Unsupported CMake build type: {self.cmake_build_type!r}'
            )
        if self.source_overlay_parallel_workers < 1:
            raise ValueError('Source overlay parallel workers must be positive')

    def to_dict(self) -> dict:
        """Return a stable, serializable build configuration."""
        return {
            'schema_version': self.schema_version,
            'cmake_build_type': self.cmake_build_type,
            'benchmark_builder': self.benchmark_builder,
            'source_overlay_builder': self.source_overlay_builder,
            'source_overlay_parallel_workers': self.source_overlay_parallel_workers,
            'benchmark_runner_patch': self.benchmark_runner_patch,
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
    source_dependencies: SourceDependencySnapshot | None = None
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
        if self.client_target.source != 'build' and self.source_dependencies is not None:
            raise ValueError('Source dependencies require a source-built rclcpp target')

    @property
    def target_key(self) -> str:
        """Return the content identity for the final benchmark target."""
        return _digest(self.identity_payload())

    @property
    def image_name(self) -> str:
        """Return the exact final image tag for this target."""
        return (
            'ros2-performance-monitoring/benchmark:'
            f'{self.ros_distro}-{self.architecture}-{self.target_key[:16]}'
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
            'schema_version': 2,
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
            'source_dependencies': (
                None
                if self.source_dependencies is None
                else self.source_dependencies.identity_payload()
            ),
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
        build_configuration_json = _canonical_json(self.build_configuration.to_dict())
        return {
            f'{LABEL_PREFIX}.target-key': self.target_key,
            f'{LABEL_PREFIX}.ros-distro': self.ros_distro,
            f'{LABEL_PREFIX}.architecture': self.architecture,
            f'{LABEL_PREFIX}.benchmark-repository': self.benchmark_repository_url,
            f'{LABEL_PREFIX}.benchmark-ref': self.benchmark_requested_ref,
            f'{LABEL_PREFIX}.benchmark-commit': self.benchmark_resolved_commit,
            f'{LABEL_PREFIX}.client-source': self.client_target.source,
            f'{LABEL_PREFIX}.client-repository': self.client_target.repository_url or '',
            f'{LABEL_PREFIX}.client-ref': self.client_target.requested_ref,
            f'{LABEL_PREFIX}.client-commit': self.client_target.resolved_commit,
            f'{LABEL_PREFIX}.source-dependencies-sha256': (
                self.source_dependencies.snapshot_key
                if self.source_dependencies is not None
                else ''
            ),
            f'{LABEL_PREFIX}.build-configuration-sha256': hashlib.sha256(
                build_configuration_json.encode()
            ).hexdigest(),
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
    benchmark_context = resolve_cache_path(cache_dir)
    if not (benchmark_context / 'Dockerfile').is_file():
        raise RuntimeError(
            f'Benchmark repository at {benchmark_context} has no Dockerfile'
        )
    _verify_git_checkout(
        benchmark_context,
        spec.benchmark_resolved_commit,
        'benchmark repository',
    )
    if spec.client_target.source == 'build':
        _verify_git_checkout(
            spec.client_target.checkout_path,
            spec.client_target.resolved_commit,
            'rclcpp target',
        )
        if spec.source_dependencies is not None:
            if spec.source_dependencies.checkout_path is None:
                raise RuntimeError('Source dependency snapshot has no checkout path')
            for dependency in spec.source_dependencies.repositories:
                _verify_git_checkout(
                    dependency.checkout_path,
                    dependency.resolved_commit,
                    f'source dependency {dependency.path!r}',
                )
    benchmark_scripts = _prepare_benchmark_scripts(spec, benchmark_context)
    _select_builder(spec.architecture)
    _build_final_image(spec, benchmark_context, benchmark_scripts)
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
    expected_image = validate_benchmark_container(spec)
    _verify_manifest(
        spec,
        ['docker', 'exec', spec.container_name, 'cat', MANIFEST_PATH],
    )
    _verify_runtime(
        spec,
        ['docker', 'exec', spec.container_name, 'bash', '-lc'],
    )
    return expected_image


def validate_benchmark_container(spec: BenchmarkImageSpec) -> VerifiedImage:
    """Validate stopped or running container identity without executing in it."""
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


def _build_final_image(
    spec: BenchmarkImageSpec,
    benchmark_context: Path,
    benchmark_scripts: Path,
) -> None:
    manifest_b64 = base64.b64encode(_canonical_json(spec.manifest()).encode()).decode()
    if spec.client_target.source == 'build':
        if spec.client_target.checkout_path is None:
            raise ValueError('A source-built rclcpp target requires a checkout path')
        fragment = SOURCE_DOCKERFILE
        source_context_arguments = [
            '--build-context', f'rclcpp={spec.client_target.checkout_path}',
            '--build-context',
            f'source-dependencies={_source_dependencies_context(spec, benchmark_context)}',
            '--build-arg',
            'SOURCE_OVERLAY_PARALLEL_WORKERS='
            f'{spec.build_configuration.source_overlay_parallel_workers}',
        ]
    elif spec.client_target.source == 'packaged':
        fragment = PACKAGED_DOCKERFILE
        source_context_arguments = []
    else:
        raise ValueError(f'Unsupported client-library source: {spec.client_target.source!r}')
    dockerfile = _prepare_combined_dockerfile(spec, benchmark_context, fragment)
    command = [
        'docker', 'buildx', 'build', '--load',
        '--platform', f'linux/{spec.architecture}',
        '--file', str(dockerfile),
        '--target', 'ros2-performance-monitoring-target',
        '--build-context', f'benchmark={benchmark_scripts}',
        *source_context_arguments,
        '--build-arg', f'BASE_IMAGE=osrf/ros:{spec.ros_distro}-desktop',
        '--build-arg', f'ROS_DISTRO={spec.ros_distro}',
        '--build-arg', f'CMAKE_BUILD_TYPE={spec.build_configuration.cmake_build_type}',
        '--build-arg', f'TARGET_MANIFEST_B64={manifest_b64}',
        '--tag', spec.image_name,
    ]
    command.extend(_label_arguments(spec.labels()))
    command.append(str(benchmark_context))
    subprocess.run(command, check=True)


def _prepare_combined_dockerfile(
    spec: BenchmarkImageSpec,
    benchmark_context: Path,
    fragment: Path,
) -> Path:
    managed_cache = benchmark_context.with_name(f'{benchmark_context.name}-targets')
    dockerfile = managed_cache / 'dockerfiles' / spec.target_key / 'Dockerfile'
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    upstream_text = (benchmark_context / 'Dockerfile').read_text().rstrip()
    fragment_text = fragment.read_text().rstrip()
    dockerfile.write_text(f'{upstream_text}\n\n{fragment_text}\n')
    return dockerfile


def _source_dependencies_context(
    spec: BenchmarkImageSpec,
    benchmark_context: Path,
) -> Path:
    if spec.source_dependencies is not None:
        if spec.source_dependencies.checkout_path is None:
            raise ValueError('Source dependency snapshot has no checkout path')
        return spec.source_dependencies.checkout_path
    managed_cache = benchmark_context.with_name(f'{benchmark_context.name}-targets')
    empty_context = managed_cache / 'source-dependencies' / 'empty'
    empty_context.mkdir(parents=True, exist_ok=True)
    (empty_context / '.empty').touch(exist_ok=True)
    return empty_context


def _verify_git_checkout(path: Path | None, expected_commit: str, label: str) -> None:
    if path is None:
        raise RuntimeError(f'The {label} has no checkout path')
    revision = subprocess.run(
        ['git', '-C', str(path), 'rev-parse', '--verify', 'HEAD'],
        check=False,
        capture_output=True,
        text=True,
    )
    if revision.returncode != 0 or revision.stdout.strip() != expected_commit:
        raise RuntimeError(
            f'The {label} at {path} is not checked out at {expected_commit}'
        )
    status = subprocess.run(
        ['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all'],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise RuntimeError(
            f'The {label} at {path} has local changes; refusing an unverifiable build'
        )


def _prepare_benchmark_scripts(
    spec: BenchmarkImageSpec,
    benchmark_context: Path,
) -> Path:
    managed_cache = benchmark_context.with_name(f'{benchmark_context.name}-targets')
    destination = managed_cache / 'benchmark-scripts' / spec.target_key / 'benchmark'
    shutil.copytree(
        benchmark_context / 'benchmark',
        destination,
        dirs_exist_ok=True,
    )
    runner = destination / 'scripts' / 'runners' / 'run_multi_process_benchmark.sh'
    if runner.is_file():
        runner_text = runner.read_text()
        if BROKEN_MULTI_PROCESS_COMMAND in runner_text:
            runner.write_text(
                runner_text.replace(BROKEN_MULTI_PROCESS_COMMAND, FIXED_MULTI_PROCESS_COMMAND)
            )
    return destination


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
