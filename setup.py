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

from glob import glob
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from setuptools import find_packages
from setuptools import setup


package_name = 'ros2_performance_monitoring'
package_version = ET.parse(Path(__file__).parent / 'package.xml').getroot().findtext(
    'version'
)


setup(
    name=package_name,
    version=package_version,
    packages=find_packages(exclude=['test']),
    package_data={
        package_name: [
            'packaged_target.Dockerfile',
            'rclcpp_target.Dockerfile',
            'ros2_benchmark_container.repos',
        ],
    },
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, [
            'package.xml',
            'compose.dashboard.yml',
            'compose.yml',
            'Dockerfile',
            'requirements-container.txt',
        ]),
        (os.path.join('share', package_name, 'doc'), glob('doc/*.md')),
        (os.path.join('share', package_name, 'grafana'), glob('grafana/*.md')),
        (os.path.join('share', package_name, 'config', 'prometheus'),
         glob('config/prometheus/*')),
        (os.path.join('share', package_name, 'config', 'grafana', 'dashboards'),
         glob('config/grafana/dashboards/*')),
        (os.path.join('share', package_name, 'config', 'grafana', 'provisioning',
                      'datasources'), glob('config/grafana/provisioning/datasources/*')),
        (os.path.join('share', package_name, 'config', 'grafana', 'provisioning',
                      'dashboards'), glob('config/grafana/provisioning/dashboards/*')),
    ],
    install_requires=['PyYAML', 'setuptools<81', 'vcstool'],
    zip_safe=True,
    maintainer='Ammaar Ahmed',
    maintainer_email='ammaarlatif53@gmail.com',
    description='Local-first dashboard and exporter tooling for ROS 2 performance visibility.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'ros2-performance-monitoring=ros2_performance_monitoring.cli:main',
            'ros2-performance-exporter=ros2_performance_monitoring.exporter:main',
        ],
    },
)
