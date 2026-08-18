# Repeat-Aware Statistical Comparison

The end-to-end `experiment compare` workflow produces statistical evidence from
a controlled experiment. `experiment report` exposes the same report stage for
an existing experiment bundle. A completed report can be supplied to the
exporter and dashboard; without one, the dashboard retains its legacy
point-estimate policy.

## Run The Comparison

Create a balanced experiment with at least three measured trials per target,
then compare its two plan labels:

```bash
ros2-performance-monitoring experiment report <experiment-dir>
```

The default output is `<experiment-dir>/comparison-report.json`. Use `--output`
to write elsewhere. The reference and candidate default to the corresponding
plan labels; they can be reversed without rerunning the benchmark:

```bash
ros2-performance-monitoring experiment report <experiment-dir> \
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

The comparison loader reads the immutable plan, checks the measured-environment
identity, and verifies every available trial completion marker and recorded file
checksum. When `experiment.complete.json` is present it is also verified, but
the report does not require the whole bundle to be complete: a failed planned
trial must remain visible as `Incomplete results`. The loader uses only records
that are all of the following:

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
6. The engine repeats that evaluation independently for each topology using
   only that topology's paired scenario metrics. Topology confidence intervals
   and verdicts are not copied from or filtered out of the final report-wide
   result.
7. The two-sided percentile interval is read from the resulting worst-case
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
  available. The report preserves the available pair count and known
  scenario/category coverage, but does not expose a point estimate, confidence
  interval, responsible metric, or confidence-backed verdict.
- `Incomplete results`: a planned measured trial or required metric is missing,
  or metric coverage changes between blocks. The report includes a reason but
  no estimate, interval, or responsible evidence.
- `Cannot compare`: pairing, scenario coverage, target identity, or provenance
  is incompatible. The report includes a reason but no estimate, interval, or
  responsible evidence.
- `N/A`: the category does not apply, such as throughput and reliability for a
  service-only experiment. It is not allowed as the overall result and does not
  participate in that result.

The overall regression rule uses the lower bound of the worst normalized
scenario across all categories. Overall possible evidence also respects each
category's own possible threshold. The report-wide result scans all applicable
scenarios, while each topology result scans only the applicable scenarios in
that topology. Consequently, Pub/Sub and Service regressions remain independent
in the dashboard. Service throughput and reliability are `N/A` and do not enter
the Service overall result.

## Report Contract

`comparison-report.json` has `schema_version: 3` and contains:

- The experiment ID, exact dataset SHA-256 binding, and complete selected target
  identities.
- Method, confidence level, bootstrap seed and repeat count, minimum and actual
  measured-pair counts, pairing policy, and point estimator.
- Overall and category statuses, thresholds, point estimates, confidence
  intervals, responsible scenarios, and responsible metrics.
- A `topologies` map containing independently calculated overall and category
  summaries for every topology represented by the scenario evidence.
- Per-scenario category evidence and each contributing metric's adverse
  direction, source unit, effect unit, threshold, estimate, interval, and
  status.

The report has no generation timestamp so deterministic input and controls
remain byte stable. Report schema changes require a schema-version increment;
method changes require a new method identifier.

All outcomes use the schema-v3 field model. Validation is state-aware:
decisive reports require the configured number of measured pairs and complete
scenario/metric evidence; insufficient reports require one or more pairs below
that minimum and retain coverage without statistical estimates; invalid or
incomplete outcomes require a reason and empty estimate fields. Thresholds must
match the category policy, and every responsible scenario and metric must refer
to evidence in the report and bound dataset. The comparison command validates
the report before publishing it, and the exporter uses the same validation
entry point before exposing it to Prometheus.

Schema v3 adds topology summaries to the report contract without changing the
method identifier. Schema-v2 reports do not contain that evidence and are no
longer accepted; regenerate them with `experiment compare` before exporting.

A report from a completed experiment contains the verified dataset checksum and
can be exported with:

```bash
ros2-performance-monitoring dashboard up \
  --input <experiment-dir>/dataset/dashboard-data.jsonl \
  --comparison-report <experiment-dir>/comparison-report.json
```

Reports produced while inspecting an unfinished bundle have no completed
dataset binding and remain useful as JSON/CLI evidence, but are rejected by the
exporter. Export validates the checksum, experiment identity, target
provenance, scenario coverage, method, evidence structure, and selected
aggregate runs. The report remains the source of truth for statuses; Prometheus
and Grafana do not reproduce the bootstrap calculation. For mixed reports, the
exporter labels the report-wide summary `topology="all"` and the scoped summaries
with their concrete topology. Single-topology reports export only the concrete
topology summary to avoid duplicate series.

## Exit Outcomes

The command writes a valid report before returning one of these outcomes. A
malformed plan or environment record is rejected without writing a report;
missing or failed planned trials instead produce an `Incomplete results` report.

| Exit code | Outcome |
| ---: | --- |
| `0` | No regression |
| `1` | Supported regression |
| `2` | Possible regression or insufficient evidence |
| `3` | Invalid, incomplete, not comparable, or not applicable comparison |
| `4` | Operational failure while loading, validating, or writing evidence |

These codes let local automation consume the decision without parsing terminal
text. The end-to-end command additionally returns `4` for operational failures
such as preflight, source resolution, image build, trial execution, dataset
publication, final validation, or dashboard startup. CI policy, hosted storage,
and automatic pull-request gating remain outside this feature.
