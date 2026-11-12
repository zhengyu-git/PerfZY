"""Guards on the iOS Instruments payload conversion and FPS smoothing.

The DVT counters arrive with inconsistent names, scales and units depending on
the iOS version, and CoreAnimation occasionally overshoots a stable frame rate.
"""

import pytest
from types import SimpleNamespace
from perfzy.collectors import ios


def test_preflight_dependency_error_prevents_stream_start(monkeypatch):
    collector = IOSCollector()
    def fail(*args, **kwargs):
        assert args == ("preflight",)
        raise RuntimeError("missing win32security")
    monkeypatch.setattr(ios, "run_json", fail)
    with pytest.raises(RuntimeError, match="win32security"):
        collector.start(None, None, frozenset())
    assert collector._process is None

from perfzy.collectors.ios import IOSCollector, _process_activity_state


def make_collector(enabled, process=None, system=None, graphics=None, battery=None):
    collector = IOSCollector()
    collector._enabled = frozenset(enabled)
    collector._latest_process = dict(process or {})
    collector._latest_system = dict(system or {})
    collector._latest_graphics = dict(graphics or {})
    collector._latest_battery = dict(battery or {})
    for source in collector._field_received_at:
        collector._field_received_at[source] = dict.fromkeys(
            getattr(collector, f"_latest_{source}"), ios.time.monotonic())
    return collector


def test_silent_stream_cannot_replay_cached_metrics_forever(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ios.time, "monotonic", lambda: now[0])
    collector = make_collector({"app_cpu"})
    collector._accept_stream_event({"type": "sysmon", "process": {"cpuUsage": 25}})
    assert collector.sample(1).values["app_cpu"] == 25
    now[0] = 104.9
    assert collector.sample(2).values["app_cpu"] == 25
    now[0] = 105.0
    collector._accept_stream_event({"type": "battery", "data": {"CurrentCapacity": 80}})
    with pytest.raises(RuntimeError, match="5 秒"):
        collector.sample(3)
    collector._accept_stream_event({"type": "sysmon", "process": {"cpuUsage": 26}})
    assert collector.sample(4).values["app_cpu"] == 26


@pytest.mark.parametrize("code", [0, 1])
def test_any_bridge_exit_stops_cached_samples(code):
    collector = make_collector({"app_cpu"}, process={"cpuUsage": 25})
    collector._process = SimpleNamespace(poll=lambda: code)
    with pytest.raises(RuntimeError, match="断开"):
        collector.sample(1)


def test_closed_stdout_detected_even_before_process_exit():
    collector = make_collector({"app_cpu"})
    collector._process = SimpleNamespace(poll=lambda: None)
    collector._stream_closed.set()
    with pytest.raises(RuntimeError, match="断开"):
        collector.sample(1)


def test_frame_rate_and_frame_time():
    collector = make_collector({"fps", "frame_time"}, graphics={"framesPerSecond": 60.0})
    values = collector.sample(1.0).values
    assert values["fps"] == pytest.approx(60.0)
    assert values["frame_time"] == pytest.approx(1000.0 / 60.0)


def test_attaching_artefacts_are_not_turned_into_frame_time_spikes():
    collector = make_collector({"fps", "frame_time"}, graphics={"framesPerSecond": 1.0})
    values = collector.sample(1.0).values
    assert values["fps"] is None
    assert values["frame_time"] is None


def test_counter_names_are_matched_loosely():
    collector = make_collector({"fps"}, graphics={"CoreAnimation.FramesPerSecond": 30.0})
    assert collector.sample(1.0).values["fps"] == pytest.approx(30.0)


def test_sub_one_percent_cpu_is_not_misread_as_a_fraction():
    collector = make_collector({"app_cpu"}, process={"cpuUsage": 0.42})
    assert collector.sample(1.0).values["app_cpu"] == pytest.approx(0.42)


def test_percent_cpu_usage_is_left_alone():
    collector = make_collector({"app_cpu"}, process={"cpuUsage": 42.0})
    assert collector.sample(1.0).values["app_cpu"] == pytest.approx(42.0)


def test_total_cpu_is_divided_by_the_core_count():
    collector = make_collector({"total_cpu"},
                               system={"SystemCPUUsage.CPU_TotalLoad": 480.0, "CPUCount": 6})
    assert collector.sample(1.0).values["total_cpu"] == pytest.approx(80.0)


