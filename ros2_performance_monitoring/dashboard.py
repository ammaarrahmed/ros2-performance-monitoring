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
from pathlib import Path
import subprocess

from ros2_performance_monitoring.exporters.prometheus import serve_metrics


PACKAGE_NAME = 'ros2_performance_monitoring'


def dashboard_up(input_path, port=9108):
    input_path = _validate_input(input_path)
    compose_file = _compose_file()
    _compose(compose_file, 'up', '-d')
    print('Grafana: http://localhost:3000')
    print('Prometheus: http://localhost:9090')
    print(f'Exporter: http://localhost:{port}/metrics')
    print('Press Ctrl+C to stop the exporter.')
    print('Run ros2-performance-monitoring dashboard down to stop Prometheus and Grafana.')
    serve_metrics(input_path, port=port)


def dashboard_down():
    _compose(_compose_file(), 'down')


def _validate_input(input_path):
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f'normalized metrics file does not exist: {path}')
    if not path.is_file():
        raise ValueError(f'normalized metrics path is not a file: {path}')
    return path


def _compose(compose_file, *args):
    command = ['docker', 'compose', '-f', str(compose_file), *args]
    try:
        subprocess.run(command, cwd=compose_file.parent, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError('docker compose is not available') from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f'docker compose failed with exit code {exc.returncode}') from exc


def _compose_file():
    for root in _package_roots():
        candidate = root / 'compose.dashboard.yml'
        if candidate.exists():
            return candidate
    raise FileNotFoundError('compose.dashboard.yml was not found')


def _package_roots():
    roots = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent,
    ]
    for prefix in os.environ.get('AMENT_PREFIX_PATH', '').split(os.pathsep):
        if prefix:
            roots.append(Path(prefix) / 'share' / PACKAGE_NAME)
    return roots
