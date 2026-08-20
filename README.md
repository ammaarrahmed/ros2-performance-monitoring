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
  -> optional comparison-report.json
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

- A compatible Linux Docker host and an internet connection are available so
  external repositories and container images can be fetched.
- Docker is installed and running, and the current user can use it without
  `sudo`.
- Docker Compose and Docker Buildx plugins are installed.
- Docker has several GB of free disk space for the ROS 2 benchmark image.
- Ports `3000`, `9090`, and `9108` are free when starting the dashboard.

The container-first workflow needs no host Python, ROS installation, or
`vcstool`; the Docker engine and its Compose and Buildx plugins are the runtime
requirements. Git is needed on the host only to clone this repository and
record the checkout revision in a locally built controller image. The CLI
image contains the project command, Python dependencies, Git, `vcstool`, Docker
CLI, Buildx, and Compose.

The host-installed workflow remains supported. For that path, create a virtual
environment and install the command from the repository root:

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

## Container-First Workflow

The controller image orchestrates the existing benchmark container through the
mounted host Docker socket. It does not contain or start a Docker daemon, and it
does not run the measured ROS workload itself. The controller, benchmark
container, and any helper containers started by the external benchmark are
siblings on the same host daemon.

Build both runtime targets from the current checkout:

```bash
./scripts/container-workflow build
```

The wrapper creates persistent `./results` and `./.container-cache`
directories, passes the invoking UID and GID, adds the Docker socket group, and
records the current Git revision. Start with the short service benchmark:

```bash
./scripts/container-workflow run \
  --suite service-rclcpp-minimal \
  --duration 1 \
  smoke
```

`smoke` is resolved under the controller's `/results` mount and is written to
`./results/smoke` on the host. A repeated comparison uses the same CLI syntax:

```bash
./scripts/container-workflow experiment compare \
  --reference-ref <reference-rclcpp-commit> \
  --candidate-ref <candidate-rclcpp-commit> \
  --ros-distro rolling \
  --suite service-rclcpp-minimal \
  --duration 1 \
  --warmups 1 \
  --repeats 3 \
  --cpuset-cpus 0-3 \
  --results-dir rclcpp-change
```

The two persistent path mappings are explicit:

| Purpose | Controller path | Default host path |
| --- | --- | --- |
| Results and resumable bundles | `/results` | `./results` |
| Source, target, and BuildKit client state | `/cache` | `./.container-cache` |

Relative result and cache paths are resolved below those controller roots.
Absolute controller paths must also remain below the matching root. Docker bind
mount sources and retained-container labels use the translated absolute host
path; local Buildx contexts are read by the Docker client from the persistent
controller cache. This distinction prevents the host daemon from receiving a
container-only path, including when a path contains spaces.

To use Compose directly instead of the wrapper, set the mapping and ownership
variables first:

```bash
export ROS2_PERFORMANCE_RESULTS_DIR="$(pwd)/results"
export ROS2_PERFORMANCE_CACHE_DIR="$(pwd)/.container-cache"
export ROS2_PERFORMANCE_HOST_UID="$(id -u)"
export ROS2_PERFORMANCE_HOST_GID="$(id -g)"
export ROS2_PERFORMANCE_DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
export ROS2_PERFORMANCE_VCS_REF="$(git rev-parse HEAD)"
mkdir -p "$ROS2_PERFORMANCE_RESULTS_DIR" \
  "$ROS2_PERFORMANCE_CACHE_DIR/home"
docker compose build cli exporter
docker compose run --rm cli experiment compare \
  --reference-ref <reference-rclcpp-commit> \
  --candidate-ref <candidate-rclcpp-commit> \
  --results-dir rclcpp-change \
  --dry-run
```

Run a completed comparison dashboard entirely in containers by mounting its
bundle read-only into the exporter:

```bash
export ROS2_PERFORMANCE_DASHBOARD_DATA_DIR="$(pwd)/results/rclcpp-change"
export ROS2_PERFORMANCE_DATASET_PATH=dataset/dashboard-data.jsonl
export ROS2_PERFORMANCE_REPORT_PATH=/data/comparison-report.json
./scripts/container-workflow dashboard
```

For a dataset without a statistical report, leave
`ROS2_PERFORMANCE_REPORT_PATH` unset. Stop the stack with:

```bash
./scripts/container-workflow down
```

