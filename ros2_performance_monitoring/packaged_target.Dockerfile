# Copyright 2026 Ammaar Ahmed
# Licensed under the Apache License, Version 2.0

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG ROS_DISTRO
ARG TARGET_MANIFEST_B64
ENV RCLCPP_TARGET_PREFIX=/opt/ros/${ROS_DISTRO}

RUN mkdir -p /etc/ros2-performance-monitoring \
    && printf '%s' "${TARGET_MANIFEST_B64}" \
      | base64 --decode > /etc/ros2-performance-monitoring/target-manifest.json
