# Grafana

The local dashboard stack provisions Grafana automatically from files under
`config/grafana`.

Run:

```bash
ros2-performance-monitoring dataset build \
  <reference-results>/normalized_metrics.jsonl \
  <candidate-results>/normalized_metrics.jsonl \
  --output dashboard-data.jsonl
ros2-performance-monitoring dashboard up --input dashboard-data.jsonl
```

Then open:

```text
http://localhost:3000
```

The stack creates a Prometheus datasource pointing at `http://prometheus:9090`
and opens `ROS 2 Performance · Default Regression Views` as the home dashboard.
Its overall, latency, throughput, resource, and reliability status cards are
also available in `ROS 2 Performance · Manual Explorer`. Measured and median
aggregate runs are labeled separately in the selectors.

The status policy and missing-data behavior are documented in
[`doc/dashboard.md`](../doc/dashboard.md#comparison-policy).

Stop the containers with:

```bash
ros2-performance-monitoring dashboard down
```
