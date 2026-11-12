import subprocess
from types import SimpleNamespace

import pytest

from perfzy.automation import PerfZY, PerfZYError
from perfzy.collectors import discovery, ios_runtime
from perfzy.domain.models import Device, Platform


@pytest.mark.parametrize("stdout,stderr,code,message", [
    ("", "ModuleNotFoundError: win32security", 1, "win32security"),
    ("bad json", "", 0, "JSON"),
    ("", "", 0, "JSON"),
    ('{}', "", 0, "设备列表"),
])
def test_strict_scan_exposes_errors(monkeypatch, stdout, stderr, code, message):
    monkeypatch.setattr(ios_runtime.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code))
    with pytest.raises(RuntimeError, match=message):
        ios_runtime.run_json("devices", strict=True)


@pytest.mark.parametrize("error", [OSError("cannot launch"), subprocess.TimeoutExpired("bridge", 18)])
def test_launch_errors_preserve_legacy_behavior(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(ios_runtime.subprocess, "run", fail)
    assert ios_runtime.run_json("devices") == []
    with pytest.raises(RuntimeError, match="启动失败或超时"):
        ios_runtime.run_json("devices", strict=True)


def test_sdk_keeps_other_devices_and_clears_recovered_error(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "discover_windows", lambda: [Device("windows:local", Platform.WINDOWS, "PC")])
    monkeypatch.setattr(discovery, "discover_android", lambda: [])
    def fail(*args, **kwargs):
        raise RuntimeError("missing win32security")
    monkeypatch.setattr(discovery, "run_ios_json", fail)
    with PerfZY(tmp_path) as api:
        assert api.refresh_devices()[0].platform is Platform.WINDOWS
        assert "win32security" in api.discovery_errors["ios"]
        with pytest.raises(PerfZYError, match="win32security"):
            api.connect_device(platform="ios")
        monkeypatch.setattr(discovery, "run_ios_json", lambda *a, **k: [])
        api.refresh_devices()
        assert api.discovery_errors == {}
        with pytest.raises(PerfZYError, match="没有找到设备：ios"):
            api.connect_device(platform="ios")


def test_successful_empty_scan_is_not_error(monkeypatch):
    monkeypatch.setattr(ios_runtime.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="[]", stderr="", returncode=0))
    assert ios_runtime.run_json("devices", strict=True) == []
