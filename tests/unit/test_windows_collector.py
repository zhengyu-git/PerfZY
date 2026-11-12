"""Guards on Windows frame accounting and the PresentMon lifecycle.

Enabling a frame metric after collection had begun used to leave FPS silently
empty for the rest of the session, because PresentMon was only ever started from
``start``. The frame filter also walked the whole process tree once per frame.
"""

import time
from collections import namedtuple
from types import SimpleNamespace

import pytest

from perfzy.collectors.windows import (FRAME_EVENT_GRACE_SECONDS, FRAME_METRICS,
                                                WindowsCollector, _GpuProcessCounters)


TARGET_PID = 4242


def test_missing_presentmon_fails_before_start(monkeypatch):
    collector = WindowsCollector()
    monkeypatch.setattr(collector, "_presentmon_path", lambda: None)
    with pytest.raises(RuntimeError, match="未找到 PresentMon"):
        collector.start(None, None, frozenset({"fps"}))
    assert collector._process is None


class FakeProcess:
    """Stands in for psutil.Process, counting how often the tree is walked."""

    def __init__(self, children=()):
        self.children_calls = 0
        self._children = tuple(children)

    def children(self, recursive=False):
        self.children_calls += 1
        return self._children

    def cpu_times(self):
        return SimpleNamespace(user=0.0, system=0.0)

    def is_running(self):
        return True


class FakeChild:
    def __init__(self, pid):
        self.pid = pid


class FakePresentMon:
    def __init__(self, returncode=None):
        self._returncode = returncode
        self.stdout = None
        self.stderr = None
        self.terminated = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        return self._returncode

    def kill(self):
        self.terminate()


def frame_row(frame_time, pid=TARGET_PID, application="game.exe"):
    return {"ProcessID": str(pid), "Application": application,
            "msBetweenPresents": str(frame_time)}


def v2_frame_row(frame_time, displayed="16.67", **metrics):
    row = {"ProcessID": str(TARGET_PID), "Application": "game.exe",
           "FrameTime": str(frame_time), "DisplayedTime": displayed}
    row.update({key: str(value) for key, value in metrics.items()})
    return row


def make_collector(rows=(), enabled=FRAME_METRICS, children=()):
    collector = WindowsCollector()
    collector._process = FakeProcess(children)
    collector._target_pid = TARGET_PID
    collector._target_process_name = "game"
    collector._enabled = frozenset(enabled)
    collector._presentmon_tracks_gpu = True
    collector._presentmon = FakePresentMon()
    collector._presentmon_started_at = time.monotonic()
    for row in rows:
        collector._present_rows.put(row)
    return collector


@pytest.mark.parametrize("enabled", [{"fps"}, {"available_memory"}, {"app_cpu"}])
def test_exited_target_stops_even_system_only_sampling(enabled):
    collector = make_collector(enabled=enabled)
    collector._process.is_running = lambda: False
    with pytest.raises(RuntimeError, match="目标游戏进程已退出"):
        collector.sample(1)
    collector.stop()


def test_target_exiting_during_sample_does_not_return_partial_sample():
    collector = make_collector(enabled=set())
    checks = iter([True, False])
    collector._process.is_running = lambda: next(checks)
    with pytest.raises(RuntimeError, match="目标游戏进程已退出"):
        collector.sample(1)
    collector.stop()


def test_frame_times_become_fps_and_jank_buckets():
    collector = make_collector([frame_row(16.0), frame_row(20.0),
                                frame_row(40.0), frame_row(60.0)])
    values = collector.sample(1.0).values
    assert values["frame_time"] == pytest.approx(34.0)
    assert values["fps"] == pytest.approx(1000.0 / 34.0)
    assert values["small_jank"] == 1.0
    assert values["jank"] == 1.0
    assert values["big_jank"] == 1.0


