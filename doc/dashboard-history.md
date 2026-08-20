# Bounded Dashboard History

The exporter can serve an ordered window of verified comparison evidence from
an explicit version 1 active-history index. The index is deployment state: it
selects active evidence, but it does not download, discover, retain, or delete
artifacts.

## Active Index Contract

Paths are relative to the directory containing the index. Bundle order is
oldest first and is preserved in the exported `history_position` label. The
declared limit must be between 1 and 100, and the active list cannot exceed it.

```json
{
  "schema_version": 1,
  "order": "oldest-first",
  "history_limit": 3,
  "bundles": [
    {
      "bundle_id": "rolling-2026-08-18",
      "path": "bundles/rolling-2026-08-18",
      "checksums_sha256": "<sha256-of-bundle-SHA256SUMS>",
      "evidence": "statistical-report",
      "profile": {
        "name": "rolling-workflow-smoke-v1",
        "authoritative": false,
        "notice": "Pipeline smoke evidence only; this profile is not calibrated for authoritative performance claims."
      }
    }
  ]
}
```

`bundle_id` values and paths must be unique. Absolute paths, parent traversal,
symlinked files, malformed checksums, duplicate checksum entries, files missing
from `SHA256SUMS`, and files not listed in `SHA256SUMS` are rejected. The index
also pins the SHA-256 of each bundle's `SHA256SUMS`, so rewriting both a payload
and the bundle checksum list does not satisfy the active deployment contract.

## Bundle Forms

`statistical-report` entries consume the compact dashboard bundle produced by
the scheduled comparison workflow. Each bundle must include the complete
version 1 producer contract from issue #66, including:

```text
bundle/
  plan.json
  targets/reference.json
  targets/candidate.json
  dataset/dashboard-data.jsonl
  dataset/dashboard-data.manifest.json
  experiment.complete.json
  comparison-report.json
  comparison.complete.json
  producer-manifest.json
  SHA256SUMS
```

The indexed profile must match `producer-manifest.json`. Startup revalidates
the compact bundle, dataset manifest, normalized records, report schema,
dataset SHA-256 binding, experiment identity, exact reference and candidate
commits, selected runs, topology coverage, outcome, and producer run IDs.
Every report is checked against the dataset inside the same bundle.

`threshold-only` entries support older datasets that have no statistical
report. They require `dataset/dashboard-data.jsonl`, its version 2
`dashboard-data.manifest.json`, and a `SHA256SUMS` covering every file in the
bundle. They must not contain `comparison-report.json` or
`producer-manifest.json`. Their comparisons are exported with evidence
`threshold-only` and method `threshold-only-v1`; they never appear as
report-backed evidence.

Run IDs must be unique across the active window. The exporter also rejects any
duplicate Prometheus family-and-label identity. It deliberately does not
deduplicate identical bundles or runs, because no cross-bundle identity is
stronger than rejecting an ambiguous active history.

## Serving A History

Start the host exporter directly:

```bash
ros2-performance-monitoring serve-prometheus \
  --history-index ./deployment/active-history.json \
  --port 9108
```

Or start Grafana and Prometheus with the same validated history:

```bash
ros2-performance-monitoring dashboard up \
  --history-index ./deployment/active-history.json
```

For the container-first stack, mount the directory containing the index and
all referenced bundle directories, then provide its container path:

```bash
export ROS2_PERFORMANCE_DASHBOARD_DATA_DIR="$(pwd)/deployment"
export ROS2_PERFORMANCE_HISTORY_INDEX_PATH=/data/active-history.json
unset ROS2_PERFORMANCE_REPORT_PATH
./scripts/container-workflow dashboard
```

The exporter validates and renders the whole active history before it binds the
HTTP server. Any invalid entry, run collision, or Prometheus series collision
fails startup; no partial history is served. Successful startup caches the
rendered payload, so `/metrics` scrapes do not reread datasets, reports,
manifests, or checksum files.

Every indexed sample carries `bundle_id`, `history_position`, `profile`,
`authoritative`, `evidence`, `comparison_id`, `reference_sha`, and
`candidate_sha`. `ros2_perf_bundle_info` also carries `profile_notice`. The
dashboard's history selector scopes raw and report-derived queries to one
bundle, and its run selectors derive reference and candidate choices from the
comparison analysis series. The home dashboard lists the selected profile,
authority flag, and notice explicitly.

The original `--input` and optional `--comparison-report` commands remain the
single-dataset interface. They also render once at startup. Updating either
file requires restarting the exporter.
