"""Regressions for independent iOS fields and cumulative observation times."""

import pytest

from perfzy.collectors import ios
from perfzy.collectors.ios_sampling import sampling_plan


RATE_CASES = (
    ("disk_read", "process", "diskBytesRead", 1024),
    ("disk_write", "process", "diskBytesWritten", 1024),
    ("network_send", "system", "netBytesOut", 1024),
    ("network_recv", "system", "netBytesIn", 1024),
    ("ios_context_switches", "process", "ctxSwitch", 1),
    ("ios_interrupt_wakeups", "process", "intWakeups", 1),
)


@pytest.fixture
def stream(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ios.time, "monotonic", lambda: clock[0])
    collector = ios.IOSCollector()

    def send(seconds, **channels):
        clock[0] = 100.0 + seconds
        collector._accept_stream_event({
            "type": "sysmon", "process": channels.get("process", {"cpuUsage": 30}),
            "system": channels.get("system", {}),
        })
        for source in ("graphics", "battery"):
            if source in channels:
                collector._accept_stream_event({"type": source, "data": channels[source]})
        return collector.sample(seconds).values

    return collector, clock, send


def test_graphics_expire_while_cpu_stream_continues_and_recover(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"fps", "frame_time", "gpu", "app_cpu"})
    send(0, graphics={"framesPerSecond": 30, "deviceUtilization": 50})
    assert send(4.9)["fps"] == 30
    expired = send(5)
    assert expired["fps"] is expired["frame_time"] is expired["gpu"] is None
    assert expired["app_cpu"] == 30
    assert send(600)["fps"] is None
    restored = send(601, graphics={"framesPerSecond": 20, "deviceUtilization": 40})
    assert restored["fps"] == 20
    assert restored["gpu"] == 40


def test_gpu_only_update_does_not_renew_fps_or_other_graphics_fields(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"fps", "gpu", "ios_gpu_renderer"})
    send(0, graphics={"framesPerSecond": 30, "deviceUtilization": 50,
                      "Renderer Utilization %": 40})
    send(4, graphics={"deviceUtilization": 60})
    values = send(5)
    assert values["fps"] is values["ios_gpu_renderer"] is None
    assert values["gpu"] == 60


def test_empty_graphics_updates_do_not_renew_readings(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"fps"})
    send(0, graphics={"framesPerSecond": 30})
    assert send(4.9, graphics={})["fps"] == 30
    assert send(5, graphics={})["fps"] is None


def test_system_fields_expire_independently_and_core_count_is_retained(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"app_cpu", "total_cpu", "cpu_core_0_usage"})
    send(0, system={"CPUCount": 6, "SystemCPUUsage.CPU_TotalLoad": 60,
                    "PerCPUUsage.0.CPU_TotalLoad": 10})
    values = send(5, system={"PerCPUUsage.0.CPU_TotalLoad": 20})
    assert values["app_cpu"] == 5
    assert values["total_cpu"] is None
    assert values["cpu_core_0_usage"] == 20


def test_missing_process_rows_do_not_replay_cpu_and_memory_forever(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"app_cpu", "ios_physical_footprint", "total_cpu"})
    send(0, process={"cpuUsage": 30, "physFootprint": 1048576})
    values = send(5, process=None, system={"SystemCPUUsage.CPU_TotalLoad": 50})
    assert values["app_cpu"] is values["ios_physical_footprint"] is None
    assert values["total_cpu"] == 50


@pytest.mark.parametrize("interval,lifetime", [(1000, 15), (10000, 30)])
def test_battery_expiry_respects_its_slower_refresh_interval(stream, interval, lifetime):
    collector, _, send = stream
    collector._enabled = frozenset({"battery_level", "battery_voltage"})
    collector.set_sample_interval_ms(interval)
    send(0, battery={"CurrentCapacity": 80, "Voltage": 4000})
    assert send(lifetime - .1, battery={})["battery_level"] == 80
    expired = send(lifetime, battery={"Voltage": 3900})
    assert expired["battery_level"] is None
    assert expired["battery_voltage"] == 3.9


