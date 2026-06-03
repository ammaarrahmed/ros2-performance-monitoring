# Architecture

This repository is planned as a local-first visibility layer for ROS 2
performance results.

## Boundary

Benchmark execution remains outside this repository.

The exporter should read artifacts produced by external tools such as
`ros2-performance` or `ros2-benchmark-container`, normalize them into a stable
internal representation, and expose them to dashboard tooling.

This keeps the project focused on:

- Artifact parsing.
- Metric normalization.
- Developer-friendly export formats.
- Local Grafana dashboards.

It avoids taking ownership of:

- Benchmark topology execution.
- RMW test matrix orchestration.
- Docker image construction.
- Long-running hosted infrastructure.

## Planned Bridge Shape

The planned design uses a small adapter boundary:

```text
artifact source -> normalized metric model -> output sink
```

Examples of future artifact sources:

- `ros2-benchmark-container` result directories.
- Direct `ros2-performance` output directories.
- Synthetic fixture directories used by tests.

Examples of future output sinks:

- JSONL files for local inspection and regression artifacts.
- Prometheus-compatible metrics for Grafana.

The first implementation pull request should keep this boundary narrow and
target only the minimum `rclcpp` pub/sub artifacts needed for an end-to-end
dashboard demonstration.

