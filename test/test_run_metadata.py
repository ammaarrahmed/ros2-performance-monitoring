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

import argparse
import json

from ros2_performance_monitoring.benchmark_image import BenchmarkImageSpec
from ros2_performance_monitoring.benchmark_image import VerifiedImage
from ros2_performance_monitoring.client_target import ClientLibraryTarget
from ros2_performance_monitoring.parsers.ros2_benchmark_container import latest_run_metadata
from ros2_performance_monitoring.run_metadata import generation_rundata


def test_run_metadata_uses_resolved_and_verified_target(tmp_path):
    client_target = ClientLibraryTarget(
        name='rclcpp',
        source='build',
        repository_url='https://github.com/example/rclcpp.git',
        requested_ref='feature/test',
        resolved_commit='b' * 40,
        checkout_path=tmp_path / 'checkout',
    )
    image_spec = BenchmarkImageSpec(
        ros_distro='rolling',
        architecture='amd64',
        benchmark_repository_url='https://github.com/ros2/ros2-benchmark-container',
        benchmark_requested_ref='rolling',
        benchmark_resolved_commit='a' * 40,
        client_target=client_target,
    )
    verified_image = VerifiedImage(
        image_name=image_spec.image_name,
        image_id=f'sha256:{"c" * 64}',
        image_digest=f'sha256:{"d" * 64}',
        target_key=image_spec.target_key,
    )
    args = argparse.Namespace(
        ros_distro='rolling',
        executor='EventsExecutor',
        duration=1,
        cpuset_cpus='0-1',
    )

    generation_rundata(
        args,
        str(tmp_path / 'results'),
        image_spec,
        verified_image,
        controller_provenance=_controller_provenance(),
    )

    metadata_path = next((tmp_path / 'results').glob('metadata_*.json'))
    metadata = json.loads(metadata_path.read_text())
    assert metadata['benchmark_repo'] == {
        'url': image_spec.benchmark_repository_url,
        'ref': image_spec.benchmark_requested_ref,
        'resolved_commit_hash': image_spec.benchmark_resolved_commit,
    }
    assert metadata['client_library_under_test'] == {
        'name': 'rclcpp',
        'repository_url': 'https://github.com/example/rclcpp.git',
        'ref': 'feature/test',
        'resolved_commit_hash': 'b' * 40,
        'source': 'build',
    }
    assert metadata['benchmark_image'] == {
        'name': verified_image.image_name,
        'id': verified_image.image_id,
        'digest': verified_image.image_digest,
        'target_key': verified_image.target_key,
    }
    assert metadata['controller'] == _controller_provenance()


def test_packaged_metadata_is_explicit(tmp_path):
    target = ClientLibraryTarget.packaged('lyrical')
    image_spec = BenchmarkImageSpec(
        ros_distro='lyrical',
        architecture='amd64',
        benchmark_repository_url='https://github.com/ros2/ros2-benchmark-container',
        benchmark_requested_ref='rolling',
        benchmark_resolved_commit='a' * 40,
        client_target=target,
    )
    verified_image = VerifiedImage(
        image_name=image_spec.image_name,
        image_id=f'sha256:{"c" * 64}',
        image_digest=f'sha256:{"c" * 64}',
        target_key=image_spec.target_key,
    )
    args = argparse.Namespace(
        ros_distro='lyrical',
        executor='EventsExecutor',
        duration=1,
        cpuset_cpus=None,
    )

    generation_rundata(
        args,
        str(tmp_path),
        image_spec,
        verified_image,
        controller_provenance=_controller_provenance(),
    )

    metadata_path = next(tmp_path.glob('metadata_*.json'))
    client_metadata = json.loads(metadata_path.read_text())['client_library_under_test']
    assert client_metadata == {
        'name': 'rclcpp',
        'repository_url': None,
        'ref': 'ros-lyrical-packages',
        'resolved_commit_hash': 'packaged',
        'source': 'packaged',
    }


def test_experiment_metadata_uses_stable_filename_and_trial_id(tmp_path):
    target = ClientLibraryTarget.packaged('rolling')
    image_spec = BenchmarkImageSpec(
        ros_distro='rolling',
        architecture='amd64',
        benchmark_repository_url='https://github.com/ros2/ros2-benchmark-container',
        benchmark_requested_ref='rolling',
        benchmark_resolved_commit='a' * 40,
        client_target=target,
    )
    verified_image = VerifiedImage(
        image_name=image_spec.image_name,
        image_id=f'sha256:{"c" * 64}',
        image_digest=f'sha256:{"c" * 64}',
        target_key=image_spec.target_key,
    )
    args = argparse.Namespace(
        ros_distro='rolling',
        executor='EventsExecutor',
        duration=1,
        cpuset_cpus='0',
        suite='service-rclcpp-minimal',
    )

    metadata_path = generation_rundata(
        args,
        str(tmp_path),
        image_spec,
        verified_image,
        metadata_filename='metadata.json',
        run_id='candidate-measured-001',
        controller_provenance=_controller_provenance(),
    )

    assert metadata_path == tmp_path / 'metadata.json'
    metadata = latest_run_metadata(tmp_path)
    assert metadata['run_id'] == 'candidate-measured-001'
    assert metadata['run_configuration']['suite'] == 'service-rclcpp-minimal'


def _controller_provenance():
    return {
        'execution_mode': 'host',
        'project_version': '0.0.0',
        'image': None,
        'docker_client_version': '27.5.1',
        'docker_server': {
            'id': 'daemon-id',
            'name': 'benchmark-host',
            'version': '27.5.1',
        },
    }
