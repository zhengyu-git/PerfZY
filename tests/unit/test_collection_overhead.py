import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from perfzy.collectors.windows import WindowsCollector, _GpuProcessCounters
from test_android_collector import make_collector, TOP, MEMINFO, BATTERY


def test_android_memory_stays_out_of_app_dump_and_cpu_is_pid_scoped():
    collector = make_collector({"top": TOP, "dumpsys meminfo": MEMINFO}, {"app_cpu", "memory"})
    values = collector.sample(1).values
    assert values["memory"] == 600
    assert values["app_cpu"] is not None
    assert any("top -b -n 1 -p 1234" in call for call in collector.calls)
    assert any("meminfo --local com.example.game" in call for call in collector.calls)


def test_android_temperature_does_not_query_current():
    collector = make_collector({"dumpsys battery": BATTERY}, {"temperature"})
    assert collector.sample(1).values["temperature"] == 35.2
    assert not any("current_now" in call for call in collector.calls)


def test_android_deselecting_frames_disables_device_tracking():
    collector = make_collector({}, {"fps"})
    collector.activate_pending_sources()
    collector.set_enabled_metrics(frozenset({"memory"}))
    collector.activate_pending_sources()
    assert not collector._frame_tracking
    assert sum("--timestats -disable" in call for call in collector.calls) == 1
    collector.activate_pending_sources()
    assert sum("--timestats -disable" in call for call in collector.calls) == 1


def test_windows_gpu_memory_does_not_enumerate_engines(monkeypatch):
    counters = _GpuProcessCounters()
    counters._requested = frozenset({"gpu_memory"})
    enum = Mock(return_value=["target"])
    add = Mock(return_value=1)
    monkeypatch.setattr(counters, "_matching_instances", enum)
    monkeypatch.setattr(counters, "_add", add)
    counters._discover_and_add(frozenset({1}))
    enum.assert_called_once_with("GPU Process Memory", frozenset({1}))
    add.assert_called_once_with("gpu_memory", "GPU Process Memory", ["target"], "Dedicated Usage")


def test_windows_deselecting_frames_stops_etw(monkeypatch):
    collector = WindowsCollector()
    stop = Mock()
    monkeypatch.setattr(collector, "_terminate_presentmon", stop)
    collector._present_rows.put({"frame": 1})
    collector.set_enabled_metrics(frozenset({"memory"}))
    collector.activate_pending_sources()
    stop.assert_called_once()
    assert collector._present_rows.empty()


def test_windows_gpu_timing_downgrades_when_deselected(monkeypatch):
    from test_windows_collector import FakeProcess, FakePresentMon
    collector = WindowsCollector()
    collector._process = FakeProcess()
    old = FakePresentMon()
    collector._presentmon = old
    collector._presentmon_tracks_gpu = True
    collector._enabled = frozenset({"fps"})
    commands = []
    monkeypatch.setattr(collector, "_presentmon_path", lambda: "PresentMon.exe")
    monkeypatch.setattr(collector, "_stop_stale_etw_sessions", lambda: None)
    monkeypatch.setattr("perfzy.collectors.windows.subprocess.Popen", lambda command, **kwargs: commands.append(command) or FakePresentMon())
    collector._ensure_presentmon()
    assert old.terminated
    assert "--no_track_gpu" in commands[0]


def test_ios_battery_reuses_session_and_closes_on_cancellation(monkeypatch):
    from tools import ios_bridge as bridge
    calls = []
    class Client:
        async def close(self): calls.append("close")
    async def lockdown(udid): calls.append("connect"); return Client()
    class Diagnostics:
        def __init__(self, client): pass
        async def __aenter__(self): calls.append("open"); return self
        async def __aexit__(self, *args): calls.append("exit")
        async def ioregistry(self, **kwargs): calls.append("read"); return {"Voltage": 4000}
    async def sleep(seconds):
        if calls.count("read") >= 3: raise asyncio.CancelledError
    monkeypatch.setattr(bridge, "lockdown", lockdown)
    monkeypatch.setattr(bridge, "DiagnosticsService", Diagnostics)
    monkeypatch.setattr(bridge.asyncio, "sleep", sleep)
    monkeypatch.setattr(bridge, "emit", lambda row: None)
    async def run():
        try: await bridge.stream_battery("device", 1000)
        except asyncio.CancelledError: pass
    asyncio.run(run())
    assert calls == ["connect", "open", "read", "read", "read", "exit", "close"]


def test_android_gpu_memory_skips_unneeded_pss_dump():
    collector = make_collector({"dumpsys gpu": "Proc 1234 total: 104857600"}, {"app_gpu_memory"})
    assert collector.sample(1).values["app_gpu_memory"] == 100
    assert not any("meminfo" in call for call in collector.calls)


def test_android_gpu_memory_keeps_local_pss_fallback():
    collector = make_collector({"dumpsys meminfo": MEMINFO}, {"app_gpu_memory"})
    assert collector.sample(1).values["app_gpu_memory"] == 300
    assert any("meminfo --local" in call for call in collector.calls)
