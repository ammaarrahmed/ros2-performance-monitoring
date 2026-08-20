^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package ros2_performance_monitoring
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.1.0 (unreleased)
------------------
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
