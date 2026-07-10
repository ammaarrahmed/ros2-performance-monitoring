# ROS 2 Performance Monitoring

This repository turns local ROS 2 benchmark output into something you can inspect
quickly: normalized JSONL, Prometheus metrics, and a Grafana dashboard running on
your machine.

The current path is intentionally small and local-first. It targets the
`rclcpp` pub/sub benchmark artifacts produced by the benchmark container fork,
then makes those results visible in Grafana.

## What This Does

```text
benchmark container run
  -> raw benchmark artifacts
  -> normalized_metrics.jsonl
  -> local Prometheus exporter
  -> Prometheus
  -> Grafana
```

It does not detect regressions yet, and it does not run hosted infrastructure.
The dashboard is organized to make performance changes visible across ROS client
library refs, ROS distros, RMW implementations, communication modes, and payload
sizes.

## Prerequisites

- Docker is installed and running.
- Docker Compose plugin is installed.
- Ports `3000`, `9090`, and `9108` are free.
- The `ros2-performance-monitoring` command is available on your `PATH`.

For local source development, one simple setup is:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Full Local Workflow

Run these commands from the repository root.

### 1. Run The Benchmark

This fetches the benchmark container repo if needed, builds the Docker image,
runs the minimal `rclcpp` pub/sub benchmark, and writes raw artifacts under
`./results`.

```bash
ros2-performance-monitoring run
```

The default run uses:

```text
duration: 60 seconds
ros_distro: lyrical
suite: pubsub-rclcpp-minimal
results_dir: ./results
cache_dir: ~/.cache/ros2-performance-monitoring
```

To make those values explicit:

```bash
ros2-performance-monitoring run 60 lyrical single-threaded ./results ~/.cache/ros2-performance-monitoring
```

If you are comparing a specific client-library branch or commit, record it with
the run:

```bash
ros2-performance-monitoring run \
  --client-library rclcpp \
  --client-library-ref <rclcpp-branch-or-ref> \
  --client-library-commit <rclcpp-commit-sha>
```

The benchmark container ref and the client-library ref are tracked separately.
The default Docker flow uses ROS distro packages, so the client-library commit is
`unknown` unless you pass it explicitly.

The current runner writes benchmark artifacts under paths like:

```text
results/benchmark/lyrical/pub-sub_single_process/...
```

### 2. Normalize The Artifacts

Convert the raw benchmark files into the normalized JSONL format consumed by the
exporter and dashboard:

```bash
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
```

You should see output similar to:

```text
Wrote 840 normalized metrics to ./results/normalized_metrics.jsonl
```

The normalized records include separate benchmark harness and client-library
provenance. In Grafana, use `client_library_ref`, `client_library_commit`, and
`ros_distro` to compare performance across branches, commits, and distributions.

### 3. Check The Exporter Directly

This step is optional, but useful when you want to verify the metrics before
starting Grafana:

```bash
ros2-performance-monitoring serve-prometheus --input ./results/normalized_metrics.jsonl --port 9108
```

Then open:

```text
http://localhost:9108/metrics
```

Stop the exporter with `Ctrl+C`.

### 4. Start Grafana And Prometheus

Start the local dashboard stack:

```bash
ros2-performance-monitoring dashboard up --input ./results/normalized_metrics.jsonl
```

This starts Prometheus and Grafana with Docker Compose, then keeps the metrics
exporter running in the foreground. Keep this terminal open while using the
dashboard.

Open Grafana:

```text
http://localhost:3000
```

The dashboard is provisioned automatically. Look for:

```text
ROS 2 Pub/Sub Client Library Performance Comparison
```

### 5. Stop The Dashboard

Press `Ctrl+C` in the terminal running `dashboard up`, then stop the containers:

```bash
ros2-performance-monitoring dashboard down
```

## Useful Commands

Build only the benchmark container:

```bash
ros2-performance-monitoring build-container
```

Serve Prometheus metrics without starting Grafana:

