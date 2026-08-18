# ROS 2 Performance Monitoring

This repository turns local ROS 2 benchmark output into something you can inspect
quickly: normalized JSONL, Prometheus metrics, and a Grafana dashboard running on
your machine.

The current path is intentionally small and local-first. It runs reduced
`rclcpp` pub/sub and service benchmark suites, normalizes the resulting
artifacts, and makes those results visible in Grafana.

## What This Does

```text
benchmark container run
  -> raw benchmark artifacts
  -> normalized_metrics.jsonl
  -> dataset build
  -> dashboard-data.jsonl
  -> local Prometheus exporter
  -> Prometheus
  -> Grafana
```

The dashboard compares runs and reports separate latency, throughput, resource,
reliability, and overall statuses. Missing required results and non-applicable
service metrics are explicit rather than treated as passing measurements. The
project does not enforce a CI-gating regression policy or run hosted
infrastructure. Comparisons can be scoped by ROS client library ref, ROS distro,
RMW implementation, communication mode, and payload size.

## Prerequisites

- Git and an internet connection are available so the external benchmark
  repository and container images can be fetched.
- Docker is installed and running, and the current user can use it without
  `sudo`.
- Docker Compose and Docker Buildx plugins are installed.
- Docker has several GB of free disk space for the ROS 2 benchmark image.
- Ports `3000`, `9090`, and `9108` are free.

From the repository root, create a virtual environment and install the command:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Check the external tools before continuing:

```bash
docker version
docker compose version
docker buildx version
git --version
```

## Full Local Workflow

Run these commands from the repository root.

### 1. Start With A Short Benchmark

The first run fetches the external benchmark repository and builds a large ROS 2
container image. Even a one-second benchmark can therefore take several minutes
on a fresh machine. Start with the smaller service suite to check the complete
workflow before committing to the default matrix:

```bash
ros2-performance-monitoring run \
  --suite service-rclcpp-minimal \
  -t 1 \
  ./results
```

Here, the duration is applied to every scenario rather than to the command as a
whole. The one-second service suite still runs multiple payload, process, RMW,
and communication-mode combinations.

The runner starts a privileged container, mounts `/var/run/docker.sock`, and
temporarily changes the host CPU governor to `performance`. The external runner
sets the governor to `powersave` when it finishes; it does not restore a
different original governor. Run it only on a machine where those host-level
changes are acceptable.

### 2. Run The Full Benchmark

This fetches the benchmark container repo if needed, builds the Docker image,
runs the reduced `rclcpp` pub/sub and service benchmarks, and writes raw
artifacts under `./results` before normalizing them to
`./results/normalized_metrics.jsonl`.

```bash
ros2-performance-monitoring run
```

The default run uses:

```text
duration: 60 seconds
ros_distro: lyrical
suite: rclcpp-minimal
results_dir: ./results
cache_dir: ~/.cache/ros2-performance-monitoring
```

The default suite contains roughly 72 scenario combinations. Because the
60-second duration applies to each one, allow well over an hour for the
benchmark itself, plus the initial image build.

To make those values explicit:

```bash
ros2-performance-monitoring run \
  -t 60 \
  -d lyrical \
  -x EventsCBGExecutor \
  --suite rclcpp-minimal \
  ./results
```

To benchmark an exact rclcpp branch, tag, or commit, request a source build:

```bash
ros2-performance-monitoring run \
  --client-library rclcpp \
  --client-library-source build \
  --client-library-ref <rclcpp-branch-tag-or-commit>
```

Source overlay builds compile without test targets and use two parallel workers
to keep memory use predictable on developer machines.

The default source repository is `https://github.com/ros2/rclcpp.git`. Use an
explicit fork when needed:

```bash
ros2-performance-monitoring run \
  --client-library-source build \
  --client-library-repo-url https://github.com/<owner>/rclcpp.git \
  --client-library-ref <branch-tag-or-commit>
```

The command fetches the repository into a managed Git cache, resolves the ref to
one full commit SHA, and creates an immutable checkout before Docker starts. It
then builds rclcpp into `/target_ws/install`, rebuilds the benchmark workspace
against that overlay, and verifies the active package prefix and linked
`librclcpp` before writing run metadata or starting a benchmark. There is no
option to supply a claimed commit separately from the resolved source.

Without `--client-library-source build`, the normal ROS package installation is
used and recorded explicitly as `packaged`. Packaged images receive the same
label, manifest, prefix, and dynamic-library checks. The benchmark repository
ref and rclcpp ref remain separate, and the host architecture is detected
automatically.

The current runner writes benchmark artifacts under paths like:

