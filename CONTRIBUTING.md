# Contributing

This repository is intended to follow ROS 2 development practices so that it can
remain easy to review, test, and potentially move under ROS 2 governance later.

## Development Expectations

Please follow the ROS 2 developer guide:

https://docs.ros.org/en/rolling/The-ROS2-Project/Contributing/Developer-Guide.html

In practice, this means:

- Keep pull requests small and focused.
- Prefer one clear behavior change per pull request.
- Include tests or explain why a change is documentation-only.
- Run the local test suite before opening a pull request.
- Keep commits reviewable and avoid mixing unrelated changes.
- Follow ROS 2 style guidance for Python, documentation, package metadata, and
  copyright headers.
- Follow the applicable ROS 2 and OSRF guidance for disclosing generative AI use
  in commits, pull requests, and generated content.

## Local Checks

From a ROS 2 workspace containing this package:

```bash
colcon test --packages-select ros2_performance_monitoring
colcon test-result --verbose
```

The initial scaffold only contains lint tests. Functional tests will be added
with the parser, exporter, and dashboard pull requests.

## Dependency Policy

This package does not vendor benchmark engines. In particular:

- `ros2-performance` is treated as an external benchmark framework.
- `ros2-benchmark-container` is treated as an external benchmark runner and
  artifact producer.
- This repository consumes result artifacts produced by external tools instead
  of copying their source code or adding them as git submodules.