def test_malformed_total_cpu_counter_does_not_abort_the_sample():
    collector = make_collector(
        {"total_cpu", "app_cpu"},
        process={"cpuUsage": 25.0},
        system={"SystemCPUUsage.CPU_TotalLoad": "not-a-number"})
    values = collector.sample(1.0).values
    assert values["total_cpu"] is None
    assert values["app_cpu"] == pytest.approx(25.0)


def test_memory_counters_are_converted_to_megabytes():
    collector = make_collector(
        {"ios_physical_footprint", "app_rss", "ios_private_memory",
         "ios_compressed_memory", "ios_virtual_memory"},
        process={"physFootprint": 512 * 1048576, "memResidentSize": 600 * 1048576,
                 "memRPrvt": 480 * 1048576, "memCompressed": 32 * 1048576,
                 "memVirtualSize": 4096 * 1048576})
    values = collector.sample(1.0).values
    assert values["ios_physical_footprint"] == pytest.approx(512.0)
    assert values["app_rss"] == pytest.approx(600.0)
    assert values["ios_private_memory"] == pytest.approx(480.0)
    assert values["ios_compressed_memory"] == pytest.approx(32.0)
    assert values["ios_virtual_memory"] == pytest.approx(4096.0)


def test_gpu_utilisation_is_clamped():
    collector = make_collector({"gpu"}, graphics={"deviceUtilization": 140.0})
    assert collector.sample(1.0).values["gpu"] == pytest.approx(100.0)


def test_per_core_usage_is_read_by_index():
    collector = make_collector({"cpu_core_0_usage", "cpu_core_1_usage"},
                               system={"PerCPUUsage.0.CPU_TotalLoad": 42.0})
    values = collector.sample(1.0).values
    assert values["cpu_core_0_usage"] == pytest.approx(42.0)
    assert values["cpu_core_1_usage"] is None


def test_battery_readings_are_converted_to_display_units():
    collector = make_collector({"battery_level", "battery_current", "battery_voltage",
                                "temperature", "power"},
                               battery={"CurrentCapacity": 87, "InstantAmperage": -500,
                                        "Voltage": 4200, "Temperature": 3012})
    values = collector.sample(1.0).values
    assert values["battery_level"] == pytest.approx(87.0)
    assert values["battery_current"] == pytest.approx(500.0)
    assert values["battery_voltage"] == pytest.approx(4.2)
    assert values["temperature"] == pytest.approx(30.12)
    assert values["power"] == pytest.approx(0.5 * 4.2)


def test_disk_throughput_needs_two_samples(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ios.time, "monotonic", lambda: now[0])
    collector = make_collector({"disk_read", "disk_write"},
                               process={"diskBytesRead": 1024, "diskBytesWritten": 2048})
    first = collector.sample(1.0).values
    assert first["disk_read"] is None

    now[0] += 1
    collector._accept_stream_event({"type": "sysmon", "process": {
        "diskBytesRead": 1024 + 4096, "diskBytesWritten": 2048 + 8192}})
    values = collector.sample(2.0).values
    assert values["disk_read"] == pytest.approx(4.0)
    assert values["disk_write"] == pytest.approx(8.0)


def test_process_counters_become_rates(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ios.time, "monotonic", lambda: now[0])
    collector = make_collector({"ios_context_switches", "ios_interrupt_wakeups"},
                               process={"ctxSwitch": 1000, "intWakeups": 200})
    collector.sample(1.0)
    now[0] += 1
    collector._accept_stream_event({"type": "sysmon", "process": {"ctxSwitch": 3000, "intWakeups": 700}})
    values = collector.sample(2.0).values
    assert values["ios_context_switches"] == pytest.approx(2000.0)
    assert values["ios_interrupt_wakeups"] == pytest.approx(500.0)


def test_boolean_counters_are_not_mistaken_for_numbers():
    collector = make_collector({"app_cpu"}, process={"cpuUsage": True})
    assert collector.sample(1.0).values["app_cpu"] is None


def test_only_selected_metrics_are_reported():
    collector = make_collector({"fps"}, graphics={"framesPerSecond": 60.0},
                               process={"cpuUsage": 42.0})
    assert set(collector.sample(1.0).values) == {"fps"}