def test_v2_frame_rows_produce_tail_drop_cpu_and_gpu_metrics():
    rows = [
        v2_frame_row(10, CPUBusy=5, CPUWait=5, GPUTime=8, GPUBusy=6,
                     GPUWait=2, GPULatency=1),
        v2_frame_row(20, CPUBusy=8, CPUWait=12, GPUTime=16, GPUBusy=12,
                     GPUWait=4, GPULatency=2),
        v2_frame_row(30, CPUBusy=9, CPUWait=21, GPUTime=21, GPUBusy=15,
                     GPUWait=6, GPULatency=3),
        v2_frame_row(40, displayed="NA", CPUBusy=10, CPUWait=30, GPUTime=30,
                     GPUBusy=20, GPUWait=10, GPULatency=4),
    ]
    values = make_collector(rows).sample(1.0).values

    assert values["frame_time_p95"] == pytest.approx(38.5)
    assert values["frame_time_p99"] == pytest.approx(39.7)
    assert values["fps_1_low"] == pytest.approx(25.0)
    assert values["fps_0_1_low"] == pytest.approx(25.0)
    assert values["jank_rate"] == pytest.approx(25.0)
    assert values["dropped_frames"] == 1.0
    assert values["dropped_frame_rate"] == pytest.approx(25.0)
    assert values["cpu_frame_busy"] == pytest.approx(8.0)
    assert values["cpu_frame_wait"] == pytest.approx(17.0)
    assert values["gpu_time"] == pytest.approx(18.75)
    assert values["gpu_busy"] == pytest.approx(13.25)
    assert values["gpu_wait"] == pytest.approx(5.5)
    assert values["gpu_latency"] == pytest.approx(2.5)


def test_fps_is_capped_for_implausibly_short_frames():
    collector = make_collector([frame_row(0.0005)])
    assert collector.sample(1.0).values["fps"] == 1000.0


def test_complete_long_frames_are_retained_and_flagged_for_review():
    collector = make_collector([frame_row(9000.0), frame_row(20.0)])
    sample = collector.sample(1.0)
    assert sample.values['frame_time'] == pytest.approx(4510.0)
    assert sample.raw_values['long_frame_max_ms'] == 9000
    assert sample.adjustments['frame_time'] == 'long_complete_frame_needs_review'
    assert len(sample.frames) == 2


def test_absurd_frame_times_are_discarded():
    collector = make_collector([frame_row(1e12), frame_row(20.0)])
    assert collector.sample(1.0).values['frame_time'] == pytest.approx(20.0)


def test_only_selected_frame_metrics_are_reported():
    collector = make_collector([frame_row(20.0)], enabled={"fps"})
    values = collector.sample(1.0).values
    assert "fps" in values
    assert "frame_time" not in values
    assert "jank" not in values


def test_frames_from_an_unrelated_process_are_ignored():
    collector = make_collector([frame_row(20.0, pid=999999, application="browser.exe")])
    assert collector.sample(1.0).values.get("fps") is None


def test_frames_from_a_child_process_are_counted():
    collector = make_collector([frame_row(20.0, pid=5555, application="renderer.exe")],
                              children=[FakeChild(5555)])
    assert collector.sample(1.0).values["fps"] == pytest.approx(50.0)


def test_same_executable_in_unrelated_pid_is_not_counted():
    collector = make_collector([frame_row(20.0, pid=999999, application="GAME.EXE")])
    assert 'fps' not in collector.sample(1.0).values


def test_malformed_rows_do_not_abort_the_sample():
    rows = [{"ProcessID": "not-a-pid", "msBetweenPresents": "20.0"},
            {"ProcessID": str(TARGET_PID), "msBetweenPresents": ""},
            frame_row(20.0)]
    collector = make_collector(rows)
    assert collector.sample(1.0).values["fps"] == pytest.approx(50.0)


def test_process_tree_is_walked_once_per_sample():
    collector = make_collector([frame_row(16.0 + index) for index in range(120)])
    collector.sample(1.0)
    assert collector._process.children_calls == 1