```text
results/benchmark/lyrical/pub-sub_single_process/...
results/benchmark/lyrical/pub-sub_multi_process/...
results/benchmark/lyrical/cli-srv_single_process/...
results/benchmark/lyrical/cli-srv_multi_process/...
```

### Run A Controlled Repeated Comparison

Use an experiment bundle when comparing two exact rclcpp targets. The command
below creates one warm-up and three measured trials per target, schedules the
targets in a deterministic balanced order, and builds the comparison dataset
automatically:

```bash
ros2-performance-monitoring experiment run ./experiments/rclcpp-change \
  --reference-ref <reference-rclcpp-commit> \
  --candidate-ref <candidate-rclcpp-commit> \
  --suite service-rclcpp-minimal \
  -t 1 \
  --warmups 1 \
  --repeats 3 \
  --order balanced \
  --seed 42 \
  --cpuset-cpus 0-3
```

Use full commit SHAs for a plan that can be resumed even after a branch moves.
An explicitly packaged target is also supported, for example:

```bash
ros2-performance-monitoring experiment run ./experiments/package-vs-source \
  --reference-source packaged \
  --candidate-ref <candidate-rclcpp-commit>
```

The first invocation resolves and verifies both exact images before publishing
an immutable `plan.json`. Run the same command again to resume. A completed
trial is reused only when its completion manifest, metadata, normalized JSONL,
raw artifacts, and recorded checksums still match. Changed target identities,
ROS distribution, suite, executor, duration, CPU set, benchmark commit, trial
counts, order, or seed require a new experiment directory.

Warm-ups run through the same benchmark path but are omitted automatically from
the dataset and median lineage. Failed and interrupted attempts remain under
their trial directory for diagnosis. A successful bundle has this shape:

```text
experiment/
  plan.json
  measured_environment.json
  trials/<trial-id>/
    status.json
    complete.json
    attempts/<attempt>/
  dataset/dashboard-data.jsonl
  dataset/dashboard-data.manifest.json
  experiment.complete.json
  comparison-report.json  # after experiment compare
```

`experiment.complete.json` is written only after every planned trial and the
dataset bundle have passed checksum validation.

After the bundle completes, calculate repeat-aware evidence from the measured
trial pairs:

```bash
ros2-performance-monitoring experiment compare ./experiments/rclcpp-change
```

This writes a deterministic, versioned
`./experiments/rclcpp-change/comparison-report.json`. The default analysis uses
a paired bootstrap over the recorded balanced trial blocks, a 95% confidence
level, 10,000 resamples, seed `0`, and a minimum of three measured trial pairs.
Warm-ups, failed or incomplete trials, and median aggregate records are never
statistical samples. The command rejects incompatible target provenance,
environment evidence, scenario coverage, metric coverage, and schedules without
valid balanced pairs before calculating uncertainty.
It can also inspect an unfinished bundle: checksum-valid measured trials remain
eligible, while a missing planned pair produces `Incomplete results` rather
than reducing the requested sample count.

To reverse the comparison direction, reverse the plan labels explicitly:

```bash
ros2-performance-monitoring experiment compare ./experiments/rclcpp-change \
  --reference candidate \
  --candidate reference
```

The report keeps practical thresholds separate from confidence intervals and
uses `No regression`, `Possible regression`, `Regression`, `Insufficient
evidence`, `Incomplete results`, `Cannot compare`, and `N/A`. The command exits
with `0` for no regression, `1` for a supported regression, `2` for possible or
insufficient evidence, and `3` for an invalid or incomplete comparison. See
[`doc/statistical-comparison.md`](doc/statistical-comparison.md) for the method,
report contract, evidence rules, and optional analysis controls.

### 3. Inspect Or Reprocess The Artifacts

The `run` command automatically creates the normalized JSONL consumed by the
exporter and dashboard. To reprocess existing raw benchmark files, run:

```bash
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
```

You should see output similar to:

```text
Wrote <count> normalized metrics to ./results/normalized_metrics.jsonl
```

The normalized records include separate benchmark harness, client-library, and
host provenance. Grafana can scope comparisons by client library, platform,
ROS distribution, and whether the client library was built or packaged. Built
versions show their resolved commit; packaged versions are identified as
`packaged`. The adjacent run metadata also records the verified image name, ID,
digest, and complete target key.

### 4. Build A Comparison Dataset

The dashboard needs at least two runs in one JSONL input. Run the benchmark in
separate result directories, then combine their normalized files:

```bash
ros2-performance-monitoring run ./results/reference
ros2-performance-monitoring run ./results/candidate
```

