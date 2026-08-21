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
- Controlled same-target calibration evidence for local benchmark noise.
- End-to-end local per-commit workflow orchestration and completion evidence.
- Short-lived scheduled comparison artifacts for latest-versus-last-successful
  rclcpp revisions.
- Explicit bounded activation of checksum-verified dashboard history.
- Transactional provider-neutral publication to remote Linux dashboard hosts.

It avoids taking ownership of:

- Ownership or vendoring of benchmark topology implementations.
- Hosted statistical analysis or CI-gating policy.
- Long-running hosted monitoring infrastructure.

## Bridge Shape

The design uses a small adapter boundary:

```text
focused host preflight
  -> remotely resolved dry-run plan, or persistent benchmark + rclcpp targets
  -> labelled image + in-image target manifest
  -> verified container runner
  -> staged trial (raw artifacts + metadata + normalized JSONL)
  -> checksum-verified trial completion
  -> validated comparison dataset + experiment completion
     -> paired measured-trial bootstrap -> comparison-report.json
     -> cross-artifact validation -> comparison.complete.json
     -> report validation + Prometheus mapping -> Prometheus -> Grafana
     -> legacy threshold-only comparison -> Prometheus -> Grafana
     -> active-history index -> atomic bundle validation -> cached Prometheus
        history -> bundle-scoped Grafana views
     -> safe local extraction -> locked atomic history publication
        -> hook + exact-index exporter health -> rollback or retention
     -> same-target paired noise analysis -> calibration-report.json
        -> calibration.complete.json (never a dashboard or gate input)
```

## Controller Execution Boundary

The host-installed and containerized commands share the same Python
orchestration code. Container mode changes only the controller boundary. It
uses Docker-outside-of-Docker through the mounted host socket; it does not add
a nested daemon, storage driver, network, cgroup hierarchy, or ROS runtime:

```text
Linux host Docker daemon
  +-- non-root CLI controller (or host-installed CLI)
  +-- privileged benchmark container using host networking and shared memory
  +-- optional helper containers started by the external benchmark
  +-- non-root exporter without Docker access
```

The measured ROS process stays in `ros2/ros2-benchmark-container`. The
controller resolves inputs, sends build contexts through the Docker client,
starts the verified benchmark sibling, invokes the existing workload through
`docker exec`, and waits for it. Host networking, privilege, shared-memory
size, CPU-set selection, result mount, Docker socket, and governor behavior are
unchanged for the benchmark container.

Container and daemon paths are separate types at the orchestration boundary.
The controller validates a results mapping and a cache mapping supplied by
Compose. Filesystem operations and Buildx client contexts use controller paths;
daemon bind mounts and retained-container labels use translated absolute host
paths. Relative paths resolve below the declared controller root, and absolute
paths outside it are rejected. This keeps paths containing spaces safe and
prevents resume or container reuse from accidentally comparing one namespace
with another.

The controller process runs with the invoking host UID and GID. Compose adds
only the host Docker socket group, and the benchmark container restores raw
result ownership to the explicitly supplied host IDs. Results and source caches
are bind-mounted and persist independently of the controller image.

Preflight verifies the connected daemon identity through `docker info`. In
container mode it measures Docker-root free space with a read-only, networkless
BusyBox sibling that mounts the host root; it never treats the daemon's
`DockerRootDir` as a controller-local directory. Trial evidence records the
controller mode, project version, inspected controller image identity, Docker
client version, and verified server identity. Container image claims are
accepted only when they match inspection of the running container and image.

The `exporter` image is built from the same source wheel as the `cli` image but
contains no Docker client, `vcstool`, socket, or benchmark runtime. Compose runs
it as non-root with a read-only evidence mount and root filesystem, dropped
capabilities, and `no-new-privileges`. Prometheus reaches it over the Compose
network rather than through a host-installed Python process.

## Runtime Image Release Boundary

`package.xml` is the canonical project version. Python package metadata reads
that value during the wheel build, and the installed CLI reports it through
`--version`. Release publication accepts only an existing `MAJOR.MINOR.PATCH`
Git tag that exactly matches the package version. The same value and the full
tagged commit are passed to the OCI version and revision labels and verified
against the locally loaded images before registry authentication.

