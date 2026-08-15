# Local Dashboard

The local dashboard consumes normalized JSONL metrics and shows them in Grafana
through a local Prometheus scrape. It supports the reduced pub/sub matrix and
initial service visibility for request/response latency, CPU, and RSS metrics.

## Prerequisites

- Docker is installed and running.
- Docker Compose plugin is installed.
- Ports `3000`, `9090`, and `9108` are available.
- At least two per-run `normalized_metrics.jsonl` files exist.

## Expected Input

Create normalized metrics from each results directory, then build the dashboard
dataset:

```bash
ros2-performance-monitoring parse <results-dir> --output <results-dir>/normalized_metrics.jsonl
ros2-performance-monitoring dataset build \
  <reference-results>/normalized_metrics.jsonl \
  <candidate-results>/normalized_metrics.jsonl \
  --output dashboard-data.jsonl
```

The normalized records keep benchmark harness provenance separate from client
library and host provenance. Dashboard comparisons can be scoped by client
library, platform, ROS distribution, and whether the client library was built
or installed from packages. Built versions show their client-library commit;
packaged versions are identified as `packaged`.

For compatible repeated measurements, pass `--aggregate median`. Measured runs
remain selectable alongside the generated aggregate run. Selectors label each
choice as measured or as a median with its repeat count while retaining the run
ID as the selected value. Run kind, aggregation method, and repeat count are
exported on `ros2_perf_run_info`; source run IDs and input checksums are recorded
in `dashboard-data.manifest.json`.

Supported service artifacts currently include `cli-srv_single_process` leaves
named `cli_srv_10b`, `cli_srv_100kb`, `cli_srv_1mb`, and `cli_srv_4mb`, and
`cli-srv_multi_process` leaves named `10b`, `100kb`, `1mb`, and `4mb`.

The dashboard does not run benchmarks or parse raw artifacts. It starts from the
normalized JSONL file, and the local exporter evaluates the deterministic
comparison policy described below.

## Start

```bash
ros2-performance-monitoring dashboard up --input dashboard-data.jsonl
```

The command starts Prometheus and Grafana with Docker Compose, then keeps the
Prometheus exporter running in the foreground.

Open:

```text
http://localhost:3000
```

Useful local URLs:

- Grafana: `http://localhost:3000`
- Default regression views:
  `http://localhost:3000/d/ros2-regression-overview/default-views`
- Manual explorer:
  `http://localhost:3000/d/rclcpp-pubsub-overview/manual-explorer`
- Run details:
  `http://localhost:3000/d/ros2-run-detail/run-details`
- Prometheus: `http://localhost:9090`
- Exporter: `http://localhost:9108/metrics`

Grafana uses the default regression views as its home dashboard, so opening
`http://localhost:3000` also lands directly on the project view.

Dashboard auto-refresh defaults to five minutes. The refresh menu only offers
intervals of five minutes or longer because benchmark data changes between
runs, not every few seconds.

## Stop

Press `Ctrl+C` to stop the exporter, then stop the containers:

```bash
ros2-performance-monitoring dashboard down
```

## Exporter Debugging

Run the exporter without Grafana or Prometheus:

```bash
ros2-performance-monitoring serve-prometheus --input dashboard-data.jsonl --port 9108
```

Then inspect:

```text
http://localhost:9108/metrics
```

## Dashboard Workflow

The dashboard is organized around a reference-versus-new-run review with two
modes.

Default views require the client library, platform, reference ROS distribution,
candidate ROS distribution, client source (`build` or `packaged`), workload
(`Pub/Sub` or `Service`), and one run from each selected distribution. The two
distribution selectors may be equal for run-over-run regression testing or
different for a cross-distribution comparison. Every automatic query keeps the
reference and candidate environments separate while matching their scenario
identity. The dashboard scans every matching scenario for the selected workload
through eleven checks:

Run selectors use current Prometheus samples rather than retained label history.
This prevents a run removed from the active JSONL dataset from remaining as a
selectable but empty result until Prometheus retention expires. The candidate
selector also excludes the selected reference run because comparing a run with
itself cannot reveal a regression.

1. Overall, latency, throughput, resource, and reliability status cards.
2. Mean and p95 latency scaling lines over a logarithmic payload axis.
3. Throughput loss and an absolute throughput scaling line.
4. Peak CPU and resident-memory regressions.
5. A compact lost, late, and too-late reliability matrix by payload.
6. Run-over-run mean latency, p95 latency, and throughput regression matrices
   across RMW implementations and payloads.
7. Run-over-run regression separated by IPC-off, IPC-on, and loaned transport.
8. Absolute RMW performance over the common IPC-off matrix.
9. IPC-on effectiveness relative to IPC-off for every RMW that records both
   transports in comparable single-process scenarios.
10. Fast DDS loaned-message effectiveness relative to IPC-off Pub/Sub.
11. A directly comparable p95-latency view for each RMW using the same 1 MiB,
    single-process, IPC-off scenario from the selected workload in both runs.

The comparison works both across ROS distributions and between repeated runs of
the same distribution. The default and manual dashboards use the same five
status cards and vocabulary: `No regression`, `Possible regression`,
`Regression`, `Incomplete results`, `Cannot compare`, and `N/A`.

Before evaluating performance, the policy compares the actual topology,
process mode, payload, RMW, transport, executor, and node-role keys in both
runs. Any missing or newly-added key makes all five statuses `Cannot compare`.
The separate comparison coverage dashboard lists those missing and added
combinations. Supplying the same run on both sides directly in a dashboard URL
also returns `Cannot compare`.