def test_process_tree_refreshes_even_in_empty_frame_window_to_discover_new_render_children():
    collector = make_collector()
    collector.sample(1.0)
    assert collector._process.children_calls == 1


def test_present_queue_drops_the_oldest_row_instead_of_growing_forever():
    collector = WindowsCollector()
    collector._present_rows = __import__("queue").Queue(maxsize=2)
    collector._enqueue_present_row(frame_row(10.0))
    collector._enqueue_present_row(frame_row(20.0))
    collector._enqueue_present_row(frame_row(30.0))

    assert collector._present_rows.qsize() == 2
    assert float(collector._present_rows.get_nowait()["msBetweenPresents"]) == 20.0


def test_enabling_a_frame_metric_midway_starts_presentmon(monkeypatch):
    collector = WindowsCollector()
    collector._process = FakeProcess()
    starts = []
    monkeypatch.setattr(collector, "_ensure_presentmon", lambda: starts.append(1))

    collector.set_enabled_metrics(frozenset({"app_cpu"}))
    collector.activate_pending_sources()
    assert starts == []

    collector.set_enabled_metrics(frozenset({"app_cpu", "fps"}))
    collector.activate_pending_sources()
    assert starts == [1]


def test_presentmon_is_not_started_twice():
    collector = WindowsCollector()
    collector._process = FakeProcess()
    collector._presentmon = FakePresentMon()
    collector._enabled = frozenset({"fps"})
    collector._presentmon_tracks_display = True
    collector._presentmon_tracks_gpu = True
    collector.activate_pending_sources()
    assert isinstance(collector._presentmon, FakePresentMon)


def test_presentmon_profile_is_stable_when_metric_selection_changes(monkeypatch):
    from test_dxgi_etw import FakeDxgi
    monkeypatch.setattr('perfzy.collectors.windows_frame_source.DxgiPresentSource',FakeDxgi)
    commands = []
    monkeypatch.setattr(WindowsCollector, "_presentmon_path", staticmethod(lambda: "PresentMon.exe"))
    monkeypatch.setattr(WindowsCollector, "_stop_stale_etw_sessions", staticmethod(lambda: None))
    monkeypatch.setattr("perfzy.collectors.windows.subprocess.Popen",
                        lambda command, **_kwargs: commands.append(command) or FakePresentMon())

    collector = WindowsCollector()
    collector._process = FakeProcess()
    collector._enabled = frozenset({"fps"})
    collector._ensure_presentmon()
    assert "--v2_metrics" in commands[-1]
    assert "--no_track_gpu" not in commands[-1]
    assert "--no_track_display" not in commands[-1]
    assert "--exclude_dropped" not in commands[-1]

    collector._enabled = frozenset({"fps", "gpu_busy"})
    collector._ensure_presentmon()
    assert "--no_track_gpu" not in commands[-1]
    assert "--no_track_display" not in commands[-1]
    assert len(commands) == 1


def test_application_only_rows_do_not_fabricate_zero_display_drops():
    collector = make_collector([frame_row(16.67)], enabled={"fps","dropped_frames","dropped_frame_rate"})
    collector._presentmon_tracks_display = False
    collector._capture_profile = 'application_present'
    result = collector.sample(1)
    assert result.values['fps'] == pytest.approx(59.988,abs=.01)
    assert 'dropped_frames' not in result.values
    assert 'dropped_frame_rate' not in result.values
    assert result.frames[0]['dropped'] is None
    assert collector.diagnostics()['mode'] == 'application_present'



def test_missing_presentmon_is_reported_instead_of_failing_silently(monkeypatch):
    monkeypatch.setattr(WindowsCollector, "_presentmon_path", staticmethod(lambda: ""))
    collector = WindowsCollector()
    collector._process = FakeProcess()
    collector._enabled = frozenset({"fps"})
    collector.activate_pending_sources()
    assert "PresentMon" in collector.status


