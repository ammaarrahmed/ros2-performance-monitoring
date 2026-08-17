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

import pytest
from ros2_performance_monitoring.benchmark_image import BenchmarkImageSpec
from ros2_performance_monitoring.benchmark_image import build_benchmark_image
from ros2_performance_monitoring.benchmark_image import detect_architecture
from ros2_performance_monitoring.client_target import DEFAULT_RCLCPP_REPOSITORY
from ros2_performance_monitoring.client_target import resolve_rclcpp_target
from ros2_performance_monitoring.container_provider import get_default_container_repo
from ros2_performance_monitoring.container_provider import setup_container_repo


pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.skipif(
        os.environ.get('ROS2_PERFORMANCE_RUN_DOCKER_INTEGRATION') != '1',
        reason='set ROS2_PERFORMANCE_RUN_DOCKER_INTEGRATION=1 to build an exact image',
    ),
]

ROLLING_INTEGRATION_RCLCPP_COMMIT = '20536064aac0d547e128d95337867b473c3efa85'


def test_source_target_builds_and_verifies_inside_image():
    cache_dir = os.environ.get(
        'ROS2_PERFORMANCE_INTEGRATION_CACHE',
        '~/.cache/ros2-performance-monitoring-integration',
    )
    ros_distro = os.environ.get('ROS2_PERFORMANCE_INTEGRATION_DISTRO', 'rolling')
    rclcpp_ref = os.environ.get(
        'ROS2_PERFORMANCE_INTEGRATION_RCLCPP_REF',
        ROLLING_INTEGRATION_RCLCPP_COMMIT,
    )
    benchmark_url, benchmark_ref = get_default_container_repo()
    target = resolve_rclcpp_target(
        DEFAULT_RCLCPP_REPOSITORY,
        rclcpp_ref,
        cache_dir,
    )
    benchmark_commit = setup_container_repo(
        benchmark_url,
        benchmark_ref,
        cache_dir,
    )
    spec = BenchmarkImageSpec(
        ros_distro=ros_distro,
        architecture=detect_architecture(),
        benchmark_repository_url=benchmark_url,
        benchmark_requested_ref=benchmark_ref,
        benchmark_resolved_commit=benchmark_commit,
        client_target=target,
    )

    verified = build_benchmark_image(spec, cache_dir)

    assert verified.target_key == spec.target_key
    assert verified.image_name == spec.image_name
    assert verified.image_id.startswith('sha256:')
    assert verified.image_digest.startswith('sha256:')
