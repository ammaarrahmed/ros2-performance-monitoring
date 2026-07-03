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

from setuptools import find_packages
from setuptools import setup


package_name = 'ros2_performance_monitoring'


setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    package_data={package_name: ['ros2_benchmark_container.repos']},
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'doc'), glob('doc/*.md')),
        (os.path.join('share', package_name, 'grafana'), glob('grafana/*.md')),
    ],
    install_requires=['setuptools<81', 'vcstool'],
    zip_safe=True,
    maintainer='Ammaar Ahmed',
    maintainer_email='ammaarlatif53@gmail.com',
    description='Local-first dashboard and exporter tooling for ROS 2 performance visibility.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'ros2-performance-monitoring=ros2_performance_monitoring.cli:main',
        ],
    },
)
