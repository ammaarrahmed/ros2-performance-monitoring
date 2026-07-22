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

from pathlib import Path
import shutil
import subprocess


def build_container(ros_distro: str, cache_dir: str) -> Path:
    relative_path = Path(cache_dir)
    absolute_path = relative_path.expanduser().resolve()
    if shutil.which('docker') is None:
        raise RuntimeError('Docker executable was not found on PATH')

    builder_name = f'ros2-performance-monitoring-{ros_distro}-builder'
    result = subprocess.run(['docker', 'buildx', 'inspect', builder_name], capture_output=True)
    if result.returncode != 0:
        subprocess.run(
            ['docker', 'buildx', 'create', '--name', builder_name, '--use'],
            cwd=absolute_path,
            check=True,
        )
    else:
        subprocess.run(['docker', 'buildx', 'use', builder_name], cwd=absolute_path, check=True)
    subprocess.run(
        ['chmod', '+x', 'docker/build', 'docker/run', 'docker/deploy', 'docker/attach'],
        cwd=absolute_path,
        check=True,
    )
    subprocess.run(['docker/build', '-d', ros_distro], cwd=absolute_path, check=True)
    print(f'Container Successfully built for {ros_distro} at : {relative_path}')
    return relative_path
