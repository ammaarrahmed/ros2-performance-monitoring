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

"""Validate the immutable inputs used to publish project runtime images."""

import argparse
from pathlib import Path
import re

from .version import package_xml_version


FULL_COMMIT_PATTERN = re.compile(r'[0-9a-f]{40}')
RELEASE_VERSION_PATTERN = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
PLACEHOLDER_VERSIONS = frozenset({'0.0.0', 'dev', 'source', 'unknown'})


class ReleaseContractError(ValueError):
    """Report inconsistent or unsafe release identity metadata."""


def validate_release_contract(
    package_version: str,
    release_tag: str,
    oci_version: str,
    revision: str,
) -> None:
    """Require package, release, and image identities to agree exactly."""
    if package_version in PLACEHOLDER_VERSIONS:
        raise ReleaseContractError(
            f'package version {package_version!r} is a release placeholder'
        )
    if RELEASE_VERSION_PATTERN.fullmatch(package_version) is None:
        raise ReleaseContractError(
            f'package version {package_version!r} is not MAJOR.MINOR.PATCH'
        )
    if release_tag != package_version:
        raise ReleaseContractError(
            f'release tag {release_tag!r} does not match package version '
            f'{package_version!r}'
        )
    if oci_version != package_version:
        raise ReleaseContractError(
            f'OCI version {oci_version!r} does not match package version '
            f'{package_version!r}'
        )
    if FULL_COMMIT_PATTERN.fullmatch(revision) is None:
        raise ReleaseContractError(
            'revision must be the full lowercase 40-character Git commit'
        )


def main() -> int:
    """Validate release arguments for the publication workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-xml', type=Path, default=Path('package.xml'))
    parser.add_argument('--release-tag', required=True)
    parser.add_argument('--oci-version')
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    package_version = package_xml_version(args.package_xml)
    oci_version = args.oci_version or package_version
    try:
        validate_release_contract(
            package_version,
            args.release_tag,
            oci_version,
            args.revision,
        )
    except ReleaseContractError as exc:
        parser.error(str(exc))
    print(package_version)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
