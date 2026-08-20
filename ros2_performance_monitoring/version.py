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

"""Resolve the project version from the ROS package metadata."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from pathlib import Path
import xml.etree.ElementTree as ET


PACKAGE_DISTRIBUTION = 'ros2-performance-monitoring'
PACKAGE_NAME = 'ros2_performance_monitoring'


def project_version() -> str:
    """Return the package.xml version in source or installed metadata."""
    module_path = Path(__file__).resolve()
    manifests = [module_path.parents[1] / 'package.xml']
    manifests.extend(
        parent / 'share' / PACKAGE_NAME / 'package.xml'
        for parent in module_path.parents
    )
    for manifest in manifests:
        if manifest.is_file():
            return package_xml_version(manifest)
    try:
        return version(PACKAGE_DISTRIBUTION)
    except PackageNotFoundError as exc:
        raise RuntimeError(
            f'Cannot determine {PACKAGE_DISTRIBUTION} version'
        ) from exc


def package_xml_version(manifest: Path) -> str:
    """Read a non-empty version from a ROS package manifest."""
    value = ET.parse(manifest).getroot().findtext('version')
    if not value or not value.strip():
        raise ValueError(f'{manifest} does not define a package version')
    return value.strip()
