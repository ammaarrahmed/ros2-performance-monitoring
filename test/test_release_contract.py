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

import importlib
from pathlib import Path
import subprocess
import sys

import pytest

from ros2_performance_monitoring import __version__
import ros2_performance_monitoring.cli as cli
from ros2_performance_monitoring.release_contract import ReleaseContractError
from ros2_performance_monitoring.release_contract import validate_release_contract
import ros2_performance_monitoring.version as version_module
from ros2_performance_monitoring.version import package_xml_version


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_VERSION = package_xml_version(REPOSITORY_ROOT / 'package.xml')
REVISION = 'a' * 40


def test_package_metadata_and_runtime_share_package_xml_version(monkeypatch, capsys):
    setup_version = subprocess.run(
        [sys.executable, 'setup.py', '--version'],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert setup_version == PROJECT_VERSION
    assert __version__ == PROJECT_VERSION

    importlib.reload(cli)
    monkeypatch.setattr(sys, 'argv', ['ros2-performance-monitoring', '--version'])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == (
        f'ros2-performance-monitoring {PROJECT_VERSION}'
    )


def test_release_contract_accepts_matching_identity():
    validate_release_contract(
        PROJECT_VERSION,
        PROJECT_VERSION,
        PROJECT_VERSION,
        REVISION,
    )


def test_installed_version_prefers_installed_package_xml(tmp_path, monkeypatch):
    install_root = tmp_path / 'install' / 'ros2_performance_monitoring'
    module = (
        install_root
        / 'lib/python3.14/site-packages/ros2_performance_monitoring/version.py'
    )
    manifest = install_root / 'share/ros2_performance_monitoring/package.xml'
    module.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    module.touch()
    manifest.write_text(
        '<package><version>2.3.4</version></package>',
        encoding='utf-8',
    )
    monkeypatch.setattr(version_module, '__file__', str(module))
    monkeypatch.setattr(version_module, 'version', lambda distribution: '0.0.0')

    assert version_module.project_version() == '2.3.4'


@pytest.mark.parametrize(
    ('package_version', 'release_tag', 'oci_version', 'revision', 'message'),
    (
        ('0.0.0', '0.0.0', '0.0.0', REVISION, 'release placeholder'),
        ('dev', 'dev', 'dev', REVISION, 'release placeholder'),
        ('1.0', '1.0', '1.0', REVISION, 'not MAJOR.MINOR.PATCH'),
        (PROJECT_VERSION, 'v' + PROJECT_VERSION, PROJECT_VERSION, REVISION,
         'release tag'),
        (PROJECT_VERSION, PROJECT_VERSION, '9.9.9', REVISION, 'OCI version'),
        (PROJECT_VERSION, PROJECT_VERSION, PROJECT_VERSION, 'abc123',
         'full lowercase'),
    ),
)
def test_release_contract_rejects_mismatched_or_placeholder_identity(
    package_version,
    release_tag,
    oci_version,
    revision,
    message,
):
    with pytest.raises(ReleaseContractError, match=message):
        validate_release_contract(
            package_version,
            release_tag,
            oci_version,
            revision,
        )
