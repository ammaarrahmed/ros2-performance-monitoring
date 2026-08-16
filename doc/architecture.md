# Architecture

This repository is a local-first execution and visibility layer for ROS 2
performance results.

## Boundary

Benchmark implementations remain external. This repository resolves their
source inputs, constructs and verifies local derived images, invokes the
external runner, and normalizes its artifacts into a stable internal
representation for dashboard tooling.

This keeps the project focused on:

- Artifact parsing.
- Metric normalization.
- Developer-friendly export formats.
- Local Grafana dashboards.
- Exact local rclcpp target resolution and provenance verification.
- Content-addressed local benchmark images and containers.

It avoids taking ownership of:

- Ownership or vendoring of benchmark topology implementations.
- General experiment and repeated-trial orchestration.
- Long-running hosted infrastructure.

## Bridge Shape

The design uses a small adapter boundary:

```text
resolved benchmark + rclcpp target
  -> labelled image + in-image target manifest
  -> verified container runner
  -> raw artifacts
  -> normalized JSONL
  -> validated comparison dataset
  -> Prometheus exporter and comparison policy
  -> Prometheus
  -> Grafana
```

The target key is a SHA-256 digest over the ROS distribution, architecture,
benchmark repository commit, client-library source and commit, and relevant
build configuration. The same key determines image and retained-container
identity. A matching name is not sufficient for reuse: labels, the manifest,
image ID, active rclcpp package prefix, and the benchmark executable's dynamic
library resolution are checked before execution.

Source-built rclcpp targets are resolved through a managed Git mirror and
immutable worktree. The derived image builds that checkout as an overlay, then
rebuilds the benchmark workspace against it. Packaged targets retain the ROS
installation underlay and are labelled explicitly as packaged. Run metadata is
created from the verified target rather than from user-provided commit claims.

Artifact sources include:

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
