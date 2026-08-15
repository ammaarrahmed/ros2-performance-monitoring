# Architecture

This repository is planned as a local-first visibility layer for ROS 2
performance results.

## Boundary

Benchmark execution remains outside this repository.

The parser reads artifacts produced by external tools such as `ros2-performance`
or `ros2-benchmark-container`, normalizes them into a stable internal
representation, and exposes them to dashboard tooling.

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

The design uses a small adapter boundary:

```text
container runner
  -> raw artifacts
  -> normalized JSONL
  -> validated comparison dataset
  -> Prometheus exporter and comparison policy
  -> Prometheus
  -> Grafana
```

Examples of future artifact sources:

- `ros2-benchmark-container` result directories.
- Direct `ros2-performance` output directories.
- Synthetic fixture directories used by tests.

Examples of future output sinks:

- JSONL files for local inspection and regression artifacts.
- Prometheus-compatible metrics for Grafana.

The dataset builder is the trust boundary between per-run normalized artifacts
and dashboard input. It validates schemas, run provenance, benchmark layout,
and unique metric identities, then writes deterministic JSONL and a checksum
manifest. Optional median runs retain only low-cardinality aggregation metadata
in metric rows; source run IDs and checksums remain in the manifest.

The current dashboard path starts from a normalized JSONL dataset. It does not
run benchmarks or parse raw artifacts as part of dashboard startup. The local
exporter derives deterministic per-category comparison statuses before exposing
the dataset to Prometheus; statistical analysis remains outside this boundary.