```bash
ros2-performance-monitoring serve-prometheus --input ./results/normalized_metrics.jsonl --port 9108
```

Parse into a run directory instead of the top-level results directory:

```bash
ros2-performance-monitoring parse ./results --output ./results/benchmark/lyrical/pub-sub_single_process/normalized_metrics.jsonl
```

Run the dashboard from that file:

```bash
ros2-performance-monitoring dashboard up --input ./results/benchmark/lyrical/pub-sub_single_process/normalized_metrics.jsonl
```

The local dashboard stack is defined by:

- `compose.dashboard.yml` for the Prometheus and Grafana containers.
- `config/prometheus/prometheus.yml` for the Prometheus scrape target.
- `config/grafana/provisioning/` for automatic Grafana datasource and
  dashboard provisioning.
- `config/grafana/dashboards/rclcpp_pubsub_overview.json` for the current
  pub/sub dashboard.

## Troubleshooting

If parsing fails with `PermissionError`, the raw artifact directory was probably
created by Docker as `root`. New benchmark runs hand ownership back to your host
user when the run finishes. For older results, fix ownership once:

```bash
sudo chown -R "$USER:$USER" ./results/benchmark
```

If Grafana is empty, check the exporter first:

```text
http://localhost:9108/metrics
```

Then check Prometheus targets:

```text
http://localhost:9090/targets
```

If a port is already in use, stop the process using `3000`, `9090`, or `9108`
before starting the dashboard.

If Docker Compose fails, check:

```bash
docker compose version
docker info
```

## Repository Boundary

This repository does not vendor the benchmark engines.

- `ros2-performance` is treated as an external ROS 2 benchmark framework.
- `ros2-benchmark-container` is treated as an external benchmark runner and
  artifact producer.
- No iRobot benchmark source code or result files are copied into this project.

The goal here is the local visibility layer: artifact parsing, normalization,
export, and dashboards.

## Development Checks

This package can be installed either as a regular Python package with `pip` or
as a ROS 2 package with `colcon`. Use one workflow at a time.

### Python virtual environment

From a clean checkout, create a virtual environment, install the development
dependencies, and install the package:

```bash
source /opt/ros/lyrical/setup.bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install .
```

Run the CLI:

```bash
ros2-performance-monitoring run
ros2-performance-monitoring doctor
ros2-performance-monitoring build-container
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
ros2-performance-monitoring serve-prometheus --input ./results/normalized_metrics.jsonl
ros2-performance-monitoring dashboard up --input ./results/normalized_metrics.jsonl
```