The exporter target runs as a non-root user with a read-only root filesystem,
a read-only evidence mount, all capabilities dropped, and no Docker tooling or
socket. Ports, host directories, data paths, image references, and dashboard
image/port choices are configurable through the `ROS2_PERFORMANCE_*` variables
in `compose.yml`.

Run metadata and trial environment evidence record whether the controller ran
on the host or in a container, the installed project version, inspected
controller image ID/digest and revision when available, Docker client version,
and the verified Docker server identity. Claimed container image metadata is
rejected when it does not match Docker inspection.

Containerizing the controller makes setup reproducible on compatible Linux
Docker hosts. It does not make performance measurements from different CPUs,
kernels, virtual machines, power states, thermal conditions, or background
loads directly comparable. The host-installed CLI remains available as a
reference controller for controlled A/B diagnostics.

## Host-Installed Workflow

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

`experiment compare` is the supported local per-commit workflow. It resolves
both rclcpp refs, prepares exact verified images, runs or resumes the experiment,
builds the dataset, generates repeat-aware evidence, validates every identity
and checksum, and prints the matching dashboard command:

The end-to-end command accepts balanced scheduling only because its statistical
report is built from paired balanced trial blocks. The lower-level `experiment
run` command continues to support interleaved scheduling for custom workflows.

```bash
ros2-performance-monitoring experiment compare \
  --reference-ref <reference-rclcpp-commit> \
  --candidate-ref <candidate-rclcpp-commit> \
  --ros-distro rolling \
  --suite service-rclcpp-minimal \
  --duration 1 \
  --warmups 1 \
  --repeats 5 \
  --order balanced \
  --seed 42 \
  --cpuset-cpus 0-3 \
  --results-dir ./experiments/rclcpp-change
```

The official `https://github.com/ros2/rclcpp.git` repository is the default.
Use `--rclcpp-repo-url` to compare refs from a fork. Full commit SHAs are best
for a workflow that must remain resumable after branches move.

Before fetching persistent source checkouts, the command checks Git, vcstool,
Docker daemon access, Buildx and native architecture support, result-directory
access, at least 10 GiB of free result and Docker storage, and CPU-set syntax.
Docker Compose and ports `3000`, `9090`, and the selected exporter port are
checked only with `--start-dashboard`.

Inspect the fully resolved commits, image keys, immutable configuration, trial
order, and output paths without cloning build contexts or writing an experiment:

```bash
ros2-performance-monitoring experiment compare \
  --reference-ref <reference-rclcpp-commit> \
  --candidate-ref <candidate-rclcpp-commit> \
  --results-dir ./experiments/rclcpp-change \
  --dry-run
```

The real invocation publishes the immutable `plan.json` only after both refs
resolve and both target images are verified. Run the identical command again to
resume. Verified images, completed trials, the dataset, and a matching report
are reused only when their complete checksum chain remains valid. A changed
target, ROS distribution, suite, executor, duration, CPU set, benchmark commit,
trial count, order, or scheduling seed is rejected with an instruction to use a
new result directory.

Warm-ups run through the same benchmark path but are omitted automatically from
the dataset and median lineage. Failed builds and workflow stages are recorded
in `workflow.log` and `workflow.status.json`; failed and interrupted trial
attempts keep their own diagnostic logs and never enter the dataset. A
successful bundle has this shape:

```text
experiment/
  workflow.log
  workflow.status.json
  plan.json
  targets/reference.json
  targets/candidate.json
  measured_environment.json
  trials/<trial-id>/
    status.json
    complete.json
    attempts/<attempt>/
  dataset/dashboard-data.jsonl
  dataset/dashboard-data.manifest.json
  experiment.complete.json
  comparison-report.json
  comparison.complete.json
```

The version 2 `experiment.complete.json` is written only after every planned
trial and the dataset bundle pass checksum validation. It binds the
`measured_environment.json` path and SHA-256 in addition to the plan, trial
completion files, dataset, and dataset manifest. Every checksum-valid measured
trial environment must match that top-level host identity; warm-up environment
evidence is retained but cannot establish or weaken the measured identity.

