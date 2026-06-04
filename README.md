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

Place this package inside a ROS 2 workspace and run:

```bash
colcon test --packages-select ros2_performance_monitoring
colcon test-result --verbose
```

At scaffold stage, these checks only validate package shape and lint rules.
## MILESTONE 1 : Automated Benchmark Container & Minimal Pub/Sub with rclcpp artifact parsing and generation
- [x] Part A : Add CLI Options such as run and doctor for ros2-performance-monitoring CLI tool
## License

New code in this repository is licensed under the Apache License, Version 2.0.

Optional external benchmark tools referenced by this project may use different
open source licenses. See `THIRD_PARTY_NOTICES.md`.

