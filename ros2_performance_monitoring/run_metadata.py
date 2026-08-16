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
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

from .benchmark_image import BenchmarkImageSpec
from .benchmark_image import VerifiedImage


def generation_rundata(
    args: argparse.Namespace,
    results_dir: str,
    image_spec: BenchmarkImageSpec,
    verified_image: VerifiedImage,
) -> None:
    run_timestamp = datetime.now(timezone.utc)
    file_timestamp = run_timestamp.strftime('%Y%m%d_%H%M%S')
    iso_format = run_timestamp.isoformat()
    py_ver = sys.version.split()[0]
    machine = platform.machine()
    os_name = platform.system()
    client_target = image_spec.client_target
    run_data = {
        'host_environment': {
            'timestamp': iso_format,
            'Python version': py_ver,
            'architecture': machine,
            'OS': os_name,
        },
        'run_configuration': {
            'ros_distro': args.ros_distro,
            'executor': args.executor,
            'duration': args.duration,
            'client_library': client_target.name,
            'cpuset_cpus': args.cpuset_cpus,
        },
        'benchmark_repo': {
            'url': image_spec.benchmark_repository_url,
            'ref': image_spec.benchmark_requested_ref,
            'resolved_commit_hash': image_spec.benchmark_resolved_commit,
        },
        'client_library_under_test': {
            'name': client_target.name,
            'repository_url': client_target.repository_url,
            'ref': client_target.requested_ref,
            'resolved_commit_hash': client_target.resolved_commit,
            'source': client_target.source,
        },
        'benchmark_image': {
            'name': verified_image.image_name,
            'id': verified_image.image_id,
            'digest': verified_image.image_digest,
            'target_key': verified_image.target_key,
        },
    }

    output_dir = Path(results_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_file = output_dir / f'metadata_{file_timestamp}.json'
    with open(metadata_file, 'w') as metadata_stream:
        json.dump(run_data, metadata_stream, indent=4)
    print(f'Run metadata saved to : {output_dir} / {metadata_file}')
