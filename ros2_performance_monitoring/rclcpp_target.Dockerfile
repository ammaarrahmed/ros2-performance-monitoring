# syntax=docker/dockerfile:1
# Copyright 2026 Ammaar Ahmed
# Licensed under the Apache License, Version 2.0

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG ROS_DISTRO
ARG CMAKE_BUILD_TYPE=Release
ARG TARGET_MANIFEST_B64

COPY --from=benchmark . /ws/src/ros2_benchmark_container/benchmark
COPY --from=rclcpp . /target_ws/src/rclcpp

RUN rm -f /target_ws/src/rclcpp/.git \
    && rosdep install \
      --from-paths /target_ws/src \
      --ignore-src \
      --rosdistro "${ROS_DISTRO}" \
      -y \
    && /bin/bash -c \
      "source /opt/ros/${ROS_DISTRO}/setup.bash; \
       colcon build \
         --merge-install \
         --base-paths /target_ws/src \
         --build-base /target_ws/build \
         --install-base /target_ws/install \
         --cmake-args -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}" \
    && rm -rf /ws/build /ws/install /ws/log \
    && /bin/bash -c \
      "source /opt/ros/${ROS_DISTRO}/setup.bash; \
       source /target_ws/install/setup.bash; \
       colcon build \
         --merge-install \
         --base-paths /ws/src \
         --build-base /ws/build \
         --install-base /ws/install \
         --cmake-args -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}" \
    && mkdir -p /etc/ros2-performance-monitoring \
    && printf '%s' "${TARGET_MANIFEST_B64}" \
      | base64 --decode > /etc/ros2-performance-monitoring/target-manifest.json

ENV RCLCPP_TARGET_PREFIX=/target_ws/install
