# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322
ARG DOCKER_CLI_IMAGE=docker:29.1.3-cli@sha256:4fa0ee1f3a7e4354c4ea34558b6d4ee32859baf4973d4c8ccc8e7fe3dd730c04

FROM ${PYTHON_IMAGE} AS wheel

WORKDIR /source
COPY setup.py setup.cfg package.xml ./
COPY resource ./resource
COPY ros2_performance_monitoring ./ros2_performance_monitoring
COPY compose.dashboard.yml ./
COPY compose.yml Dockerfile requirements-container.txt ./
COPY config ./config
COPY doc ./doc
COPY grafana ./grafana
RUN python -m pip wheel --no-deps --wheel-dir /wheels .


FROM ${DOCKER_CLI_IMAGE} AS cli

ARG PROJECT_VERSION=0.0.0
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="ROS 2 Performance Monitoring CLI" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/ammaarrahmed/ros2-performance-monitoring"

RUN apk add --no-cache git python3 py3-pip && \
    python3 -m venv /opt/ros2-performance-monitoring
COPY requirements-container.txt /tmp/requirements-container.txt
COPY --from=wheel /wheels /wheels
RUN /opt/ros2-performance-monitoring/bin/python -m pip install \
      --no-cache-dir -r /tmp/requirements-container.txt && \
    /opt/ros2-performance-monitoring/bin/python -m pip install \
      --no-cache-dir --no-deps /wheels/*.whl && \
    rm -rf /tmp/requirements-container.txt /wheels

RUN addgroup -g 10001 controller && \
    adduser -D -u 10001 -G controller -h /home/controller controller && \
    install -d -o controller -g controller /cache /results /workspace
ENV PATH="/opt/ros2-performance-monitoring/bin:${PATH}" \
    HOME=/cache/home \
    PYTHONUNBUFFERED=1
WORKDIR /workspace
USER controller
ENTRYPOINT ["ros2-performance-monitoring"]
CMD ["help"]


FROM ${PYTHON_IMAGE} AS exporter

ARG PROJECT_VERSION=0.0.0
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="ROS 2 Performance Monitoring Exporter" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/ammaarrahmed/ros2-performance-monitoring"

RUN python -m venv /opt/ros2-performance-monitoring
COPY --from=wheel /wheels /wheels
RUN /opt/ros2-performance-monitoring/bin/python -m pip install \
      --no-cache-dir --no-deps /wheels/*.whl && \
    rm -rf /wheels && \
    addgroup -g 10001 exporter && \
    adduser -D -u 10001 -G exporter -h /home/exporter exporter && \
    install -d -o exporter -g exporter /data
ENV PATH="/opt/ros2-performance-monitoring/bin:${PATH}" \
    HOME=/home/exporter \
    PYTHONUNBUFFERED=1
EXPOSE 9108
USER exporter
ENTRYPOINT ["ros2-performance-exporter"]