Each `run` creates its own `normalized_metrics.jsonl`. Build the shared dataset:

```bash
ros2-performance-monitoring dataset build \
  ./results/reference/normalized_metrics.jsonl \
  ./results/candidate/normalized_metrics.jsonl \
  --output ./results/dashboard-data.jsonl
```

The command validates schemas, metric identities, and run provenance before it
atomically replaces the output. Input order does not affect the output bytes. A
`dashboard-data.manifest.json` sidecar records input checksums, source run IDs,
and the dataset checksum. The dataset is published first and the manifest is
published last as its completion marker.

For repeated measurements, keep the measured runs and add a median run for each
compatible group:

```bash
ros2-performance-monitoring dataset build \
  ./results/repeats/*/normalized_metrics.jsonl \
  --aggregate median \
  --exclude-run <warm-up-run-id> \
  --output ./results/dashboard-data.jsonl
```

`--exclude-run` may be repeated. Aggregate runs are only created from at least
two measured runs with identical provenance and scenario/metric coverage.
Dashboard selectors distinguish measured runs from median aggregates and show
the aggregate repeat count.

### 5. Check The Exporter Directly

This step is optional, but useful when you want to verify the metrics before
starting Grafana:

```bash
ros2-performance-monitoring serve-prometheus --input ./results/dashboard-data.jsonl --port 9108
```

Then open:

```text
http://localhost:9108/metrics
```

Stop the exporter with `Ctrl+C`.

### 6. Start Grafana And Prometheus

Start the local dashboard stack:

```bash
ros2-performance-monitoring dashboard up --input ./results/dashboard-data.jsonl
```

This starts Prometheus and Grafana with Docker Compose, then keeps the metrics
exporter running in the foreground. Keep this terminal open while using the
dashboard.

Open Grafana:

```text
http://localhost:3000
```

The curated regression view is provisioned automatically and configured as
Grafana's home dashboard. Look for:

```text
ROS 2 Performance · Default Regression Views
```

