^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package ros2_performance_monitoring
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.1.2 (2026-08-22)
------------------
* Fixed statistical active-history entries to retain and revalidate the full
  pinned producer profile, including exact dependency and benchmark contracts.
* Fixed hosted Rolling artifact publication to preserve checksummed hidden
  benchmark configurations and use supported Node 24 workflow actions.
* Activated exact Rolling dependency snapshots in the scheduled comparison
  producer and fixed its dependency manifest path evaluation.

0.1.1 (2026-08-21)
------------------
* Added provider-neutral transactional dashboard publication with safe archive
  extraction, immutable accepted bundles, locked atomic history activation,
  idempotency, configurable retention and reload hooks, exact-index health
  rollback, audit records, a pull-based GitHub Actions adapter, and generic
  Linux deployment examples.
* Added exact source dependency snapshots for source-built rclcpp targets,
  including strict vcstool manifest validation, shared comparison provenance,
  managed checkouts, and dependency-first image builds.
* Updated the scheduled rclcpp producer to run through an immutable published
  controller image instead of installing the repository checkout at runtime.

0.1.0 (2026-08-20)
------------------
* Added a versioned, explicitly ordered active-history index for bounded,
  checksum-verified comparison bundles, atomic startup validation, cached
  multi-bundle metrics, profile authority metadata, and bundle-scoped Grafana
  selection.
* Added a guarded latest-versus-last-successful rclcpp comparison producer with
  a pinned non-authoritative smoke profile, durable transparent state, and
  checksum-bound full and dashboard artifacts.
* Added versioned, digest-pinnable CLI and exporter image publication with a
  strict release-version contract, pre-push smoke tests, OCI metadata, SBOMs,
  provenance attestations, immutable tags, and release digest reporting.
* Added a container-first workflow with shared CLI and exporter image targets,
  explicit controller-to-daemon path mapping, non-root ownership and socket
  access, verified controller provenance, and a complete Compose dashboard.
* Added a controlled, resumable A/A calibration workflow with same-commit
  balanced trial streams, versioned noise evidence, environment observations,
  and non-gating exit semantics.
* Hardened resumable experiment and comparison bundles with version 2
  completion manifests, measured-environment checksums, complete resume
  validation, and deterministic derived-report recovery.
* Added one-command per-commit rclcpp comparison orchestration with focused
  preflight, non-persistent dry runs, safe resume, cross-artifact validation,
  operational logs, and a checksum-bound completion manifest.
* Added independently calculated Pub/Sub and Service statistical summaries for
  mixed benchmark reports and topology-matched dashboard evidence.
* Added dataset-bound statistical comparison export and Grafana evidence views,
  with strict report validation and threshold-only fallback labelling.
* Added deterministic paired-bootstrap regression reports for completed
  experiments, with scan-aware confidence intervals and documented CLI exit
  outcomes.
* Added immutable, resumable experiment bundles with balanced repeated trials,
  automatic warm-up exclusion, environment evidence, and checksum-verified
  completion state.
* Made dataset publication crash-consistent with a dataset checksum and a
  manifest written last as the completion marker.
* Added exact rclcpp source resolution, content-addressed benchmark images, and
  pre-run provenance verification.
* Added validated, deterministic comparison dataset creation with optional
  median aggregation.
* Added local Prometheus exporter and Grafana dashboard support for normalized pub/sub metrics.
* Initial repository scaffold.
* Contributors: Ammaar Ahmed
