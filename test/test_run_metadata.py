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

    generation_rundata(args, str(tmp_path / 'results'), image_spec, verified_image)

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

    generation_rundata(args, str(tmp_path), image_spec, verified_image)

    metadata_path = next(tmp_path.glob('metadata_*.json'))
    client_metadata = json.loads(metadata_path.read_text())['client_library_under_test']
    assert client_metadata == {
        'name': 'rclcpp',
        'repository_url': None,
        'ref': 'ros-lyrical-packages',
        'resolved_commit_hash': 'packaged',
        'source': 'packaged',
    }
