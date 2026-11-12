import builtins
import runpy
import sys
import threading
from pathlib import Path

import pytest
from perfzy import automation as a
from perfzy.domain.models import Device, Platform, Target, Sample


class Collector:
    stops = 0
    fail = False
    def start(self, *args): pass
    def sample(self, elapsed):
        if self.fail: raise OSError("disconnected")
        return Sample(elapsed, {"app_cpu": 10})
    def stop(self): self.stops += 1


def start_api(tmp_path, monkeypatch, collector=None):
    c = collector or Collector()
    monkeypatch.setattr(a, "WindowsCollector", lambda: c)
    api = a.PerfZY(tmp_path)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "A", 1)
    api.start(metrics=["app_cpu"], screenshots=False)
    return api, c


def test_switch_game_rejected_before_discovery(tmp_path, monkeypatch):
    api, c = start_api(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(a, "discover_targets", lambda *args, **kwargs: pytest.fail("must not discover"))
        with pytest.raises(a.PerfZYError, match="不能切换游戏"):
            api.select_game("B")
        assert api.game.process_id == 1
    finally: api.stop()


def test_failed_save_can_retry_and_timer_stays_frozen(tmp_path, monkeypatch):
    api, c = start_api(tmp_path, monkeypatch)
    api._stop_event.set()
    api._thread.join(timeout=5)
    with monkeypatch.context() as m:
        def fail(*args, **kwargs): raise OSError("disk full")
        m.setattr(a, "save_session_from_spool", fail)
        m.setattr(a, "save_session", fail)
        with pytest.raises(a.PerfZYError, match="保存采集记录失败"): api.stop("retry")
    duration = api.elapsed
    assert api.collecting and api._session_spool is not None
    with monkeypatch.context() as m:
        m.setattr(a.time, "monotonic", lambda: api._started + 1000)
        assert api.elapsed == duration
        result = api.stop("retry")
        assert result.duration == duration
        assert api.elapsed == duration
    assert result.session_path.is_file()
    assert c.stops == 1
    assert api.stop() == result
    assert not api.collecting


def test_worker_failure_freezes_time_before_delayed_stop(tmp_path, monkeypatch):
    c = Collector(); c.fail = True
    api, c = start_api(tmp_path, monkeypatch, c)
    api._thread.join(timeout=5)
    assert not api._thread.is_alive()
    duration = api.elapsed
    with monkeypatch.context() as m:
        m.setattr(a.time, "monotonic", lambda: api._started + 1000)
        with pytest.raises(a.PerfZYError, match="已保存现有数据"): api.stop()
        assert api._last_result.duration == duration
        assert api.elapsed == duration
    # The next capture resets the frozen clock.
    c.fail = False
    api.start(metrics=["app_cpu"], screenshots=False)
    assert api._stopped_elapsed is None
    api.stop()


def test_bridge_import_error_does_not_show_modal(tmp_path, monkeypatch):
    import ctypes
    from perfzy import runtime_paths
    monkeypatch.setattr(runtime_paths, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["PerfZY.exe", "--ios-bridge", "preflight"])
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *args: pytest.fail("bridge must not show a modal"))
    original = builtins.__import__
    def broken(name, *args, **kwargs):
        if name == "tools.ios_bridge": raise ImportError("bridge dependency missing")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ImportError, match="bridge dependency missing"):
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "app.py"), run_name="__main__")
    assert "bridge dependency missing" in (tmp_path / "data/startup-error.log").read_text(encoding="utf-8")