def test_long_sysmon_interval_does_not_expire_valid_fields_early(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"total_cpu"})
    collector._accept_stream_event({"type": "configuration", **sampling_plan({"total_cpu"}, 10000)})
    send(0, system={"SystemCPUUsage.CPU_TotalLoad": 60})
    assert send(10)["total_cpu"] == 60
    assert send(30)["total_cpu"] is None


@pytest.mark.parametrize("metric,source,field,unit", RATE_CASES)
def test_rate_uses_the_full_gap_between_valid_observations(stream, metric, source, field, unit):
    collector, _, send = stream
    collector._enabled = frozenset({metric})
    assert send(0, **{source: {field: 0}})[metric] is None
    assert send(1)[metric] is None
    assert send(2, **{source: {field: 2 * unit}})[metric] == pytest.approx(1)


@pytest.mark.parametrize("metric,source,field,unit", RATE_CASES)
def test_repeated_cache_reads_do_not_advance_rate_baselines(stream, metric, source, field, unit):
    collector, clock, send = stream
    collector._enabled = frozenset({metric})
    send(0, **{source: {field: 0}})
    assert send(1, **{source: {field: unit}})[metric] == 1
    clock[0] = 101.5
    assert collector.sample(1.5).values[metric] is None
    assert send(2, **{source: {field: 2 * unit}})[metric] == 1


@pytest.mark.parametrize("metric,source,field,unit", RATE_CASES)
def test_real_unchanged_counters_report_zero_and_resets_reestablish_baselines(stream, metric, source, field, unit):
    collector, _, send = stream
    collector._enabled = frozenset({metric})
    send(0, **{source: {field: 2 * unit}})
    assert send(1, **{source: {field: 2 * unit}})[metric] == 0
    assert send(2, **{source: {field: 0}})[metric] is None
    assert send(3, **{source: {field: unit}})[metric] == 1


def test_partial_network_updates_use_independent_counter_baselines(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"network_send", "network_recv"})
    send(0, system={"netBytesOut": 0, "netBytesIn": 0})
    first = send(2, system={"netBytesOut": 2048})
    assert first["network_send"] == 1
    assert first["network_recv"] is None
    second = send(4, system={"netBytesIn": 4096})
    assert second["network_send"] is None
    assert second["network_recv"] == 1


def test_process_restart_resets_process_rates_but_preserves_device_network_baseline(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"disk_read", "network_send"})
    send(0, process={"pid": 1, "diskBytesRead": 0}, system={"netBytesOut": 0})
    first = send(1, process={"pid": 1, "diskBytesRead": 1024}, system={"netBytesOut": 1024})
    assert first["disk_read"] == first["network_send"] == 1
    restarted = send(2, process={"pid": 2, "diskBytesRead": 102400}, system={"netBytesOut": 2048})
    assert restarted["disk_read"] is None
    assert restarted["network_send"] == 1
    assert send(3, process={"pid": 2, "diskBytesRead": 103424})["disk_read"] == 1


def test_configuration_change_discards_old_readings_and_rate_baselines(stream):
    collector, clock, send = stream
    collector._enabled = frozenset({"fps", "disk_read"})
    send(0, process={"diskBytesRead": 100}, graphics={"framesPerSecond": 30})
    clock[0] = 101
    collector._accept_stream_event({"type": "configuration", **sampling_plan(collector._enabled)})
    assert collector.sample(1).values == {"fps": None, "disk_read": None}
    assert send(2, process={"diskBytesRead": 10000})["disk_read"] is None


def test_inflight_snapshot_cannot_restore_a_discarded_rate_baseline(stream):
    collector, _, send = stream
    collector._enabled = frozenset({"disk_read"})
    send(0, process={"diskBytesRead": 0})
    old = dict(collector._field_received_at["process"])
    collector._accept_stream_event({"type": "configuration", **sampling_plan(collector._enabled)})
    assert collector._counter_rate("disk_read", "process", {"diskBytesRead": 0}, old,
                                   "diskBytesRead", divisor=1024) is None
    assert not collector._counter_baselines
