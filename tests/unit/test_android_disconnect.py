from types import SimpleNamespace
import subprocess

import pytest

from perfzy.collectors import android


def test_timeouts_are_consecutive_per_command(monkeypatch):
    collector = android.AndroidCollector()
    collector._adb = "adb"
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("adb", 3)
    monkeypatch.setattr(android.subprocess, "run", timeout)
    collector._shell("dumpsys", "meminfo")
    collector._shell("dumpsys", "meminfo")
    assert not collector._connection_error
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="ok", stderr="", returncode=0))
    collector._shell("dumpsys", "meminfo")
    monkeypatch.setattr(android.subprocess, "run", timeout)
    collector._shell("dumpsys", "meminfo")
    collector._shell("dumpsys", "meminfo")
    assert not collector._connection_error
    collector._shell("dumpsys", "meminfo")
    with pytest.raises(RuntimeError, match="连续超时 3 次"):
        collector.sample(1)


def test_missing_adb_fails_before_creating_workers():
    collector = android.AndroidCollector()
    collector._adb = ""
    with pytest.raises(RuntimeError, match="未找到 adb"):
        collector.start(None, None, frozenset())
    assert collector._sample_executor is None


@pytest.mark.parametrize("message", [
    "adb.exe: device '1234' not found", "error: device offline",
    "error: device unauthorized.", "error: no devices/emulators found",
])
def test_transport_failure_stops_sampling(monkeypatch, message):
    collector = android.AndroidCollector()
    collector._adb = "adb"
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="", stderr=message, returncode=1))
    assert collector._shell("pidof", "game") == ""
    with pytest.raises(RuntimeError, match="ADB 连接"):
        collector.sample(1)
    collector.stop()  # cleanup must not raise the latched transport error


@pytest.mark.parametrize("message", ["Permission denied", "Can't find service: gpu"])
def test_optional_counter_failure_is_not_disconnect(monkeypatch, message):
    collector = android.AndroidCollector()
    collector._adb = "adb"
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="", stderr=message, returncode=1))
    assert collector._shell("dumpsys", "gpu") == ""
    assert collector._connection_error == ""


def test_disconnect_during_sample_does_not_return_fake_values(monkeypatch):
    collector = android.AndroidCollector()
    collector._adb = "adb"
    collector._enabled = frozenset({"memory"})
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="", stderr="error: device offline", returncode=1))
    with pytest.raises(RuntimeError, match="ADB 连接"):
        collector.sample(1)