An applicable category is `Incomplete results` when either run lacks one of its
required measurements. Latency requires mean and p95 values, resources require
peak CPU and RSS, and Pub/Sub requires throughput plus lost-, late-, and
too-late-message percentages. Service throughput and reliability are `N/A` and
do not affect its overall status. The overall card reports the highest severity
among applicable categories; incomplete data therefore cannot result in a
passing overall status. If no category applies, the overall status is `Cannot
compare`.

Measured and aggregate runs use this same evaluation path. Reversing the
selected runs also reverses the direction of every change instead of presenting
a percentage as though it were symmetric.

## Comparison Policy

The policy uses the worst applicable measurement in each category. An
improvement in one measurement does not cancel a regression in another.

| Category | Possible regression | Regression |
| --- | ---: | ---: |
| Mean or p95 latency increase | >= 0.5% | >= 2% |
| Throughput decrease | >= 0.5% | >= 2% |
| Peak CPU or RSS increase | >= 1% | >= 5% |
| Reliability increase | >= 0.01 percentage points | >= 0.1 percentage points |

These are deterministic review thresholds, not a statistical-significance or
noise model. Detailed panels retain improvements and individual measurements so
the status can be investigated rather than treated as a statistical conclusion.

Cross-RMW comparisons use IPC-off because it is the common transport represented
for Fast DDS, Cyclone DDS, and Zenoh. IPC-on effectiveness is restricted to
single-process scenarios with both IPC modes, while loaned-message comparisons
are restricted to Fast DDS Pub/Sub. Missing combinations are omitted rather
than treated as zero.
The current Service results do not export throughput or message-reliability
metrics and do not support loaned messages, so those category statuses show
`N/A` and Pub/Sub-specific detail panels show no data instead of mixing in
Pub/Sub results.

The dashboard uses different visual forms for different questions. Headline
regressions remain stat cards, ordered payload scaling uses XY lines, categorical
RMW and transport rankings use horizontal bars, positive/negative percentage
changes use zero-centred axes, and two-dimensional RMW/payload scans use
color-coded tables. Green matrix cells mean no measured regression; orange
cells need review; red cells exceed the configured regression threshold.

A true latency histogram or box-style distribution is not currently possible:
the normalized data exports aggregate percentiles but not raw latency samples or
histogram buckets.

Clicking either run card opens that run's detail page. Run identity, provenance,
scenario counts, and the scenario inventory describe the complete run. The
performance profile has its own workload picker and only aggregates measurements
from the selected `Pub/Sub` or `Service` workload.

Manual mode is for investigating one precise scenario:

1. Select the client library.
2. Select the platform, reference ROS distribution, candidate ROS distribution,
   and whether the client is built or packaged.
3. Select a reference run (the earlier or accepted result) and a new run (the
   commit, branch, or distribution being checked). Each run list is scoped to
   its corresponding ROS distribution.
4. Keep the benchmark topology, process mode, payload, RMW, and transport
   identical using the chained scenario selectors.
5. Read the five workload status cards, then use the selected-scenario deltas to
   investigate the responsible measurements. Green means no regression, orange
   needs review, and red exceeds the regression threshold.
6. Confirm the exact reference and new-run values. Their refs and versions are
   shown directly in the reference and new-run cards. A built version shows its
   commit; a packaged version shows `packaged`.
7. Review lost, late, and too-late message percentages in separate reliability
   comparisons.
8. Use the payload-scaling charts to check whether latency and throughput
   changes remain consistent from 10 B through 4 MiB.
9. Use the regression scan to find payload-specific mean latency, p95 latency,
   or throughput regressions. Positive values mean the new run is worse.
10. Inspect the p50/p95/p99 profile and peak CPU/RSS regression charts for
   tail-latency and resource regressions hidden by headline averages.

The mode control at the top moves between the two dashboards. Grafana dashboard
variables cannot be conditionally disabled, so the curated view omits the
advanced scenario controls while manual mode exposes them. This avoids controls
that look disabled but can still affect a comparison.

The reference and new-run cards are links to a run-details dashboard. That page
shows the selected run's client ref and version, platform, ROS distribution,
client source, executor, timestamp, benchmark ref and commit, scenario
inventory, latency percentiles, throughput, resource use, and reliability
profile. The inventory includes the executor and node role for each recorded
configuration. Host OS, Python version, benchmark URL, and run duration remain
in `metadata_<run-id>.json`; they are not yet exported as Prometheus labels.

The default scenario is Pub/Sub, single process, 10 B, Fast DDS, with IPC
enabled. Service latency and resource results use the same workflow when the
benchmark selector is changed to Service. Throughput and message reliability
are Pub/Sub-only signals.

## Deferred Matrix Items

The dashboard intentionally does not cover long-running actions, multiple-client
service sweeps, a full Zenoh matrix, remote-host tests, executor sweeps, or a
CI-gating regression policy.

## Troubleshooting

Port conflicts: stop the process using `3000`, `9090`, or `9108`, then run the
dashboard command again.

Docker not running: start Docker and check `docker compose version`.

Empty dashboard: confirm the exporter is reachable at
`http://localhost:9108/metrics` and Prometheus has the
`host.docker.internal:9108` target up.

Exporter not reachable: confirm `dashboard up` is still running in the terminal
and the input JSONL path exists.
