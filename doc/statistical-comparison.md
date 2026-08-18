# Repeat-Aware Statistical Comparison

`experiment compare` produces statistical evidence from a completed controlled
experiment without changing the dashboard's legacy point-estimate policy.

## Run The Comparison

Create a balanced experiment with at least three measured trials per target,
then compare its two plan labels:

```bash
ros2-performance-monitoring experiment compare <experiment-dir>
```

The default output is `<experiment-dir>/comparison-report.json`. Use `--output`
to write elsewhere. The reference and candidate default to the corresponding
plan labels; they can be reversed without rerunning the benchmark:

```bash
ros2-performance-monitoring experiment compare <experiment-dir> \
  --reference candidate \
  --candidate reference
```

The analysis controls are:

- `--confidence-level`: two-sided interval confidence level; default `0.95`.
- `--bootstrap-repeats`: number of paired resamples; default `10000`.
- `--seed`: deterministic bootstrap seed; default `0`.
- `--minimum-trials`: required measured pairs; default and minimum `3`. It may
  be raised for a stricter local policy but not lowered.

Changing an analysis control changes the report contents. Identical verified
input and identical controls produce byte-for-byte identical JSON.

## Eligible Evidence

The comparison loader first verifies `experiment.complete.json`, the immutable
plan checksum, the dataset completion manifest, every trial completion marker,
and every recorded trial file checksum. It then loads only records that are all
of the following:

- Planned as `measured` rather than `warmup`.
- Present in a completed, checksum-valid trial attempt.
- Marked as a measured record rather than a median aggregate.
- Part of a balanced sequence block containing exactly one selected reference
  trial and one selected candidate trial.

Failed, interrupted, missing, warm-up, unplanned, and aggregate records cannot
increase the statistical sample size. Interleaved schedules are recorded by the
experiment runner but are not eligible for this initial paired method. Missing
or malformed pairing is reported as `Cannot compare` rather than being treated
as independent samples.

Before calculating effects, the engine also requires compatible benchmark,
build, client-library, platform, ROS distribution, executor, measured-host, and
target provenance. Every pair must have the same scenario and metric coverage,
and coverage must remain complete across all measured blocks.

## Method

The report records the method as `paired-bootstrap-worst-scenario-v1`.

1. Each recorded balanced sequence is one paired trial block.
2. The point estimate for a metric compares the candidate and reference medians
   across the measured blocks.
3. Each bootstrap resample draws the same number of whole blocks with
   replacement. Reference and candidate values from a block always move
   together.
4. Metric effects are recalculated from the resampled medians. The same seeded
   block selection is used for every scenario, preserving relationships across
   the full scenario matrix.
5. Within each resample, category evidence takes the worst adverse metric and
   scenario. Overall evidence takes the worst scenario after dividing its
   effect by that category's regression threshold. Improvements therefore
   cannot cancel regressions, and the interval covers the complete scan rather
   than treating each scenario as an unrelated test.
6. The two-sided percentile interval is read from the resulting worst-case
   bootstrap distribution.

Latency and resource increases and throughput decreases are adverse relative
percentage changes. Lost, late, and too-late message increases are adverse
percentage-point changes. Reversing the selected targets reverses the sign and
directional interpretation of every effect.

The practical thresholds remain the same as the dashboard policy:

| Category | Possible regression | Regression |
| --- | ---: | ---: |
| Mean or p95 latency increase | 0.5% | 2% |
| Throughput decrease | 0.5% | 2% |
| Peak CPU or RSS increase | 1% | 5% |
| Reliability increase | 0.01 percentage points | 0.1 percentage points |

These thresholds describe practical impact. The confidence interval describes
measurement uncertainty; one is not substituted for the other.

## Evidence Rules

- `Regression`: the lower confidence bound of the scan-aware distribution is
  strictly greater than the practical regression threshold.
- `Possible regression`: evidence reaches a category's possible threshold but
  its lower bound does not support `Regression`. This includes a point estimate
  above the regression threshold with a wide interval.
- `No regression`: the upper confidence bound remains below the possible
  threshold.
- `Insufficient evidence`: fewer than the configured minimum measured pairs are
  available. Point estimates are not promoted to statistical verdicts.
- `Incomplete results`: a planned measured trial or required metric is missing,
  or metric coverage changes between blocks.
- `Cannot compare`: pairing, scenario coverage, target identity, or provenance
  is incompatible.
- `N/A`: the category does not apply, such as throughput and reliability for a
  service-only experiment.

The overall regression rule uses the lower bound of the worst normalized
scenario across all categories. Overall possible evidence also respects each
category's own possible threshold.

## Report Contract

`comparison-report.json` has `schema_version: 1` and contains:

- The experiment ID and complete selected target identities.
- Method, confidence level, bootstrap seed and repeat count, minimum and actual
  measured-pair counts, pairing policy, and point estimator.
- Overall and category statuses, thresholds, point estimates, confidence
  intervals, responsible scenarios, and responsible metrics.
- Per-scenario category evidence and each contributing metric's adverse
  direction, source unit, effect unit, threshold, estimate, interval, and
  status.

The report has no generation timestamp so deterministic input and controls
remain byte stable. Report schema changes require a schema-version increment;
method changes require a new method identifier.

## Exit Outcomes

The command always writes a valid report before returning one of these outcomes.
An experiment that fails completion verification is rejected without writing a
report.

| Exit code | Outcome |
| ---: | --- |
| `0` | No regression |
| `1` | Supported regression |
| `2` | Possible regression or insufficient evidence |
| `3` | Invalid, incomplete, not comparable, or not applicable comparison |

These codes let local automation consume the decision without parsing terminal
text. CI policy, dashboard rendering, hosted storage, and automatic pull-request
gating remain outside this feature.