Use the mode control to move between the automatic full-matrix checks and the
manual scenario explorer. Click either run card to open that run's metadata,
scenario inventory, and complete performance profile. Both comparison views
show the same five KPI statuses and deterministic thresholds; see
[`doc/dashboard.md`](doc/dashboard.md#comparison-policy) for the policy and
missing-data rules.

### 7. Stop The Dashboard

Press `Ctrl+C` in the terminal running `dashboard up`, then stop the containers:

```bash
ros2-performance-monitoring dashboard down
```

## Useful Commands

Build only the benchmark container:

```bash
ros2-performance-monitoring build-container
```

Build and verify an exact source target without running a benchmark:

```bash
ros2-performance-monitoring build-container \
  --client-library-source build \
  --client-library-ref <branch-tag-or-commit>
```

Serve Prometheus metrics without starting Grafana:

```bash
ros2-performance-monitoring serve-prometheus --input ./results/normalized_metrics.jsonl --port 9108
```

Combine two completed runs without aggregation:

```bash
ros2-performance-monitoring dataset build \
  ./results/reference/normalized_metrics.jsonl \
  ./results/candidate/normalized_metrics.jsonl \
  --output ./results/dashboard-data.jsonl
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
  pub/sub and service dashboard.

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

If a source ref is missing or is ambiguous between a branch and tag, resolution
stops before Docker or metadata creation. Use a fully qualified ref such as
`refs/heads/rolling` or `refs/tags/<tag>` to disambiguate it. Source and
benchmark checkouts must also be clean at their resolved commits; the builder
rejects local cache edits because they would make the image provenance
unverifiable.

## Repository Boundary

This repository does not vendor the benchmark engines.

- `ros2-performance` is treated as an external ROS 2 benchmark framework.
- `ros2-benchmark-container` is treated as an external benchmark runner and
  artifact producer.
- rclcpp and benchmark sources are fetched into managed caches and copied only
  into derived local images; they are not vendored in this repository.
- No iRobot benchmark source code or result files are committed to this project.

The repository owns exact local image preparation and provenance verification
in addition to artifact parsing, normalization, export, and dashboards. It does
not own the external benchmark implementations or hosted infrastructure.

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
ros2-performance-monitoring help
ros2-performance-monitoring run
ros2-performance-monitoring build-container
ros2-performance-monitoring experiment run ./experiments/example \
  --reference-ref <reference-commit> --candidate-ref <candidate-commit>
ros2-performance-monitoring experiment compare ./experiments/example
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
ros2-performance-monitoring dataset build \
  ./results/run-1.jsonl ./results/run-2.jsonl \
  --output ./results/dashboard-data.jsonl
ros2-performance-monitoring serve-prometheus --input ./results/normalized_metrics.jsonl
ros2-performance-monitoring dashboard up --input ./results/normalized_metrics.jsonl
```

The `doctor` subcommand is currently a placeholder and does not perform
environment checks yet.

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
rosdep install --from-paths src --ignore-src -r -y
colcon build --packages-select ros2_performance_monitoring
source install/setup.bash
```

Run the CLI through ROS 2:

```bash
ros2 run ros2_performance_monitoring ros2-performance-monitoring run
ros2 run ros2_performance_monitoring ros2-performance-monitoring build-container
ros2 run ros2_performance_monitoring ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
ros2 run ros2_performance_monitoring ros2-performance-monitoring dataset build \
  ./results/run-1.jsonl ./results/run-2.jsonl \
  --output ./results/dashboard-data.jsonl
ros2 run ros2_performance_monitoring ros2-performance-monitoring dashboard up --input ./results/normalized_metrics.jsonl
```

### Benchmark container build

The `build-container` command prepares a verified local benchmark image. It
requires Docker and Docker Buildx to be installed and available on `PATH` in the
same shell that runs the command:

```bash
docker version
docker buildx version
```

It uses `vcstool` to fetch the external benchmark container repository and Git
to resolve optional rclcpp source targets. For pip installs, `vcstool` is
installed as a Python package dependency. For ROS 2 workspace installs,
`rosdep` installs it from the `python3-vcstool` package. The Docker build pulls
and exports a large ROS 2 base image, so make sure Docker has several GB of free
disk space available.

The upstream benchmark Dockerfile remains in the external repository. The
command fetches or updates that checkout before starting Docker. By default it
is stored at:

```bash
~/.cache/ros2-performance-monitoring
```

Managed rclcpp mirrors, immutable worktrees, and prepared benchmark script
contexts are stored beside it under:

```text
~/.cache/ros2-performance-monitoring-targets
```

The final image identity includes the ROS distribution, Docker architecture,
benchmark-container commit, rclcpp source and commit, and build configuration.
The image tag and retained-container name contain a prefix of that identity
key. Full inputs are recorded in Docker labels and in
`/etc/ros2-performance-monitoring/target-manifest.json` inside the image.

On a fresh machine, `build-container` can be run directly:

```bash
ros2-performance-monitoring build-container
```

That command builds a packaged-rclcpp image. Add the same source options used by
`run` to build a derived rclcpp image:

```bash
ros2-performance-monitoring build-container \
  --client-library-source build \
  --client-library-ref <branch-tag-or-commit>
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

The `run` command executes the current local benchmark path:

1. Resolve the requested packaged or source-built rclcpp target.
2. Fetch and resolve the external `ros2-benchmark-container` checkout.
3. Build an image keyed by all target inputs and store its provenance manifest.
4. Verify image labels, manifest, rclcpp prefix, and linked dynamic library.
5. Write metadata from the resolved and verified target.
6. Start the container, repeat runtime verification, and run the selected suite.
7. Write raw outputs and normalize them to `normalized_metrics.jsonl`.

The default run uses ROS `lyrical`, a 60 second duration, the
`EventsCBGExecutor` executor, the `rclcpp-minimal` suite, `./results` for
outputs, and
`~/.cache/ros2-performance-monitoring` for the external container checkout:

```bash
ros2-performance-monitoring run
```

The run options are:

```bash
ros2-performance-monitoring run \
  -t <duration> \
  -d <ros-distro> \
  -x <executor> \
  <results-dir>
```

The container repository is cached under
`~/.cache/ros2-performance-monitoring` by default. Use `--cache-dir` to place
the checkout elsewhere, such as on a persistent CI cache volume.

By default, `run` creates a benchmark container and removes it when the command
finishes. Pass `--keep-container` to retain it. A later `run --keep-container`
for the exact same target reuses that container and skips the image build:

```bash
ros2-performance-monitoring run --keep-container ./results/repeats/1-lyrical
ros2-performance-monitoring run --keep-container ./results/repeats/2-lyrical
```

Results directories used with one retained container must have the same parent
directory. This keeps every run separate while allowing the original results
root to remain mounted in the container. The command rejects an incompatible
results path instead of writing artifacts to the wrong location. Remove a
retained container when the repeated runs are complete, using the exact name
printed by `run`:

```bash
docker rm -f ros2-performance-monitoring-<distro>-<architecture>-<target-key>
```

Retaining the container avoids repeated Buildx work. A different ROS
distribution, architecture, benchmark commit, rclcpp commit, source type, or
build configuration produces a different image and container name. Labels and
the actual image ID are still checked before reuse, so modifying a matching-name
container does not bypass target verification.

If the required image has already been built, `--skip-build` prevents the first
retained run from invoking Buildx as well:

```bash
ros2-performance-monitoring run \
  --skip-build \
  --keep-container \
  ./results/repeats/1-lyrical
```

The command checks that the exact target image exists and verifies its labels,
manifest, package prefix, and linked library. It fails if any value is missing
or mismatched. Omit `--skip-build` when the requested target has not been built.

Supported suites are:

```bash
ros2-performance-monitoring run --suite rclcpp-minimal
ros2-performance-monitoring run --suite pubsub-rclcpp-minimal
ros2-performance-monitoring run --suite service-rclcpp-minimal
```

For repeatability work, `--cpuset-cpus` restricts the benchmark container to a
Docker CPU-set expression. Select cores that are appropriate for the benchmark
host. For example:

```bash
ros2-performance-monitoring run \
  --cpuset-cpus 0,2,4,6,8,10
```

The executor argument is passed directly to the benchmark container. Supported
values are `SingleThreadedExecutor`, `MultiThreadedExecutor`,
`EventsExecutor`, and `EventsCBGExecutor`.

The currently supported ROS distributions are `jazzy`, `lyrical`, and
`rolling`. Other distributions are rejected before the container repository is
fetched, run metadata is created, or an image build starts.

The default `rclcpp-minimal` suite runs the reduced pub/sub and service
topologies covered by the parser: single-process and multi-process pub/sub, plus
single-process and multi-process client/service. It covers `10b`, `100kb`,
`1mb`, and `4mb` payloads.

The benchmark runner requires Docker with the Buildx plugin and a running
Docker daemon. The current user must be able to run Docker commands without
`sudo`. The runner starts a privileged container and mounts
`/var/run/docker.sock` into it.

### Parse benchmark artifacts

The `run` command invokes normalization automatically. The `parse` command can
also read existing raw benchmark outputs and write normalized JSONL metrics:

```bash
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
```

When a results directory contains metadata from multiple runs, parsing selects
the newest metadata file and only discovers artifacts for that run's recorded
ROS distribution. This prevents retained artifacts from another distribution
from being labelled as part of the newest run.

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

### Build a comparison dataset

`dataset build` accepts normalized JSONL files, validates every non-empty line,
and creates the multi-run input expected by the dashboard. It rejects
unsupported schemas, non-finite metric values, conflicting run provenance,
duplicate metric identities, run IDs split across files, and output/input path
collisions before replacing an existing dataset.

```bash
ros2-performance-monitoring dataset build \
  <run-1>/normalized_metrics.jsonl \
  <run-2>/normalized_metrics.jsonl \
  --output dashboard-data.jsonl
```

Rows have stable ordering, so reversing the input arguments produces the same
JSONL. The adjacent `dashboard-data.manifest.json` records each resolved input
path, SHA-256 checksum, included run IDs, and the final dataset checksum.
Publication removes the old completion marker, atomically replaces the dataset,
and writes the new manifest last, so an interrupted update is never accepted as
complete.

Use `--aggregate median` for repeated measurements. Runs only share an
aggregate when their schema, ROS distribution, benchmark and client-library
provenance, platform, executor, benchmark layout, and complete metric identity
sets match. Commits, payloads, topologies, RMW implementations, communication
modes, and partial metric coverage are never mixed. For an even repeat count,
the median is the arithmetic mean of the two middle values after sorting. The
command reports compatible groups with fewer than two measured runs without
creating an aggregate for them.

Aggregate rows use a stable `aggregate-median-...` run ID and expose
`run_kind="aggregate"`, `aggregation_method="median"`, and `repeat_count` to
`ros2_perf_run_info`. Source run IDs and input checksums are stored once in the
sidecar manifest instead of being repeated on every metric row. Schema v4
measured records remain accepted; new parser output and aggregate records use
schema v5.

Service support includes request/response latency, CPU, and RSS visibility for
the local `10b`, `100kb`, `1mb`, and `4mb` layouts. Long-running actions,
multiple-client service sweeps, a full Zenoh matrix, remote-host tests, executor
sweeps, and CI-gating regression policy are deferred.

Run the ROS 2 package tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test --packages-select ros2_performance_monitoring --python-testing pytest
colcon test-result --verbose
```

## License

New code in this repository is licensed under the Apache License, Version 2.0.

Optional external benchmark tools referenced by this project may use different
open source licenses. See `THIRD_PARTY_NOTICES.md`.
