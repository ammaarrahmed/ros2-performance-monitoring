# ROS 2 Performance Monitoring

This repository turns ROS 2 benchmark output into normalized JSONL, verified
comparison evidence, Prometheus metrics, and Grafana dashboards. You can run the
complete workflow locally or publish completed GitHub Actions artifacts to a
long-running Linux dashboard host.

The project runs reduced `rclcpp` Pub/Sub and Service benchmark suites. It
supports packaged ROS installations, exact source commits, controlled repeated
experiments, and non-authoritative scheduled Rolling smoke comparisons.

## Table Of Contents

- [What This Does](#what-this-does)
- [Choose A Workflow](#choose-a-workflow)
- [Prerequisites](#prerequisites)
- [Container-First Workflow](#container-first-workflow)
- [Host-Installed Workflow](#host-installed-workflow)
- [Hosted Rolling Workflow](#hosted-rolling-workflow)
- [Calibration Workflow](#calibration-workflow)
- [Inspect And Visualize Results](#inspect-and-visualize-results)
- [Useful Commands](#useful-commands)
- [Troubleshooting](#troubleshooting)
- [Repository Boundary](#repository-boundary)
- [Development Checks](#development-checks)
- [License](#license)

## What This Does

```text
benchmark container run
  -> raw benchmark artifacts
  -> normalized_metrics.jsonl
  -> dataset build
  -> dashboard-data.jsonl
  -> optional repeat-aware comparison-report.json
  -> Prometheus exporter
  -> Prometheus
  -> Grafana
```

The dashboard compares runs and reports separate latency, throughput, resource,
reliability, and overall statuses. Missing required results and non-applicable
service metrics are explicit rather than treated as passing measurements. The
project does not treat its uncalibrated smoke profile as a CI gate and does not
provision cloud infrastructure. Comparisons can be scoped by history bundle,
ROS client library ref, ROS distribution, RMW implementation, communication
mode, topology, and payload size.

## Choose A Workflow

Most users should start with the container-first workflow. It keeps Python, ROS
2, `vcstool`, and the project CLI inside versioned images while continuing to
run the measured ROS workload in the upstream benchmark container.

| Goal | Recommended path | Start here |
| --- | --- | --- |
| Verify the project on one machine | Container-first short benchmark | [Container-First Workflow](#container-first-workflow) |
| Compare two exact rclcpp commits | Controlled repeated comparison | [Compare Exact rclcpp Commits](#compare-exact-rclcpp-commits) |
| Develop or debug the Python/ROS package | Host-installed workflow | [Host-Installed Workflow](#host-installed-workflow) |
| Produce periodic Rolling evidence | GitHub Actions producer | [Hosted Rolling Workflow](#hosted-rolling-workflow) |
| Measure normal same-commit variation | Controlled A/A calibration | [Calibration Workflow](#calibration-workflow) |
| Publish verified bundles to a Linux host | Transactional publisher | [`doc/dashboard-publication.md`](doc/dashboard-publication.md) |

Benchmark runs are intentionally invasive: they use privileged containers and
temporarily change CPU-governor settings. Results from different machines,
kernels, power states, thermal conditions, or background loads are not directly
comparable.

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

### Use Published Runtime Images

Project releases publish matching `linux/amd64` runtime images at:

- `ghcr.io/ammaarrahmed/ros2-performance-monitoring-cli`
- `ghcr.io/ammaarrahmed/ros2-performance-monitoring-exporter`

Use the manifest digests recorded in the
[GitHub release notes](https://github.com/ammaarrahmed/ros2-performance-monitoring/releases)
and workflow summary. Check out the same project release, then pin both images
by digest:

```bash
git switch --detach <release-version>
export ROS2_PERFORMANCE_CLI_IMAGE="ghcr.io/ammaarrahmed/ros2-performance-monitoring-cli@sha256:<cli-digest>"
export ROS2_PERFORMANCE_EXPORTER_IMAGE="ghcr.io/ammaarrahmed/ros2-performance-monitoring-exporter@sha256:<exporter-digest>"
docker pull "$ROS2_PERFORMANCE_CLI_IMAGE"
docker pull "$ROS2_PERFORMANCE_EXPORTER_IMAGE"
./scripts/container-workflow run \
  --suite service-rclcpp-minimal \
  --duration 1 \
  smoke
```

The same exporter pin is used by `./scripts/container-workflow dashboard`.
Update or roll back by changing the two digest values; released digests are not
removed automatically. Release-version and full 40-character commit tags are
also published for discovery, but deployments should use digests. There is no
`latest` release contract.

The repository's two GHCR packages are public and can be pulled without
registry credentials. Fork maintainers publishing under a different namespace
must make their new package entries public once; see GitHub's
[package visibility documentation](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).
The OCI source label links each package to its repository.

To test unreleased changes or a non-`amd64` host, build the targets locally as
described below.

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
`./results/smoke` on the host. It verifies benchmark execution and
normalization, but one run alone is not a comparison dataset.

### Compare Exact rclcpp Commits

A repeated comparison uses the same CLI syntax:

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
export ROS2_PERFORMANCE_PROJECT_VERSION="$(sed -n 's:.*<version>\([^<]*\)</version>.*:\1:p' package.xml)"
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

To serve several downloaded compact bundles, mount one directory containing a
versioned active-history index and all of its relative bundle paths:

```bash
export ROS2_PERFORMANCE_DASHBOARD_DATA_DIR="$(pwd)/deployment"
export ROS2_PERFORMANCE_HISTORY_INDEX_PATH=/data/active-history.json
unset ROS2_PERFORMANCE_REPORT_PATH
./scripts/container-workflow dashboard
```

The exporter validates the complete window before startup and caches the
rendered metrics for normal scrapes. See
[`doc/dashboard-history.md`](doc/dashboard-history.md) for the index, checksum,
compact report bundle, and legacy threshold-only bundle contracts.

To publish a completed compact bundle to a long-running Linux dashboard host,
use the provider-neutral transactional publisher:

```bash
ros2-performance-monitoring dashboard publish \
  --bundle ./rclcpp-dashboard.zip \
  --profile .github/benchmark-profiles/rolling-workflow-smoke-v2.json \
  --deployment-root /srv/ros2-performance-monitoring/dashboard \
  --restart-hook /usr/local/libexec/ros2-performance-restart-dashboard
```

The publisher safely extracts and validates the bundle, serializes concurrent
updates, activates a bounded history atomically, rolls back failed hook or
health checks, applies inactive retention, and records an audit trail. A
separate pull adapter can retrieve completed GitHub Actions artifacts without
coupling the core publisher to GitHub. See
[`doc/dashboard-publication.md`](doc/dashboard-publication.md) for the full
contract and generic systemd/container examples.

When no workflow run is pinned, the GitHub adapter checks successful runs from
newest to oldest and selects the first run containing exactly one unexpired
artifact with the configured prefix. Successful no-op runs that intentionally
produce no artifact, such as an unchanged-upstream skip, do not hide the most
recent publishable comparison. An explicit `--run-id` remains strict and never
falls back to a different run.

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

### Start With A Short Benchmark

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

### Run The Full Benchmark

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

Rolling commits can depend on APIs that have merged into another ROS repository
but have not reached binary packages yet. Supply those sources with an exact
[`vcstool`](https://github.com/dirk-thomas/vcstool) manifest:

```yaml
repositories:
  ros2/rcl:
    type: git
    url: https://github.com/ros2/rcl.git
    version: <full-40-character-rcl-commit>
```

```bash
ros2-performance-monitoring experiment compare \
  --reference-ref <reference-rclcpp-commit> \
  --candidate-ref <candidate-rclcpp-commit> \
  --source-dependencies ./rolling-source-dependencies.repos \
  --results-dir ./experiments/rclcpp-change
```

Dependency manifests accept Git repositories and full lowercase commit SHAs
only. The command validates the standard `.repos` structure, verifies every
commit, creates managed immutable worktrees, and builds the dependency workspace
before rclcpp. Both comparison targets use the same snapshot, so dependency
movement cannot be mistaken for the candidate's effect inside a comparison.
Repository paths, URLs, and exact commits are included in the target key, Docker
labels, `plan.json`, and the in-image target manifest. The same option is
available on `run`, `build-container`, `experiment run`, and `experiment
calibrate`; it cannot be combined with packaged rclcpp targets.

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
target, source dependency snapshot, ROS distribution, suite, executor, duration,
CPU set, benchmark commit, trial count, order, or scheduling seed is rejected
with an instruction to use a new result directory.

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

## Hosted Rolling Workflow

The `Produce latest rclcpp comparison` GitHub Actions workflow is the hosted
producer for bounded dashboard history. It resolves the exact current
`ros2/rclcpp` Rolling SHA, compares it with the last successfully published
candidate, and skips before dependency installation or image builds when the
SHA is unchanged. If several upstream commits arrive between runs, it produces
one latest-versus-last-successful comparison instead of benchmarking every
missed commit.

The pinned `rolling-workflow-smoke-v2` profile runs `rclcpp-minimal` with
one-second samples, no warm-ups, three measured pairs, balanced ordering, 100
bootstrap resamples, and no CPU-set requirement. Its exact benchmark-container
commit, Rolling source dependency repositories, and all analysis settings live
in `.github/benchmark-profiles/rolling-workflow-smoke-v2.json`.

When a changed rclcpp SHA needs a comparison, discovery resolves the configured
`ros2/rcl` Rolling branch once and converts it to an exact `.repos` snapshot.
Reference and candidate images build against that same rcl commit. The snapshot
is checksum-verified before the controller starts, becomes part of both target
identities, and is recorded in the producer manifest and durable state. An
unchanged rclcpp SHA still skips before resolving rcl or pulling the controller.

The benchmark job pulls the released CLI controller by immutable registry
digest and uses the host Docker socket. The controller and measured benchmark
remain sibling containers on the runner's daemon, and the inspected controller
digest is recorded in run provenance. A failed controller pull is retried three
times before target preparation starts.

An operational comparison failure receives one same-run resume attempt with
the identical plan and results directory. Verified trials are reused and failed
attempts remain in the experiment evidence. If the resume also fails, the job
prints the nested trial logs and uploads a seven-day
`rclcpp-failure-<candidate-sha>-<run-id>-<run-attempt>` diagnostic artifact.
This artifact never uses the dashboard prefix and cannot advance the durable
baseline.

> This profile produces non-authoritative pipeline smoke evidence only. It is
> not calibrated for authoritative performance claims and its outcome is not a
> CI gate.

Before enabling the schedule in a new deployment or fork, run the first
end-to-end pilot manually on the default branch. Supply an exact bootstrap SHA
when a specific initial baseline is required; otherwise the workflow records
and uses the candidate's first parent:

```bash
gh workflow run scheduled-rclcpp-comparison.yml --ref main

# Explicit first-run baseline alternative:
gh workflow run scheduled-rclcpp-comparison.yml \
  --ref main \
  -f bootstrap_sha=<exact-rclcpp-commit>
```

The off-hours Monday schedule is gated by the repository Actions variable
`ENABLE_RCLCPP_SCHEDULE`; this repository enables it after the successful
pilot. A fork should leave the variable unset until its own pilot succeeds. A
single concurrency group serializes manual and scheduled producers, so two runs
cannot race while advancing the baseline.

Completed comparison outcomes with exit codes `0`, `1`, or `2` publish two
14-day artifacts and advance state. Invalid or operational outcomes with exit
codes `3` or `4` fail without changing the baseline:

- `rclcpp-evidence-<candidate-sha>-<run-id>` contains the full resumable
  evidence bundle.
- `rclcpp-dashboard-<candidate-sha>-<run-id>` contains the compact dashboard
  dataset, report, manifests, and completion chain.

Both artifacts include `producer-manifest.json` and `SHA256SUMS`, binding the
profile, exact rclcpp and dependency SHAs, benchmark run IDs, workflow run
identity, outcome, and every uploaded file. Download and validate the compact
artifact with the exact command printed in the workflow summary:

```bash
gh run download <run-id> \
  --repo ammaarrahmed/ros2-performance-monitoring \
  --name rclcpp-dashboard-<candidate-sha>-<run-id> \
  --dir ./rclcpp-dashboard
python3 -m ros2_performance_monitoring.scheduled_comparison validate \
  --profile .github/benchmark-profiles/rolling-workflow-smoke-v2.json \
  --bundle ./rclcpp-dashboard
```

Several validated compact artifacts can be activated together through the
bounded history index. It lists bundle directories explicitly in oldest-first
order, pins each `SHA256SUMS` digest, carries the expected profile authority and
notice, and sets the maximum active count. The exporter fails atomically on a
mismatched report/dataset pair, checksum error, duplicate run ID, or duplicate
Prometheus series. It never scans a directory or manages artifact retention.
See [`doc/dashboard-history.md`](doc/dashboard-history.md) for the complete
contract and deployment example.

The durable baseline is the reviewable
`.benchmark-state/rclcpp-last-successful.json` file on the `benchmark-state`
branch. Only a completed default-branch producer may update it. The benchmark
job has read-only repository permission; the separate state job downloads and
revalidates the compact artifact before receiving `contents: write`. The
workflow never runs for pull requests and does not publish benchmark images.

## Calibration Workflow

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

## Inspect And Visualize Results

The `run` and `experiment compare` commands already normalize their results.
Use the following commands when reprocessing old artifacts, composing a dataset
manually, checking the exporter, or opening Grafana without rerunning a
benchmark.

### Reprocess Existing Artifacts

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

### Build A Comparison Dataset

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

### Check The Exporter Directly

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

To inspect an indexed comparison window instead:

```bash
ros2-performance-monitoring serve-prometheus \
  --history-index ./deployment/active-history.json \
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

### Start Grafana And Prometheus

Start the local dashboard stack:

```bash
ros2-performance-monitoring dashboard up --input ./results/dashboard-data.jsonl
```

Use `--history-index ./deployment/active-history.json` instead of `--input` to
select from a bounded window. The history bundle selector keeps raw queries and
the reference/candidate pair within the same verified bundle. The home view
also shows the bound profile, authoritative flag, and producer notice.

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

### Stop The Dashboard

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

The host-installed dashboard uses `compose.dashboard.yml` and
`config/prometheus/prometheus.yml`. The container-first stack uses `compose.yml`
and `config/prometheus/prometheus.container.yml`. Both use
`config/grafana/provisioning/` and the tracked dashboards under
`config/grafana/dashboards/`.

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

The repository owns exact image preparation, provenance verification, artifact
parsing, normalization, export, dashboards, and provider-neutral publication
logic. It does not own the external benchmark implementations or provision a
VM, DNS, tunnel, or other cloud infrastructure.

## Development Checks

This package can be installed either as a regular Python package with `pip` or
as a ROS 2 package with `colcon`. Use one workflow at a time.

### Python virtual environment

From a clean checkout, create a virtual environment, install the development
dependencies, and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install .
```

Confirm that the installed CLI is the expected checkout:

```bash
ros2-performance-monitoring --version
ros2-performance-monitoring help
```

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

### Runtime image release checks

The package version in `package.xml` is the project version source. A release
tag must match it exactly in `MAJOR.MINOR.PATCH` form; `v`-prefixed,
placeholder, mismatched, or abbreviated identities are rejected. Publish by
creating a GitHub release from that existing tag, or manually dispatch the
`Publish runtime images` workflow with the tag when publication must be
started explicitly. Pull requests, schedules, and ordinary branch pushes do
not publish images.

The workflow builds and smoke-tests both targets before it logs in and pushes
either release image. It publishes no partial release summary unless both
manifests and their attestations succeed. Every Action, BuildKit image, and
runtime base image is pinned; BuildKit cache entries are never tagged as
releases. Each pushed image includes standard OCI identity labels, an SBOM,
maximal BuildKit provenance, and a GitHub registry-backed provenance
attestation. The summary records each manifest digest, build/push time, and
compressed content size. Published-release runs append the same immutable
digests to the release notes.

Run the offline release-contract tests with:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  test/test_release_contract.py test/test_publish_workflow.py
```

Run the local image inspection, CLI data-processing, and exporter endpoint
smoke test with:

```bash
version="$(sed -n 's:.*<version>\([^<]*\)</version>.*:\1:p' package.xml)"
revision="$(git rev-parse HEAD)"
docker build --target cli \
  --build-arg "PROJECT_VERSION=$version" \
  --build-arg "VCS_REF=$revision" \
  --tag ros2-performance-monitoring-cli:release-smoke .
docker build --target exporter \
  --build-arg "PROJECT_VERSION=$version" \
  --build-arg "VCS_REF=$revision" \
  --tag ros2-performance-monitoring-exporter:release-smoke .
scripts/smoke-runtime-images \
  ros2-performance-monitoring-cli:release-smoke \
  ros2-performance-monitoring-exporter:release-smoke \
  "$version" "$revision"
docker image rm \
  ros2-performance-monitoring-cli:release-smoke \
  ros2-performance-monitoring-exporter:release-smoke
```

The smoke script removes its temporary exporter container and output directory.
The final `docker image rm` removes only the two explicitly named local test
images.

### ROS 2 workspace

Place this package inside a ROS 2 workspace, build it with `colcon`, and source
the workspace:

```bash
ROS_DISTRO=lyrical  # Replace this with an installed ROS 2 distribution.
source "/opt/ros/${ROS_DISTRO}/setup.bash"
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

### Benchmark And Container Integration Checks

The user-facing build, run, parse, and dataset commands are documented in
[Host-Installed Workflow](#host-installed-workflow),
[Inspect And Visualize Results](#inspect-and-visualize-results), and
[Useful Commands](#useful-commands). Use `ros2-performance-monitoring
<command> --help` for the complete option reference. The checks below focus on
ROS package and container integration instead of repeating those workflows.

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