Run the Python tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest
```

The `ament-copyright`, `ament-flake8`, and `ament-pep257` test helpers are
provided by the sourced ROS 2 installation.

### ROS 2 workspace

Place this package inside a ROS 2 workspace, build it with `colcon`, and source
the workspace:

```bash
source /opt/ros/lyrical/setup.bash
mkdir -p ~/ros2_performance_ws/src
cd ~/ros2_performance_ws/src
git clone https://github.com/ammaarrahmed/ros2-performance-monitoring.git
cd ..
colcon build --packages-select ros2_performance_monitoring
source install/setup.bash
```

Run the CLI through ROS 2:

```bash
ros2 run ros2_performance_monitoring ros2-performance-monitoring run
ros2 run ros2_performance_monitoring ros2-performance-monitoring doctor
ros2 run ros2_performance_monitoring ros2-performance-monitoring build-container
ros2 run ros2_performance_monitoring ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
ros2 run ros2_performance_monitoring ros2-performance-monitoring dashboard up --input ./results/normalized_metrics.jsonl
```

### Benchmark container build

The `build-container` command builds the external benchmark container. It
requires Docker and Docker Buildx to be installed and available on `PATH` in the
same shell that runs the command:

```bash
docker version
docker buildx version
```

It also uses `vcstool` to fetch the external benchmark container repository.
For pip installs, this is installed as a Python package dependency. For ROS 2
workspace installs, `rosdep` installs it from the `python3-vcstool` package.
The Docker image build pulls and exports a large ROS 2 base image, so make sure
Docker has several GB of free disk space available.

The Docker build scripts are not stored in this repository. The command fetches
or updates the external `ros2-benchmark-container` checkout in the cache
directory before starting Docker. By default that cache directory is:

```bash
~/.cache/ros2-performance-monitoring
```

On a fresh machine, `build-container` can be run directly:

```bash
ros2-performance-monitoring build-container
```

With a ROS 2 workspace build, use the equivalent `ros2 run` commands:

```bash
ros2 run ros2_performance_monitoring ros2-performance-monitoring build-container
```

If Docker is not installed, Docker is not available on `PATH`, or the current
user cannot access the Docker daemon, the command exits with an error instead of
printing a successful build message.

If `build-container` is not listed as an available command, rebuild or reinstall
this package in the active environment. That usually means the shell is still
finding an older installed `ros2-performance-monitoring` executable.

### Minimal benchmark run

The `run` command executes the current MVP benchmark path:

1. Fetch or update the external `ros2-benchmark-container` checkout.
2. Build the benchmark container image for the selected ROS distro.
3. Start the container and run the `pubsub-rclcpp-minimal` suite.
4. Write raw benchmark outputs under the results directory.

The default run uses ROS `lyrical`, a 60 second duration, the
`pubsub-rclcpp-minimal` suite, `./results` for outputs, and
`~/.cache/ros2-performance-monitoring` for the external container checkout:

```bash
ros2-performance-monitoring run
```

The positional arguments are:

```bash
ros2-performance-monitoring run \
  <duration> \
  <ros-distro> \
  <executor> \
  <results-dir> \
  <cache-dir> \
  <container-repo-url> \
  <container-ref>
```

The only supported suite in this branch is:

```bash
ros2-performance-monitoring run --suite pubsub-rclcpp-minimal
```

The benchmark runner requires Docker with the Buildx plugin and a running
Docker daemon. The current user must be able to run Docker commands without
`sudo`. The runner starts a privileged container and mounts
`/var/run/docker.sock` into it.

### Parse benchmark artifacts

The `parse` command reads raw benchmark outputs from a results directory and
writes normalized JSONL metrics:

```bash
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
```

The parser targets the reduced `ros2-benchmark-container` pub/sub and service
matrix. It
looks under the results directory for a benchmark artifact root named
`benchmark`, then discovers single-process and multi-process pub/sub leaves plus
initial client/service leaves for `10b`, `100kb`, `1mb`, and `4mb` payloads,
including Fast DDS and Cyclone DDS result directories where present. Each
discovered leaf must include these files:

```text
metadata.txt
resources.txt
latency_all.txt
latency_total.txt
```

Each JSONL record keeps the dimensions needed for local analysis:

- ROS distro.
- RMW implementation normalized to ROS identifiers such as `rmw_fastrtps_cpp`
  and `rmw_cyclonedds_cpp`.
- executor.
- topology as `pub-sub` or `service`.
- process mode as `single_process` or `multi_process`.
- communication mode as `ipc_on`, `ipc_off`, or `loaned`.
- payload size in bytes, such as `10` for `10b`, `102400` for `100kb`,
  `1048576` for `1mb`, and `4194304` for `4mb`.
- frequency as numeric Hz for pub/sub records, or `0.0` for service records.
- metric name, value, unit, and aggregation.
- source artifact file.

If required artifact files are missing or the directory layout is unsupported,
the command exits with a clear error instead of silently producing partial
metrics.

Run the ROS 2 package tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test --packages-select ros2_performance_monitoring --python-testing pytest
colcon test-result --verbose
```

## License

New code in this repository is licensed under the Apache License, Version 2.0.

Optional external benchmark tools referenced by this project may use different
open source licenses. See `THIRD_PARTY_NOTICES.md`.
