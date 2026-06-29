# Local Dashboard

The local dashboard consumes normalized JSONL metrics and shows them in Grafana
through a local Prometheus scrape.

## Prerequisites

- Docker is installed and running.
- Docker Compose plugin is installed.
- Ports `3000`, `9090`, and `9108` are available.
- `normalized_metrics.jsonl` exists.

## Expected Input

Create normalized metrics from a results directory:

```bash
ros2-performance-monitoring parse <results-dir> --output <results-dir>/normalized_metrics.jsonl
```

The normalized records keep benchmark harness provenance separate from client
library provenance. Use `client_library_ref`, `client_library_commit`, and
`ros_distro` to compare performance across client-library branches, commits, and
distributions.

The dashboard does not run benchmarks, parse raw artifacts, or detect
regressions. It starts from the normalized JSONL file.

## Start

```bash
ros2-performance-monitoring dashboard up --input <results-dir>/normalized_metrics.jsonl
```

The command starts Prometheus and Grafana with Docker Compose, then keeps the
Prometheus exporter running in the foreground.

Open:

```text
http://localhost:3000
```

Useful local URLs:

- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Exporter: `http://localhost:9108/metrics`

## Stop

Press `Ctrl+C` to stop the exporter, then stop the containers:

```bash
ros2-performance-monitoring dashboard down
```

## Exporter Debugging

Run the exporter without Grafana or Prometheus:

```bash
ros2-performance-monitoring serve-prometheus --input <results-dir>/normalized_metrics.jsonl --port 9108
```

Then inspect:

```text
http://localhost:9108/metrics
```

## Dashboard Panels

- Selected configs counts the run/RMW/payload configurations currently selected.
- Mean latency, p95 latency, and mean throughput provide quick headline values.
- Mean latency comparison groups results by client-library ref, client-library
  commit, ROS distro, run, RMW, communication mode, and payload size.
- p95 latency comparison highlights tail-latency changes that often matter for
  regressions.
- Throughput, CPU max, and RSS max comparisons make resource tradeoffs visible.
- Reliability comparison shows late, too-late, and lost message percentages.
- Latency summary table keeps exact mean/min/max/p95/p99 values available for
  review.
- Run matrix lists the selected normalized run metadata.

## Troubleshooting

Port conflicts: stop the process using `3000`, `9090`, or `9108`, then run the
dashboard command again.

Docker not running: start Docker and check `docker compose version`.

Empty dashboard: confirm the exporter is reachable at
`http://localhost:9108/metrics` and Prometheus has the
`host.docker.internal:9108` target up.

Exporter not reachable: confirm `dashboard up` is still running in the terminal
and the input JSONL path exists.