def test_partial_and_empty_stream_events_keep_the_last_valid_snapshot():
    collector = IOSCollector()
    collector._enabled = frozenset({"fps", "gpu", "total_cpu", "cpu_core_0_usage",
                                    "battery_level"})
    collector._accept_stream_event({
        "type": "sysmon",
        "system": {"SystemCPUUsage.CPU_TotalLoad": 30.0,
                   "PerCPUUsage.0.CPU_TotalLoad": 20.0},
        "process": {"cpuUsage": 10.0},
    })
    collector._accept_stream_event({
        "type": "graphics",
        "data": {"framesPerSecond": 60.0, "deviceUtilization": 40.0},
    })
    collector._accept_stream_event({"type": "battery", "data": {"CurrentCapacity": 88}})

    # DVT can alternate empty and partial channel updates. None of them should
    # erase unrelated values that are still current on the same live stream.
    collector._accept_stream_event({"type": "sysmon", "system": {}, "process": None})
    collector._accept_stream_event({"type": "graphics", "data": {}})
    collector._accept_stream_event({"type": "graphics", "data": {"deviceUtilization": 45.0}})
    collector._accept_stream_event({"type": "battery", "data": {}})

    values = collector.sample(1.0).values
    assert values["fps"] == pytest.approx(60.0)
    assert values["gpu"] == pytest.approx(45.0)
    assert values["total_cpu"] == pytest.approx(30.0)
    assert values["cpu_core_0_usage"] == pytest.approx(20.0)
    assert values["battery_level"] == pytest.approx(88.0)


def _stream_sample(collector, elapsed, cpu, fps, gpu=40.0):
    collector._accept_stream_event({
        "type": "sysmon",
        "system": {},
        "process": {"cpuUsage": cpu},
    })
    collector._accept_stream_event({
        "type": "graphics",
        "data": {"framesPerSecond": fps, "deviceUtilization": gpu,
                 "Renderer Utilization %": gpu, "Tiler Utilization %": gpu / 2,
                 "Alloc system memory": 128 * 1048576},
    })
    return collector.sample(elapsed).values


def test_background_system_fps_is_not_attributed_to_the_game():
    collector = IOSCollector()
    collector._enabled = frozenset({"fps", "frame_time", "gpu", "ios_gpu_renderer",
                                    "ios_gpu_tiler", "ios_gpu_allocated_memory"})
    for elapsed in range(1, 6):
        values = _stream_sample(collector, elapsed, 80.0, 30.0)
    assert values["fps"] == pytest.approx(30.0)

    # App CPU collapses while the device-wide CoreAnimation stream moves to
    # SpringBoard's 60 Hz composition.  The first high point is smoothed to the
    # game baseline and the second makes all global graphics counters invalid.
    first = _stream_sample(collector, 6.0, 2.0, 60.0)
    second = _stream_sample(collector, 7.0, 3.0, 60.0)
    assert first["fps"] == pytest.approx(30.0)
    assert second["fps"] is None
    assert second["frame_time"] is None
    assert second["gpu"] is None
    assert second["ios_gpu_renderer"] is None
    assert second["ios_gpu_tiler"] is None
    assert second["ios_gpu_allocated_memory"] is None
    assert "后台" in collector.status


def test_two_missing_target_rows_pause_graphics_but_one_transient_miss_does_not():
    collector = IOSCollector()
    collector._enabled = frozenset({"fps"})
    for elapsed in range(1, 5):
        _stream_sample(collector, elapsed, 40.0, 30.0)

    collector._accept_stream_event({"type": "sysmon", "system": {}, "process": None})
    assert collector.sample(5.0).values["fps"] == pytest.approx(30.0)
    collector._accept_stream_event({"type": "sysmon", "system": {}, "process": None})
    assert collector.sample(6.0).values["fps"] is None


def test_graphics_resume_immediately_when_target_process_returns():
    collector = IOSCollector()
    collector._enabled = frozenset({"fps"})
    for elapsed in range(1, 5):
        _stream_sample(collector, elapsed, 70.0, 30.0)
    collector._accept_stream_event({"type": "sysmon", "system": {}, "process": None})
    collector._accept_stream_event({"type": "sysmon", "system": {}, "process": None})
    collector._accept_stream_event({"type": "graphics", "data": {"framesPerSecond": 60.0}})
    assert collector.sample(5.0).values["fps"] is None

    resumed = _stream_sample(collector, 6.0, 65.0, 30.0)
    assert resumed["fps"] == pytest.approx(30.0)
    assert collector.status == "正在采集 iOS 真机指标"


