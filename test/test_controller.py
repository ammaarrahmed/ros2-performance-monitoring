# Copyright 2026 Ammaar Ahmed
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json

import pytest
import ros2_performance_monitoring.controller as controller


def test_host_context_preserves_existing_path_and_owner_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(controller.os, 'getuid', lambda: 123)
    monkeypatch.setattr(controller.os, 'getgid', lambda: 456)

    context = controller.controller_context({})

    assert context.mode == 'host'
    assert (context.host_uid, context.host_gid) == (123, 456)
    assert context.resolve_results(tmp_path / 'result') == tmp_path / 'result'
    assert context.daemon_results_path(tmp_path / 'result') == tmp_path / 'result'


def test_container_context_maps_absolute_relative_and_spaced_paths(tmp_path):
    controller_results = tmp_path / 'controller results'
    host_results = tmp_path / 'host results'
    controller_cache = tmp_path / 'controller cache'
    host_cache = tmp_path / 'host cache'
    values = _container_environment(
        controller_results,
        host_results,
        controller_cache,
        host_cache,
    )

    context = controller.controller_context(values)

    assert context.resolve_results('trial one') == controller_results / 'trial one'
    assert context.daemon_results_path('trial one') == host_results / 'trial one'
    assert context.daemon_results_path(
        controller_results / 'absolute trial'
    ) == host_results / 'absolute trial'
    assert context.resolve_cache('benchmark') == controller_cache / 'benchmark'
    assert (context.host_uid, context.host_gid) == (1001, 1002)


def test_container_context_rejects_paths_outside_declared_mount(tmp_path):
    context = controller.controller_context(_container_environment(
        tmp_path / 'results',
        tmp_path / 'host-results',
        tmp_path / 'cache',
        tmp_path / 'host-cache',
    ))

    with pytest.raises(controller.ControllerConfigurationError, match='outside'):
        context.resolve_results(tmp_path / 'different')


def test_container_context_requires_complete_absolute_mapping(tmp_path):
    values = _container_environment(
        tmp_path / 'results',
        tmp_path / 'host-results',
        tmp_path / 'cache',
        tmp_path / 'host-cache',
    )
    del values[controller.HOST_CACHE_ROOT_ENV]

    with pytest.raises(
        controller.ControllerConfigurationError,
        match=controller.HOST_CACHE_ROOT_ENV,
    ):
        controller.controller_context(values)


def test_container_image_identity_comes_from_docker_inspection(monkeypatch):
    image_id = f'sha256:{"a" * 64}'
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ['container', 'inspect']:
            payload = [{
                'Image': image_id,
                'Config': {'Image': 'controller:test'},
            }]
        else:
            payload = [{
                'Id': image_id,
                'RepoDigests': ['controller@sha256:digest'],
                'Config': {
                    'Labels': {'org.opencontainers.image.revision': 'abc123'},
                },
            }]
        return argparse.Namespace(stdout=json.dumps(payload))

    monkeypatch.setenv('HOSTNAME', 'container-id')
    monkeypatch.setenv(controller.CONTROLLER_IMAGE_ENV, 'controller:test')
    monkeypatch.setattr(controller.subprocess, 'run', fake_run)

    assert controller._controller_image_identity() == {
        'reference': 'controller:test',
        'id': image_id,
        'digest': 'sha256:digest',
        'revision': 'abc123',
    }
    assert calls == [
        ['docker', 'container', 'inspect', 'container-id'],
        ['docker', 'image', 'inspect', image_id],
    ]


def test_container_image_identity_rejects_claimed_reference(monkeypatch):
    monkeypatch.setenv('HOSTNAME', 'container-id')
    monkeypatch.setenv(controller.CONTROLLER_IMAGE_ENV, 'claimed:test')
    monkeypatch.setattr(
        controller.subprocess,
        'run',
        lambda *args, **kwargs: argparse.Namespace(stdout=json.dumps([{
            'Image': f'sha256:{"a" * 64}',
            'Config': {'Image': 'actual:test'},
        }])),
    )

    with pytest.raises(RuntimeError, match='actual:test'):
        controller._controller_image_identity()


def test_docker_server_identity_requires_complete_inspected_data(monkeypatch):
    monkeypatch.setattr(
        controller.subprocess,
        'run',
        lambda *args, **kwargs: argparse.Namespace(stdout=json.dumps({
            'ID': 'daemon-id',
            'Name': 'benchmark-host',
            'ServerVersion': '27.5.1',
            'OperatingSystem': 'Linux',
            'Architecture': 'x86_64',
            'DockerRootDir': '/var/lib/docker',
        })),
    )

    assert controller.docker_server_identity()['id'] == 'daemon-id'


def _container_environment(controller_results, host_results, controller_cache, host_cache):
    return {
        controller.CONTROLLER_MODE_ENV: 'container',
        controller.CONTROLLER_RESULTS_ROOT_ENV: str(controller_results),
        controller.HOST_RESULTS_ROOT_ENV: str(host_results),
        controller.CONTROLLER_CACHE_ROOT_ENV: str(controller_cache),
        controller.HOST_CACHE_ROOT_ENV: str(host_cache),
        controller.HOST_UID_ENV: '1001',
        controller.HOST_GID_ENV: '1002',
    }
