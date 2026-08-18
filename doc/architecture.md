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
- Immutable experiment plans and repeated-trial bundle orchestration.

It avoids taking ownership of:

- Ownership or vendoring of benchmark topology implementations.
- Statistical verdicts beyond deterministic median aggregation.
- Long-running hosted infrastructure.

## Bridge Shape

The design uses a small adapter boundary:

```text
resolved benchmark + rclcpp target
  -> labelled image + in-image target manifest
  -> verified container runner
  -> staged trial (raw artifacts + metadata + normalized JSONL)
  -> checksum-verified trial completion
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

The experiment layer compares exactly two labelled targets. Its immutable plan
records both complete target identities, shared run configuration, warm-up and
measured counts, scheduling policy, seed, and exact planned order. Balanced
schedules alternate which target runs first within each pair; interleaved
schedules deterministically shuffle each pair. Trial IDs include the target
role, trial kind, sequence, and target-key prefix.

Each trial runs in an attempt-specific staging directory. Metadata, raw
artifacts, and normalized JSONL are validated and checksummed before the attempt
directory is renamed and the trial completion marker is published. Failed and
interrupted attempts are retained without a completion marker. Resume only
reuses trials whose complete file graph still matches those checksums.

Host architecture, CPU model, kernel, Docker version, CPU set, and CPU governor
state are captured for every trial. The first measured trial establishes the
measured-environment identity; subsequent measured trials must match it before
they start. Target image ID and digest, benchmark commit, executor, duration,
suite, and ROS distribution remain recorded with each trial as evidence.

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
manifest. The old manifest is removed before replacement, the dataset is
published and synced first, and a manifest containing the dataset checksum is
published last as the bundle completion marker. Optional median runs retain
only low-cardinality aggregation metadata in metric rows; source run IDs and
checksums remain in the manifest.

The current dashboard path starts from a normalized JSONL dataset. It does not
run benchmarks or parse raw artifacts as part of dashboard startup. The local
exporter derives deterministic per-category comparison statuses before exposing
the dataset to Prometheus; statistical analysis remains outside this boundary.
