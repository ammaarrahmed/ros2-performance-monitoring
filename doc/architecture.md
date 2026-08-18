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
- Repeat-aware local statistical comparison reports.

It avoids taking ownership of:

- Ownership or vendoring of benchmark topology implementations.
- Hosted statistical analysis or CI-gating policy.
- Long-running hosted infrastructure.

## Bridge Shape

The design uses a small adapter boundary:

```text
resolved benchmark + rclcpp target
  -> labelled image + in-image target manifest
  -> verified container runner
  -> staged trial (raw artifacts + metadata + normalized JSONL)
  -> checksum-verified trial completion
  -> validated comparison dataset + experiment completion
     -> paired measured-trial bootstrap -> comparison-report.json
     -> report validation + Prometheus mapping -> Prometheus -> Grafana
     -> legacy threshold-only comparison -> Prometheus -> Grafana
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

The statistical comparison boundary reads a controlled experiment rather than
its aggregate dataset. It verifies the experiment and trial completion graphs,
loads only measured records, reconstructs pairs from balanced schedule blocks,
and rejects incompatible provenance or coverage. A deterministic paired
bootstrap resamples whole blocks and retains the complete scenario scan in each
resample. Category decisions use the worst scenario, while overall evidence
uses the worst category-normalized scenario. The resulting versioned JSON report
binds completed evidence to the exact dashboard dataset SHA-256 while remaining
separate from Prometheus and Grafana formatting.

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

The dashboard path starts from a normalized JSONL dataset. It does not run
benchmarks or parse raw artifacts as part of dashboard startup. When a report is
supplied, the exporter validates its schema, experiment and dataset binding,
target provenance, method, and scenario coverage, then maps its overall,
category, and per-scenario evidence directly to low-cardinality metrics. It does
not recalculate statistical evidence, and it emits status only for the
report-defined aggregate pair. Without a report, the exporter retains the
ordered-pair deterministic policy and marks those statuses as threshold-only.
