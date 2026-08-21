# Remote Dashboard Publication

The dashboard publisher turns one completed compact comparison bundle into a
bounded, verified active history on a generic Linux host. The local publisher
does not know how the bundle arrived. A directory, ZIP archive, or TAR archive
is accepted through the same command:

```bash
ros2-performance-monitoring dashboard publish \
  --bundle /var/spool/ros2-dashboard/rclcpp-dashboard.zip \
  --profile .github/benchmark-profiles/rolling-workflow-smoke-v2.json \
  --deployment-root /srv/ros2-performance-monitoring/dashboard \
  --history-limit 10 \
  --inactive-retention 20 \
  --restart-hook /usr/local/libexec/ros2-performance-restart-dashboard
```

The command does not provision the host, choose a domain, open a management
port, or require a provider SDK. Bundle paths, history and retention limits,
the hook, health endpoints, and audit path are command options.

## Transaction and Validation Contract

Publication performs these operations in order:

1. Copy or safely extract the source into a deployment-local staging directory.
   Archive paths must be relative and unique. Symbolic links, hard links,
   devices, traversal, absolute paths, duplicate members, oversized archives,
   and files outside the compact dashboard contract are rejected.
2. Validate the producer manifest, all checksums, producer profile, completed
   comparison exit code, exact commits, dataset manifest and normalized schema,
   comparison-report schema and dataset binding, experiment identity, outcome,
   run IDs, and the complete active-history window.
3. Acquire an exclusive Linux interprocess lock and repeat validation against
   the current index. The accepted bundle is moved into a deterministic,
   read-only directory below `bundles/`; an existing directory is never
   overwritten.
4. Write and sync a candidate index, validate every referenced bundle, then
   atomically replace `active-history.json`.
5. Run the optional executable hook. The hook receives
   `ROS2_PERFORMANCE_ACTIVE_HISTORY` in its environment. Shell command strings
   are deliberately not evaluated.
6. Wait for the exporter and Prometheus health endpoints. The exporter must
   report the SHA-256 of the exact active index it loaded, so an old exporter
   process cannot pass the check. Hook or health failure atomically restores the
   previous index and invokes the hook again for rollback.
7. Append a mode `0600` JSONL audit record, then prune only inactive publisher-
   owned bundles beyond the configured retention count.

The source directory or archive is retained by default. Add `--delete-source`
only when the operator explicitly wants successful local publication to remove
that source evidence. Active bundles are never removed by inactive retention.

Delivery is idempotent by both GitHub repository/run ID and experiment ID. A
repeat of an active identity records an `idempotent` audit outcome without a
restart. Reuse of either identity with conflicting commits, run IDs, outcome,
or profile is rejected. An accepted bundle left inactive after a health
rollback can be delivered again to retry activation.

The deployment layout is:

```text
/srv/ros2-performance-monitoring/dashboard/
  active-history.json
  publication-audit.jsonl
  .publish.lock
  .staging/
  bundles/
    rolling-workflow-smoke-v2-<candidate>-<run>-<identity>/
```

Point the long-running exporter at
`/srv/ros2-performance-monitoring/dashboard/active-history.json`. For the
repository container stack, its environment file can contain:

```bash
ROS2_PERFORMANCE_DASHBOARD_DATA_DIR=/srv/ros2-performance-monitoring/dashboard
ROS2_PERFORMANCE_HISTORY_INDEX_PATH=/data/active-history.json
```

The example hook in
`deployment/dashboard-publisher/restart-dashboard-compose` recreates the cached
exporter and Prometheus after activation or rollback, including on the first
deployment. The publisher independently checks both services afterward.

## Pulling GitHub Actions Artifacts

`dashboard pull-github` is a separate pull-based adapter. It discovers the
latest successfully completed run of a configured workflow, downloads exactly
one unexpired artifact matching the prefix, writes a temporary local ZIP, and
passes that path to `dashboard publish`:

```bash
ros2-performance-monitoring dashboard pull-github \
  --repository owner/ros2-performance-monitoring \
  --workflow scheduled-rclcpp-comparison.yml \
  --artifact-prefix rclcpp-dashboard- \
  --token-file /etc/ros2-performance-monitoring/github-actions.token \
  --profile /opt/ros2-performance-monitoring/.github/benchmark-profiles/rolling-workflow-smoke-v2.json \
  --deployment-root /srv/ros2-performance-monitoring/dashboard \
  --restart-hook /usr/local/libexec/ros2-performance-restart-dashboard
```

Use `--run-id` to pin one completed successful workflow run instead of selecting
the latest. `--github-api-url` can select a GitHub Enterprise API endpoint. The
adapter needs outbound HTTPS only. Give its fine-grained token
read-only repository Metadata and Actions access. Store only the token in the
token file, make the file owned by the service account, and set mode `0600`:

```bash
sudo install -o ros2-dashboard -g ros2-dashboard -m 0600 \
  ./github-actions.token \
  /etc/ros2-performance-monitoring/github-actions.token
```

Authorization headers and token contents are not included in exceptions or
audit records. No credential is stored in GitHub Actions, the deployment index,
or committed configuration.

## Generic systemd Example

Tracked examples are under `deployment/dashboard-publisher/`:

- `publisher.env.example` contains non-secret paths and endpoint settings.
- `restart-dashboard-compose` is an executable hook for the repository's
  container dashboard stack.
- `ros2-dashboard-publisher.service` runs the pull adapter as an unprivileged
  user with a read-only host filesystem except for the deployment root.
- `ros2-dashboard-publisher.timer` performs a bounded outbound poll every 15
  minutes. Repeated delivery is harmless because the publisher is idempotent.

Copy and edit these examples for the host rather than committing machine
addresses, domains, credentials, or provider resource identifiers. The example
hook needs Docker access; membership in the Docker group is effectively root
access and should be limited to this dedicated service account. A typical
installation is:

```bash
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin \
  --groups docker ros2-dashboard
sudo install -d -o ros2-dashboard -g ros2-dashboard -m 0750 \
  /srv/ros2-performance-monitoring/dashboard
sudo install -d -o root -g root -m 0755 \
  /etc/ros2-performance-monitoring /usr/local/libexec
sudo install -o root -g root -m 0644 \
  deployment/dashboard-publisher/publisher.env.example \
  /etc/ros2-performance-monitoring/publisher.env
sudo install -o root -g root -m 0755 \
  deployment/dashboard-publisher/restart-dashboard-compose \
  /usr/local/libexec/ros2-performance-restart-dashboard
sudo install -o root -g root -m 0644 \
  deployment/dashboard-publisher/ros2-dashboard-publisher.service \
  deployment/dashboard-publisher/ros2-dashboard-publisher.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ros2-dashboard-publisher.timer
```

Test one explicit run before enabling the timer, then inspect
`active-history.json`, `publication-audit.jsonl`, the exporter `/healthz`, and
Prometheus `/-/healthy`.
