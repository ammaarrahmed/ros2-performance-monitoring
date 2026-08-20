# Controlled A/A Calibration

`experiment calibrate` measures one exact rclcpp target as two independently
scheduled streams. It estimates within-session benchmark noise and shows how
often individual unchanged-target KPI pairs cross the current practical
thresholds. It is calibration evidence, not a commit comparison, regression
verdict, release gate, or automatic policy update.

## Recommended Starting Profile

Start with the smaller service suite, two warm-ups per stream, ten measured
pairs, a dedicated CPU set, and a ten-second scenario duration:

```bash
ros2-performance-monitoring experiment calibrate \
  --target-ref <full-rclcpp-commit> \
  --ros-distro rolling \
  --suite service-rclcpp-minimal \
  --duration 10 \
  --warmups 2 \
  --repeats 10 \
  --cpuset-cpus 0-3 \
  --seed 42 \
  --bootstrap-seed 0 \
  --results-dir ./experiments/host-calibration
```

The defaults use two warm-ups and ten measured pairs, while duration, suite,
CPU set, and target remain explicit choices. Ten pairs are enough to start
examining a host profile, but they do not prove a false-positive rate or justify
changing thresholds. Repeat sessions under the same controls before making a
gating decision. A full-SHA target is best for a resumable workflow because a
branch or tag may move.

Use `--dry-run` first to resolve the commit, benchmark commit, image key, trial
order, and output paths without creating repositories, images, containers, or
artifacts. A real run uses the same disk, Docker, Buildx, architecture, result
directory, and CPU-set preflight as `experiment compare`.

## Controlled Host Checklist

Record the conditions before a session and keep them stable:

- Reserve the requested CPU set for the benchmark where the host permits it;
  avoid unrelated work on those CPUs.
- Keep the host on external power with a consistent performance or cooling
  profile. Allow temperatures and clocks to stabilize before measurement.
- Stop avoidable background builds, indexing, downloads, and containers.
- Keep the ROS distribution, RMW implementation, benchmark commit, executor,
  suite, duration, Docker version, kernel, architecture, and CPU set unchanged.
- Confirm the measured CPUs use the intended governor. The upstream runner may
  change the governor and does not restore an arbitrary original value.
- Note ambient, cooling, or system-load assumptions that the machine cannot
  expose reliably in software.

The bundle automatically records the exact rclcpp and benchmark identities,
verified image identity, ROS distribution, executor, suite, duration,
architecture, CPU model, kernel, Docker version, CPU set, and CPU governors.
Each measured trial also records load averages and any readable Linux thermal
zone temperatures. Missing thermal zones remain an empty observation rather
than a fabricated temperature. Load and temperature are not immutable resume
identity fields because they naturally move during a session; review their
ranges when interpreting the report.

## Scheduling And Samples

The immutable plan is marked `purpose: calibration`. Only that purpose permits
the `reference` and `candidate` stream labels to use the same target key. Normal
`experiment compare`, `experiment run`, and comparison-report analysis continue
to reject identical target identities.

Calibration always uses balanced execution blocks. Each sequence contains one
trial from each stream, and the first stream alternates between blocks. Warm-ups
exercise the same execution path but never enter the dataset or calibration
samples. Failed, interrupted, checksum-invalid, unplanned, and median aggregate
records are also excluded. A valid measured pair requires complete scenario and
metric coverage plus matching benchmark, client-library, platform, ROS,
executor, and host provenance.

Rerun the identical command to resume. The existing plan, target manifests,
trial files, environments, dataset, report, and completion chain are verified;
only incomplete or invalid work is regenerated. Changing the target, benchmark
commit, ROS distribution, suite, executor, duration, CPU set, warm-ups, repeats,
or scheduling seed requires a new result directory.

## Evidence Method

The report method is `paired-bootstrap-aa-calibration-v1`.

For every supported KPI and scenario, the report contains the two stream values
and adverse effect for each recorded pair. Latency and resource increases and
throughput decreases use relative percentages. Reliability increases use
percentage points. It also contains the paired-effect minimum, maximum, mean,
median, sample standard deviation, median-based point estimate, and deterministic
paired-bootstrap confidence interval. The bootstrap seed and repeat count are
configurable with `--bootstrap-seed` and `--bootstrap-repeats`.

Each observed paired effect is labelled against the current practical threshold
snapshot:

- `No regression`: below the category's possible threshold.
- `Possible regression`: at or above the possible threshold and not above the
  regression threshold.
- `Regression`: strictly above the regression threshold.

Per-KPI and report-wide counts show the fraction of these observed unchanged
pairs labelled `Possible regression` or `Regression`. This is a descriptive
within-session threshold-crossing rate, not the confidence-backed verdict rate
of repeated full comparison sessions. The report keeps the threshold snapshot
under `policy.practical_thresholds` and explicitly records that threshold
recommendations were not generated. Review evidence separately before proposing
any comparison-policy change.

## Output Contract And Exit Behavior

A successful bundle adds these files to the ordinary experiment artifacts:

```text
calibration/
  workflow.log
  workflow.status.json
  plan.json
  targets/reference.json
  targets/candidate.json
  measured_environment.json
  trials/...
  dataset/dashboard-data.jsonl
  dataset/dashboard-data.manifest.json
  experiment.complete.json
  calibration-report.json
  calibration.complete.json
```

`calibration-report.json` uses `schema_version: 1` and `report_type:
calibration`. It binds the dataset SHA-256, target and image provenance,
configuration, stable environment identity, measured-trial observations,
analysis controls, threshold snapshot, summary counts, and per-KPI evidence.
It has no generation timestamp, so verified inputs and analysis controls produce
byte-for-byte stable evidence. `calibration.complete.json` is published last and
checksums the plan, both target manifests, experiment completion, dataset,
dataset manifest, and calibration report.

The command returns `0` when valid calibration evidence is published, regardless
of threshold-crossing counts. Operational failures return `4`; command-line
usage errors retain argparse's `2`. Calibration output never returns comparison
codes `1`, `2`, or `3`. Its distinct schema is rejected by the comparison-report
validator and cannot be supplied through `--comparison-report` to Prometheus or
Grafana. Inspect the JSON directly or archive the complete local bundle for
later review.