The release workflow has a pre-publication phase and a publication phase. The
first phase checks out the exact tag, builds both `linux/amd64` targets, runs
CLI help/version and data processing, verifies image contents and metadata,
and serves a read-only fixture through the exporter health and metrics
endpoints. A failure in either build or any smoke check prevents both pushes.
The second phase refuses to overwrite either version or full-commit tag, then
publishes both manifests with SBOM and maximal BuildKit provenance attestations.
GitHub provenance attestations bind the workflow identity to the returned
registry digests. Only after both images are pushed and attested does the
workflow report a complete release and update release notes.

The CLI and exporter packages are linked to this repository through their OCI
source labels. Package visibility is a one-time owner-controlled registry
setting; publication credentials remain the workflow's scoped `GITHUB_TOKEN`.
Build cache uses the GitHub Actions cache backend and is never an image tag.
Release manifests remain addressable by digest for update and rollback; there
is no mutable `latest` deployment input or automatic registry cleanup.

These distributable project runtimes are separate from the large derived ROS
benchmark target images. Exact `ros2-benchmark-container` and rclcpp targets
remain content-addressed outputs in the benchmark host's Docker daemon. They
are neither tagged as project releases nor pushed by the runtime publication
workflow.

## Scheduled Producer Boundary

The scheduled rclcpp workflow is a thin hosted producer around the same exact
target resolver, comparison coordinator, and version 2 completion graph used
locally. A versioned JSON profile pins the benchmark-container commit, suite,
duration, trial schedule, analysis resamples, and the explicit
non-authoritative label. The only moving input is the upstream Rolling branch,
which discovery resolves to a full commit before any build begins.

The producer runs the released CLI controller by immutable image digest. It
mounts the runner's Docker socket so the controller and the measured benchmark
remain siblings on one daemon rather than using Docker-in-Docker. Controller
path mappings translate result and cache paths back to the runner, and runtime
provenance verifies the exact controller image before measured output is
accepted.

Discovery reads the last successfully published candidate from a JSON file on
the `benchmark-state` branch through the GitHub API. An unchanged SHA ends the
workflow before dependency installation. Otherwise the state SHA becomes the
single reference and the newest Rolling SHA becomes the single candidate,
coalescing every missed commit into one experiment. On the first run, an exact
operator-provided bootstrap SHA takes precedence; without one, the candidate's
first parent is recorded as the bootstrap source.

The benchmark job has read-only repository permission and is the only job with
Docker access. Comparison outcomes 0, 1, and 2 represent completed evidence and
continue to packaging; outcomes 3 and 4 stop before publication or state
mutation. The full evidence artifact and compact dashboard artifact each add a
producer manifest and checksum list over their uploaded contents. The manifest
binds exact reference and candidate SHAs, profile, experiment and benchmark run
IDs, workflow run identity, comparison outcome, and the non-authoritative
notice.

State mutation is isolated in a default-branch-only job with `contents: write`.
It downloads the compact artifact, verifies every checksum and completed exit
code, derives the next state from that verified manifest, and updates the
transparent state branch. Workflow concurrency serializes discovery through
state advancement. There is no pull-request trigger, artifact retention is 14
days, and derived benchmark images remain runner-local and unpublished. The
off-hours trigger is additionally gated by an opt-in repository variable until
the manual integration pilot succeeds.

The history-serving boundary is separate from production and retention. A
versioned index lists active bundle paths in stable oldest-first order and
declares a bounded window. Each entry pins the digest of its checksum manifest
and the expected profile metadata. The loader never scans a directory or uses
modification times. It validates all indexed bundles before returning any,
checks each report only against its co-located dataset, rejects run and
Prometheus-series collisions, and renders the accepted window once before the
HTTP server starts. Compact report bundles retain their exact producer,
comparison, target, topology, and run identities; legacy dataset bundles are
labelled threshold-only and cannot carry report-backed evidence.

