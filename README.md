# ROS 2 Performance Monitoring

Local-first dashboard and exporter tooling for ROS 2 performance visibility.

This repository is being prepared as part of the GSoC 2026 client library system
performance monitoring work. Its first goal is to make existing benchmark output
easy for ROS 2 developers to inspect through normalized artifacts and a local
Grafana dashboard.

## Current Scope

This initial repository scaffold does not implement benchmark parsing, metric
export, or dashboards yet. It establishes the project structure, license posture,
and contribution expectations before functional pull requests begin.

The first functional pull request is expected to target:

- `rclcpp` pub/sub benchmark artifacts.
- One fixed payload size.
- Local result parsing.
- Normalized artifacts suitable for Prometheus and Grafana.

## Planned Data Flow

The intended MVP data flow is:

```text
external benchmark run
  -> raw benchmark result artifacts
  -> normalized exporter artifacts
  -> Prometheus-compatible metrics
  -> local Grafana dashboard
```

## Relationship To Benchmark Repositories

This repository does not vendor benchmark engines.

- `ros2-performance` is treated as an external ROS 2 benchmark framework.
- `ros2-benchmark-container` is treated as an external benchmark runner and
  artifact producer.
- Neither repository is included here as a git submodule.
- No iRobot benchmark source code or result files are copied into this scaffold.

The exporter will consume result artifacts produced by external tools instead of
owning benchmark execution itself. That keeps this repository focused on
developer-facing visibility: parsing, normalization, export, and dashboards.

## Non-Goals For The Scaffold

This initial commit intentionally does not include:

- Parser implementation.
- Prometheus exporter implementation.
- Grafana dashboard JSON.
- Docker Compose or hosted infrastructure.
- Benchmark runner wrappers.
- Copied benchmark outputs.

## Local Development

This package can be installed either as a regular Python package with `pip` or
as a ROS 2 package with `colcon`. Use one workflow at a time.

### Python virtual environment

From a clean checkout, create a virtual environment, install the development
dependencies, and install the package:

```bash
source /opt/ros/lyrical/setup.bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install .
```

Run the CLI:

```bash
ros2-performance-monitoring run
ros2-performance-monitoring doctor
```

Run the Python tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest
```

The `ament-copyright`, `ament-flake8`, and `ament-pep257` test helpers are
provided by the sourced ROS 2 installation.

### ROS 2 workspace

Place this package inside a ROS 2 workspace, build it with `colcon`, and source
the workspace:

```bash
source /opt/ros/lyrical/setup.bash
mkdir -p ~/ros2_performance_ws/src
cd ~/ros2_performance_ws/src
git clone https://github.com/ammaarrahmed/ros2-performance-monitoring.git
cd ..
colcon build --packages-select ros2_performance_monitoring
source install/setup.bash
```

Run the CLI through ROS 2:

```bash
ros2 run ros2_performance_monitoring ros2-performance-monitoring run
ros2 run ros2_performance_monitoring ros2-performance-monitoring doctor
```

Run the ROS 2 package tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test --packages-select ros2_performance_monitoring --python-testing pytest
colcon test-result --verbose
```

At scaffold stage, these checks only validate package shape and lint rules.
## MILESTONE 1 : Automated Benchmark Container & Minimal Pub/Sub with rclcpp artifact parsing and generation
- [x] Part A : Add CLI Options such as run and doctor for ros2-performance-monitoring CLI tool
## License

New code in this repository is licensed under the Apache License, Version 2.0.

Optional external benchmark tools referenced by this project may use different
open source licenses. See `THIRD_PARTY_NOTICES.md`.