def test_a_pending_frame_source_is_reported_rather_than_looking_idle():
    collector = make_collector(enabled={"fps"})
    collector._presentmon = None
    collector.sample(1.0)
    assert collector.status


def test_frame_grace_period_is_measured_from_the_presentmon_start():
    collector = make_collector(enabled={"fps"})
    collector.sample(600.0)
    assert collector.status == ""

    collector._presentmon_started_at = time.monotonic() - FRAME_EVENT_GRACE_SECONDS - 1
    collector.sample(601.0)
    assert "ETW" in collector.status


def test_frame_arrival_clears_a_stale_warning():
    collector = make_collector(enabled={"fps"})
    collector._presentmon_started_at = time.monotonic() - FRAME_EVENT_GRACE_SECONDS - 1
    collector.sample(20.0)
    assert "ETW" in collector.status

    collector._present_rows.put(frame_row(20.0))
    collector.sample(21.0)
    assert collector.status == ""


def test_lost_etw_events_are_explained():
    collector = make_collector(enabled={"fps"})
    collector._presentmon_stderr.append("WARNING: 1234 events were lost")
    assert "丢失" in collector.status


def test_access_denied_points_at_elevation():
    collector = make_collector(enabled={"fps"})
    collector._presentmon_stderr.append("error: Access denied")
    assert "管理员" in collector.status


def test_stop_clears_the_frame_source_state():
    collector = make_collector([frame_row(20.0)])
    collector.sample(1.0)
    collector.stop()
    assert collector._presentmon is None
    assert collector._present_frame_count == 0
    assert collector._enabled == frozenset()
    assert collector._presentmon_started_at == 0.0


def test_process_user_and_kernel_cpu_are_normalized_to_machine_capacity(monkeypatch):
    CpuTimes = namedtuple("CpuTimes", "user system")
    process = FakeProcess()
    process.cpu_times = lambda: CpuTimes(2.0, 1.5)
    collector = make_collector(enabled={"app_cpu", "app_user_cpu", "app_kernel_cpu"})
    collector._process = process
    collector._cpus = 4
    collector._process_cpu_active = True
    collector._last_cpu_times = CpuTimes(1.0, 1.0)
    collector._last_cpu_time = 10.0
    monkeypatch.setattr("perfzy.collectors.windows.time.monotonic", lambda: 11.0)

    values = collector.sample(1.0).values
    assert values["app_user_cpu"] == pytest.approx(25.0)
    assert values["app_kernel_cpu"] == pytest.approx(12.5)
    assert values["app_cpu"] == pytest.approx(37.5)


def test_per_core_cpu_uses_raw_time_deltas(monkeypatch):
    CpuTimes = namedtuple("CpuTimes", "user system idle interrupt dpc")
    old = [CpuTimes(1, 1, 8, 0, 0)]
    current = [CpuTimes(2, 2, 16, 0, 0)]
    collector = make_collector(enabled={"cpu_core_0_usage"})
    collector._per_core_active = True
    collector._last_per_cpu_times = old
    monkeypatch.setattr("perfzy.collectors.windows.psutil.cpu_times",
                        lambda percpu=False: current)

    assert collector.sample(1.0).values["cpu_core_0_usage"] == pytest.approx(20.0)


def test_page_fault_and_iops_are_rates_not_lifetime_totals(monkeypatch):
    old_io = SimpleNamespace(read_bytes=1024, write_bytes=2048, read_count=10, write_count=20)
    new_io = SimpleNamespace(read_bytes=5120, write_bytes=10240, read_count=18, write_count=32)
    process = FakeProcess()
    process.memory_info = lambda: SimpleNamespace(num_page_faults=140)
    process.io_counters = lambda: new_io
    collector = make_collector(enabled={"page_faults", "disk_read_iops", "disk_write_iops"})
    collector._process = process
    collector._page_fault_active = True
    collector._last_page_faults = 100
    collector._last_page_fault_time = 8.0
    collector._io_active = True
    collector._last_io = old_io
    collector._last_io_time = 8.0
    monkeypatch.setattr("perfzy.collectors.windows.time.monotonic", lambda: 10.0)

    values = collector.sample(2.0).values
    assert values["page_faults"] == pytest.approx(20.0)
    assert values["disk_read_iops"] == pytest.approx(4.0)
    assert values["disk_write_iops"] == pytest.approx(6.0)