The version 2 `comparison.complete.json` is the end-to-end completion marker. It
binds the plan, verified target manifests, experiment completion, dataset,
dataset manifest, report, evidence status, and comparison exit outcome by
SHA-256. Resume validates every stable marker field before reusing the report.
If a derived report or its marker is missing, damaged, version 1, or disagrees
with those verified inputs, the workflow removes the invalid marker and
deterministically regenerates the report and version 2 marker from the verified
experiment evidence. Version 1 experiment markers are likewise regenerated
from checksum-valid trials, measured environments, and datasets without
rerunning valid trials. A failure during recovery leaves no final comparison
completion marker.

The default report analysis uses a paired bootstrap over recorded balanced
trial blocks, a 95% confidence level, 10,000 resamples, bootstrap seed `0`, and
a minimum of three measured pairs. Use `--bootstrap-seed` separately from the
schedule's `--seed`. Warm-ups, failed or incomplete trials, and median aggregate
records are never statistical samples.

The lower-level component commands remain available for diagnosis or custom
composition. `experiment run` creates or resumes only the experiment and
dataset; `experiment report` analyses an existing bundle. For example, reverse
an existing report without rerunning benchmarks:

```bash
ros2-performance-monitoring experiment report ./experiments/rclcpp-change \
  --reference candidate \
  --candidate reference
```

The report keeps practical thresholds separate from confidence intervals and
uses `No regression`, `Possible regression`, `Regression`, `Insufficient
evidence`, `Incomplete results`, `Cannot compare`, and `N/A`. The command exits
with `0` for no regression, `1` for a supported regression, `2` for possible or
insufficient evidence, `3` for an invalid, incomplete, or non-comparable
comparison, and `4` only when an operational failure prevents the comparison.
Preflight, resolution, build, trial, parse, dataset, validation, and dashboard
startup failures are operational failures, so they are never mistaken for a
performance verdict.
See [`doc/statistical-comparison.md`](doc/statistical-comparison.md) for the
method, report contract, evidence rules, and optional analysis controls.

For a suite containing both Pub/Sub and Service scenarios, the report keeps a
report-wide result and also evaluates each topology independently from its own
paired measurements. The dashboard shows the independently calculated result
for the selected topology, so a regression in one workload does not change the
other workload's status. Service throughput and reliability remain `N/A` and
are excluded from the Service overall result.

The final summary prints this exact report-bound dashboard command:

```bash
ros2-performance-monitoring dashboard up \
  --input ./experiments/rclcpp-change/dataset/dashboard-data.jsonl \
  --comparison-report ./experiments/rclcpp-change/comparison-report.json
```

Dashboard startup verifies the report schema, experiment and target identities,
method, scenario coverage, and dataset SHA-256 before starting Docker. A stale
or unrelated report therefore fails instead of being displayed beside a
different dataset. Add `--start-dashboard` to the comparison invocation to run
that command automatically after successful validation.

### Calibrate Same-Commit Benchmark Noise

Use the separate calibration workflow before treating comparison outcomes as a
required gate. It resolves one exact rclcpp target, measures it as two distinct
balanced streams, and reports how often unchanged paired KPI effects cross the
current practical thresholds. It does not produce a reference-versus-candidate
verdict:

```bash
ros2-performance-monitoring experiment calibrate \
  --target-ref <rclcpp-commit> \
  --ros-distro rolling \
  --suite service-rclcpp-minimal \
  --duration 10 \
  --warmups 2 \
  --repeats 10 \
  --cpuset-cpus 0-3 \
  --seed 42 \
  --results-dir ./experiments/host-calibration
```

Ten measured pairs per stream are a recommended first local profile, not a
statistical guarantee or an automatic threshold recommendation. Use an idle,
thermally stable host with a dedicated CPU set and keep the power, cooling,
middleware, Docker, kernel, and background-load conditions consistent. The
bundle records the exact target and benchmark commits, ROS distribution,
executor, suite, duration, architecture, kernel, Docker version, CPU governors,
CPU set, per-trial load averages, and available thermal-zone readings.

The workflow writes `calibration-report.json` and
`calibration.complete.json`, returns `0` when valid calibration evidence is
published, and returns `4` for operational failure. It never returns a
regression-gate outcome. Calibration reports have a separate schema and are
rejected by `--comparison-report`; inspect them as JSON instead of supplying
them to the exporter or dashboard. Rerun the identical command to resume or
verify the immutable bundle. See [`doc/calibration.md`](doc/calibration.md) for
the method, report fields, controlled-host checklist, and interpretation.

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

For a completed repeated experiment, add its report:

