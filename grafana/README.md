# Grafana

The local dashboard stack provisions Grafana automatically from files under
`config/grafana`.

Run:

```bash
ros2-performance-monitoring dashboard up --input <results-dir>/normalized_metrics.jsonl
```

Then open:

```text
http://localhost:3000
```

The stack creates a Prometheus datasource pointing at `http://prometheus:9090`
and loads the `ROS 2 Pub/Sub Client Library Performance Comparison` dashboard from
`config/grafana/dashboards/rclcpp_pubsub_overview.json`.

Stop the containers with:

```bash
ros2-performance-monitoring dashboard down
```