def test_gpu_process_counters_sum_target_instances_and_warm_up_usage(monkeypatch):
    class FakePdh:
        PERF_DETAIL_WIZARD = 1
        PDH_FMT_LARGE = 2
        PDH_FMT_DOUBLE = 3

        @staticmethod
        def OpenQuery(): return object()
        @staticmethod
        def CloseQuery(_query): pass
        @staticmethod
        def CollectQueryData(_query): pass
        @staticmethod
        def EnumObjectItems(_machine, _source, object_name, _detail):
            suffix = "eng_0_engtype_3D" if object_name == "GPU Engine" else "phys_0"
            return [], [f"pid_{TARGET_PID}_luid_0x1_{suffix}", "pid_9999_luid_0x1_phys_0"]
        @staticmethod
        def MakeCounterPath(parts): return parts
        @staticmethod
        def AddCounter(_query, path): return path
        @staticmethod
        def GetFormattedCounterValue(handle, _fmt):
            counter = handle[-1]
            value = {"Dedicated Usage": 100 * 1048576,
                     "Shared Usage": 50 * 1048576,
                     "Utilization Percentage": 42.0}[counter]
            return 0, value

    monkeypatch.setattr("perfzy.collectors.windows.win32pdh", FakePdh)
    counters = _GpuProcessCounters()
    enabled = frozenset({"app_gpu_usage", "gpu_memory", "shared_gpu_memory"})

    first = counters.sample({TARGET_PID}, enabled)
    second = counters.sample({TARGET_PID}, enabled)
    assert first == {"gpu_memory": 100.0, "shared_gpu_memory": 50.0}
    assert second["app_gpu_usage"] == 42.0


def test_gpu_engine_created_after_collection_start_is_discovered(monkeypatch):
    state = {"now": 0.0, "engine": False}

    class LateGpuPdh:
        PERF_DETAIL_WIZARD = 1
        PDH_FMT_LARGE = 2
        PDH_FMT_DOUBLE = 3

        @staticmethod
        def OpenQuery(): return object()
        @staticmethod
        def CloseQuery(_query): pass
        @staticmethod
        def CollectQueryData(_query): pass
        @staticmethod
        def EnumObjectItems(_machine, _source, object_name, _detail):
            if object_name == "GPU Engine" and not state["engine"]:
                return [], []
            suffix = "eng_0_engtype_3D" if object_name == "GPU Engine" else "phys_0"
            return [], [f"pid_{TARGET_PID}_luid_0x1_{suffix}"]
        @staticmethod
        def MakeCounterPath(parts): return parts
        @staticmethod
        def AddCounter(_query, path): return path
        @staticmethod
        def GetFormattedCounterValue(handle, _fmt):
            value = 35.0 if handle[-1] == "Utilization Percentage" else 1048576.0
            return 0, value

    monkeypatch.setattr("perfzy.collectors.windows.win32pdh", LateGpuPdh)
    monkeypatch.setattr("perfzy.collectors.windows.time.monotonic",
                        lambda: state["now"])
    counters = _GpuProcessCounters()
    enabled = frozenset({"app_gpu_usage", "gpu_memory"})

    assert "app_gpu_usage" not in counters.sample({TARGET_PID}, enabled)
    state.update(now=6.0, engine=True)
    assert "app_gpu_usage" not in counters.sample({TARGET_PID}, enabled)
    state["now"] = 7.0
    assert counters.sample({TARGET_PID}, enabled)["app_gpu_usage"] == 35.0
