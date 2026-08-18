^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package ros2_performance_monitoring
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.0.0 (unreleased)
------------------
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
