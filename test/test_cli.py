import sys
import importlib

import ros2_performance_monitoring.cli as cli
from ros2_performance_monitoring.config import RunDefaults

def test_run_command_prints_message(monkeypatch,capsys):
    importlib.reload(cli)
    monkeypatch.setattr(sys,"argv",["ros2-performance-monitoring","run","60"])
    cli.main()
    captured=capsys.readouterr()
    assert "Running Performance Monitor..." in captured.out

def test_doctor_command(monkeypatch,capsys):
    importlib.reload(cli)
    monkeypatch.setattr(sys,"argv",["ros2-perfomance-monitoring","doctor"])
    cli.main()
    captured=capsys.readouterr()
    assert "Checking environment..." in captured.out

def test_run_uses_config_defaults(monkeypatch):
    importlib.reload(cli)
    defaults=RunDefaults()
    received={}

    def fake_run(args):
        received["args"]=args
    
    monkeypatch.setattr(cli,"run_command",fake_run)
    monkeypatch.setattr(sys,"argv",["ros2-performance-monitoring","run",str(defaults.duration)])
    cli.main()
    assert received["args"].ros_distro==defaults.ros_distro
    assert received["args"].executor==defaults.executor
    assert received["args"].container_repo_url==defaults.container_repo_url
    assert received["args"].container_ref==defaults.container_ref