```bash
ros2-performance-monitoring serve-prometheus \
  --input ./experiments/rclcpp-change/dataset/dashboard-data.jsonl \
  --comparison-report ./experiments/rclcpp-change/comparison-report.json \
  --port 9108
```

When supplied, the validated report is the source of truth for status and only
its reference/candidate aggregate pair is exported. Mixed reports expose the
report-wide summary with `topology="all"` and independently calculated summaries
with `topology="pub-sub"` and `topology="service"`. A single-topology report
exports only its matching topology summary. Without a report, the exporter
retains the legacy ordered-pair policy and labels it `threshold-only`.

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
show the same five KPI statuses. When a report is supplied, the evidence strip
shows the measured-pair count, selected-category effect estimate, confidence
interval, and practical thresholds for the selected topology. Without one, the
analysis method is visibly labelled `Threshold-only`; see
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
ros2-performance-monitoring experiment compare \
  --reference-ref <reference-commit> \
  --candidate-ref <candidate-commit> \
  --results-dir ./experiments/example
ros2-performance-monitoring experiment calibrate \
  --target-ref <commit> \
  --results-dir ./experiments/calibration
ros2-performance-monitoring experiment report ./experiments/example
ros2-performance-monitoring parse ./results --output ./results/normalized_metrics.jsonl
ros2-performance-monitoring dataset build \
  ./results/run-1.jsonl ./results/run-2.jsonl \
  --output ./results/dashboard-data.jsonl
ros2-performance-monitoring serve-prometheus --input ./results/normalized_metrics.jsonl
ros2-performance-monitoring dashboard up --input ./results/normalized_metrics.jsonl
```

Pass `--comparison-report <path>` to either exporter command to use a matching
schema-v3 statistical report instead of the legacy threshold-only statuses.

The `doctor` subcommand is currently a placeholder and does not perform
environment checks yet.

Run the Python tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest
```

The two-target Docker smoke test is opt-in because it builds source overlays and
runs six short benchmark trials. Use a dedicated cache and confirm adequate
memory and disk space first:

```bash
ROS2_PERFORMANCE_RUN_WORKFLOW_INTEGRATION=1 \
ROS2_PERFORMANCE_INTEGRATION_CACHE=~/.cache/ros2-performance-monitoring-integration \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q test/test_comparison_workflow_integration.py
```

Override `ROS2_PERFORMANCE_INTEGRATION_REFERENCE_REF`,
`ROS2_PERFORMANCE_INTEGRATION_CANDIDATE_REF`,
`ROS2_PERFORMANCE_INTEGRATION_DISTRO`, or
`ROS2_PERFORMANCE_INTEGRATION_CPUSET` when the defaults are unsuitable for the
host.

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

Container distribution tests are opt-in because they build local images. They
verify the CLI/exporter tool boundary, non-root exporter health and metrics,
and same-daemon sibling visibility, then remove their temporary containers and
image tags:

```bash
ROS2_PERFORMANCE_RUN_CONTAINER_IMAGE_TESTS=1 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q test/test_container_distribution.py
```

The end-to-end container benchmark test is gated separately because it builds
an exact upstream ROS 2 target and needs several GB of Docker storage. Give it
a dedicated persistent source cache; exact refs and an isolated CPU set are
recommended for reviewer runs:

```bash
ROS2_PERFORMANCE_RUN_CONTAINER_BENCHMARK_TEST=1 \
ROS2_PERFORMANCE_CONTAINER_BENCHMARK_CACHE="$HOME/.cache/ros2-performance-container-test" \
ROS2_PERFORMANCE_CONTAINER_BENCHMARK_REF=<ros2-benchmark-container-commit> \
ROS2_PERFORMANCE_CONTAINER_RCLCPP_REF=<rclcpp-commit> \
ROS2_PERFORMANCE_CONTAINER_CPUSET=0-3 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q \
  test/test_container_distribution.py::test_container_controller_runs_short_upstream_benchmark
```

The test runs the reduced service suite for one second per case, checks
controller/daemon/image provenance and host ownership, and removes benchmark
image tags that it created. BuildKit cache and the explicitly supplied source
cache remain reusable; inspect them with `docker system df` and remove them
only when they are no longer needed.

## License

New code in this repository is licensed under the Apache License, Version 2.0.

Optional external benchmark tools referenced by this project may use different
open source licenses. See `THIRD_PARTY_NOTICES.md`.