The remote publication boundary treats every local directory or archive as
untrusted. Safe extraction, producer validation, dataset/report binding, and a
prospective complete-history load happen before active state changes. An
interprocess lock serializes publishers. Accepted bundles move into
deterministic read-only directories and are never overwritten; the bounded
index is synced and atomically replaced only after every entry validates. A
service-manager-neutral executable hook reloads the cached exporter, whose
health response identifies the exact index SHA-256 it loaded. Prometheus must
also become healthy. Hook or health failure restores the previous index and
runs the hook again for rollback. GitHub Actions retrieval remains a separate
outbound-only adapter that feeds a temporary local ZIP through the same core
publication path.

The comparison workflow is a thin coordinator over the target resolver and
builder, immutable experiment runner, dataset builder, statistical comparison
engine, report validator, and dashboard command. It does not reimplement their
identity, scheduling, parsing, aggregation, statistical, or export policies.
Before persistent repository preparation it checks required executables, Docker
daemon and Buildx availability, native architecture support, disk space,
CPU-set syntax, and result-directory access. Compose and port checks are added
only when dashboard startup is requested. Dry-run resolution uses `git
ls-remote`, so it can print exact commits, image keys, trial order,
configuration, and output paths without cloning build contexts or publishing an
experiment.

During a real run, `workflow.status.json` and `workflow.log` retain the current
stage and operational failure. The immutable plan and local verified-target
manifests are published before trial execution. Existing image and trial
verification remains authoritative for resume; only incomplete work is retried.
After report generation, the workflow validates the experiment identity,
dataset checksum, report binding, both target keys, and local image manifests.
The version 2 `comparison.complete.json` is published last and checksums every
final contract artifact, leaving local files suitable for later CI upload
without conversion. Report reuse requires every stable completion field to
match the current plan, targets, experiment, dataset, report, status, and exit
outcome. Missing, damaged, or version 1 derived report chains are regenerated
deterministically from verified experiment evidence; the invalid marker is
removed before recovery begins.

The target key is a SHA-256 digest over the ROS distribution, architecture,
benchmark repository commit, client-library source and commit, and relevant
build configuration. The same key determines image and retained-container
identity. A matching name is not sufficient for reuse: labels, the manifest,
image ID, active rclcpp package prefix, and the benchmark executable's dynamic
library resolution are checked before execution.

Source-built rclcpp targets are resolved through a managed Git mirror and
immutable worktree. Optional exact vcstool manifests resolve additional Git
repositories into one content-addressed dependency workspace. The derived image
builds that workspace first, builds rclcpp over it, then rebuilds the benchmark
workspace. Dependency paths, URLs, and commits are part of the target identity;
two comparison targets must share them. Packaged targets retain the ROS
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
they start. Experiment completion version 2 binds the top-level measured
identity by path and checksum, and loading or resuming the bundle compares it
with the checksum-bound environment evidence of every measured trial. Warm-up
evidence remains outside that identity contract. Version 1 experiment
completion is regenerated only after the full version 2 environment and
artifact checks pass. Target image ID and digest, benchmark commit, executor,
duration, suite, and ROS distribution remain recorded with each trial as
evidence.

The statistical comparison boundary reads a controlled experiment rather than
its aggregate dataset. It verifies the experiment and trial completion graphs,
loads only measured records, reconstructs pairs from balanced schedule blocks,
and rejects incompatible provenance or coverage. A deterministic paired
bootstrap resamples whole blocks and retains the complete scenario scan in each
resample. Category decisions use the worst scenario, while overall evidence
uses the worst category-normalized scenario. The resulting versioned JSON report
binds completed evidence to the exact dashboard dataset SHA-256 while remaining
separate from Prometheus and Grafana formatting.

Calibration reuses the same preflight, exact target resolution, verified image,
balanced schedule, environment checks, trial completion, dataset, and immutable
resume boundaries. Its plan is explicitly marked `purpose: calibration`, which
is the only state in which both labelled streams may share a target key. The
normal comparison coordinator and statistical report builder continue to reject
that identity. Calibration output uses its own versioned report and completion
names, records per-KPI paired noise and individual threshold-crossing counts,
and has no overall verdict or dashboard command. Load averages and available
thermal-zone temperatures are observations rather than immutable host identity
fields because they naturally vary between trials.

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