def test_foreground_refresh_rate_change_is_still_accepted():
    collector = IOSCollector()
    collector._enabled = frozenset({"fps"})
    result = []
    for elapsed, fps in enumerate([30, 30, 30, 30, 60, 60, 60], start=1):
        result.append(_stream_sample(collector, elapsed, 60.0, fps)["fps"])
    assert result[-1] == pytest.approx(60.0)


def test_explicit_process_suspension_states_are_recognized():
    assert _process_activity_state({"isSuspended": True}) is False
    assert _process_activity_state({"processState": "Background Suspended"}) is False
    assert _process_activity_state({"executionState": "Foreground Active"}) is True
    assert _process_activity_state({"someFutureField": 1}) is None


def _feed(collector, readings):
    return [collector._stabilize_fps(value) for value in readings]


def test_a_single_upward_spike_is_suppressed():
    collector = IOSCollector()
    result = _feed(collector, [60, 60, 60, 60, 120, 60])
    assert result == [60, 60, 60, 60, 60, 60]


def test_a_sustained_refresh_rate_change_is_accepted():
    collector = IOSCollector()
    result = _feed(collector, [60, 60, 60, 60, 120, 120, 120])
    assert result[-1] == 120


def test_frame_rate_drops_are_never_suppressed():
    collector = IOSCollector()
    result = _feed(collector, [60, 60, 60, 60, 20])
    assert result[-1] == 20


def test_a_telemetry_gap_discards_pending_spike_candidates():
    collector = IOSCollector()
    _feed(collector, [60, 60, 60, 60])
    _feed(collector, [120, 120])
    assert collector._stabilize_fps(None) is None
    assert collector._fps_high_candidates == []
    # The run of high readings has to start over rather than count as a switch.
    assert collector._stabilize_fps(120) == 60


def test_sample_raises_when_the_bridge_has_exited():
    class DeadBridge:
        stderr = None

        def poll(self):
            return 1

    collector = make_collector({"fps"})
    collector._process = DeadBridge()
    collector._error = "IOS_ERROR: developer mode is not enabled"
    with pytest.raises(RuntimeError, match="开发者模式"):
        collector.sample(1.0)


def test_trust_failures_are_explained_in_plain_language():
    assert "信任" in IOSCollector._friendly_error("Failed to pair with device")
    assert "开发者模式" in IOSCollector._friendly_error("Developer mode disabled")
    assert "开发者服务" in IOSCollector._friendly_error("could not start tunnel")


@pytest.mark.parametrize("raw", [0, .42, 94.35, 100, 101, 240, 599])
def test_ios_multicore_cpu_survives_persistence_validation(raw):
    from perfzy.domain.validation import metric_value_is_plausible
    collector = make_collector({"app_cpu", "total_cpu"},
                               process={"cpuUsage": raw},
                               system={"CPUCount": 6, "SystemCPUUsage.CPU_TotalLoad": raw})
    values = collector.sample(1).values
    for key in ("app_cpu", "total_cpu"):
        assert values[key] == pytest.approx(raw / 6)
        assert metric_value_is_plausible(key, values[key])


def test_ios_cpu_uses_device_cores_when_system_count_is_temporarily_absent():
    collector = make_collector({"app_cpu"}, process={"cpuUsage": 240})
    collector._device_cpu_count = 6
    assert collector.sample(1).values["app_cpu"] == pytest.approx(40)


def test_missing_cpu_is_not_filled_with_an_old_value():
    collector = make_collector({"app_cpu"})
    collector._accept_stream_event({"type": "sysmon", "process": {"cpuUsage": 240}, "system": {"CPUCount": 6}})
    assert collector.sample(1).values["app_cpu"] == pytest.approx(40)
    collector._accept_stream_event({"type": "sysmon", "process": {"cpuUsage": None, "physFootprint": 123}})
    assert collector.sample(2).values["app_cpu"] is None
