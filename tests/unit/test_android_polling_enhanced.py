from concurrent.futures import ThreadPoolExecutor
from statistics import median
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import subprocess

import pytest

from perfzy.collectors.android import AndroidCollector
from perfzy.collectors.android_shell import PersistentAdbShell
from perfzy.domain.models import Device, Platform, Target
from perfzy.collectors.polling import SingleFlightPoller
from test_android_collector import MEMINFO, make_collector


def start_mock_collector(monkeypatch):
    collector = AndroidCollector()
    collector._adb = "mock-adb-no-device"
    entered, release, next_entered, next_release = Event(), Event(), Event(), Event()
    memory_calls = []

    def read(*args, timeout=4.0):
        if args[0] == "pidof":
            return "1234"
        if args[:2] == ("dumpsys", "meminfo"):
            memory_calls.append(args)
            if len(memory_calls) == 1:
                entered.set()
                assert release.wait(timeout=3)
            else:
                next_entered.set()
                assert next_release.wait(timeout=3)
            return MEMINFO
        return ""

    monkeypatch.setattr(collector, "_shell", read)
    monkeypatch.setattr(collector, "_light_batch", lambda requests: {"status": "Threads: 43\n"})
    collector.start(Device("android:MOCK", Platform.ANDROID, "Mock phone"),
                    Target("android:MOCK:com.example.game", "android:MOCK", "Mock game"),
                    frozenset({"memory", "java_heap", "app_threads"}))
    return collector, entered, release, next_entered, next_release, memory_calls


def test_real_executor_slow_pss_does_not_block_fast_counters(monkeypatch):
    c, entered, release, next_entered, next_release, calls = start_mock_collector(monkeypatch)
    try:
        started = monotonic()
        sample = c.sample(1)
        assert monotonic() - started < 0.5
        assert entered.wait(1)
        assert not release.is_set()
        assert sample.values["app_threads"] == 43
        assert sample.values["memory"] is None
        assert sample.observed_elapsed == {}
        for elapsed in (1.1, 1.2, 1.3):
            sample = c.sample(elapsed)
            assert sample.values["memory"] is None
            assert sample.values["app_threads"] == 43
        assert len(calls) == 1
        assert c.diagnostics()["memory"]["busy_polls"] == 3
        release.set()
        c._memory_poller._future.result(timeout=1)
        sample = c.sample(2)
        assert sample.values["memory"] == 600.0
        assert sample.values["java_heap"] == 50.0
        assert 1.0 <= sample.observed_elapsed["memory"] < 2.0
        assert sample.observed_elapsed["memory"] == sample.observed_elapsed["java_heap"]
        assert "app_threads" not in sample.observed_elapsed
        assert next_entered.wait(1)
        assert c.sample(3).values["memory"] is None
        assert len(calls) == 2
        snapshot = c.diagnostics()
        assert snapshot["memory"]["source"] == "dumpsys meminfo --local"
        assert snapshot["memory"]["refresh_interval_s"] == 1.0
        assert snapshot["memory"]["last_received_age_s"] is not None
    finally:
        release.set()
        next_release.set()
        c.stop()
    assert c.diagnostics()["memory"]["submitted"] == 2
    assert c.diagnostics()["memory"]["closed"] is True
    assert c.diagnostics()["connected"] is False


def test_pid_change_discards_previous_process_pss(monkeypatch):
    c, entered, release, next_entered, next_release, _ = start_mock_collector(monkeypatch)
    try:
        c.sample(1)
        assert entered.wait(1)
        release.set()
        c._memory_poller._future.result(timeout=1)
        c._pid = "9999"
        sample = c.sample(2)
        assert sample.values["memory"] is None
        assert c._baseline_pss_mb is None
        assert sample.observed_elapsed == {}
        assert c.diagnostics()["memory"]["context_discarded"] == 1
    finally:
        release.set()
        next_release.set()
        c.stop()


def test_start_failure_does_not_leak_executors(monkeypatch):
    c = AndroidCollector()
    c._adb = "mock-adb"
    monkeypatch.setattr(c, "_shell", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="尚未运行"):
        c.start(Device("android:MOCK", Platform.ANDROID, "Mock"),
                Target("android:MOCK:game", "android:MOCK", "game"), frozenset({"memory"}))
    assert c._sample_executor is None
    assert c._memory_poller is None


def test_memory_freshness_tracks_requested_cadence_without_extra_throttle(monkeypatch):
    c, entered, release, next_entered, next_release, _ = start_mock_collector(monkeypatch)
    try:
        c.set_sample_interval_ms(5000)
        assert c.diagnostics()["memory"]["max_age_s"] == 10.0
        assert c.diagnostics()["memory"]["refresh_interval_s"] == 5.0
        c.set_sample_interval_ms(500)
        assert c.diagnostics()["memory"]["max_age_s"] == 5.0
        assert c.diagnostics()["memory"]["refresh_interval_s"] == 0.5
        assert c.diagnostics()["memory"]["refresh_policy"] == "each_sample_tick_single_flight"
    finally:
        release.set()
        next_release.set()
        c.stop()


def test_shell_diagnostics_count_actual_failures_and_elapsed(monkeypatch):
    c = AndroidCollector()
    c._adb = "mock-adb"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="ok", stderr="", returncode=0))
    assert c._shell("true") == "ok"
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("adb", 1)
    monkeypatch.setattr(subprocess, "run", timeout)
    c._shell("true")
    stats = c.diagnostics()["shell"]
    assert stats["commands"] == 2
    assert stats["timeouts"] == 1
    assert stats["errors"] == 0
    assert stats["duration_ms_total"] >= stats["duration_ms_max"] >= 0


def test_light_channel_diagnostics_do_not_wait_for_execute_lock(monkeypatch):
    shell = PersistentAdbShell("mock", "MOCK")
    shell._lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            snapshot = executor.submit(shell.diagnostics).result(timeout=0.5)
        assert snapshot["commands"] == 0
        assert snapshot["connected"] is False
    finally:
        shell._lock.release()
        shell.close()


def test_controlled_80ms_meminfo_sample_latency(monkeypatch):
    """Host-only timing experiment; it does not measure real device overhead."""
    collector = make_collector({"dumpsys meminfo": MEMINFO}, {"memory"})
    original_shell = collector._shell

    def slow_shell(*args, **kwargs):
        if args[:2] == ("dumpsys", "meminfo"):
            sleep(0.08)
        return original_shell(*args, **kwargs)

    monkeypatch.setattr(collector, "_shell", slow_shell)
    synchronous = []
    for tick in range(5):
        began = monotonic()
        assert collector.sample(tick + 1).values["memory"] == 600
        synchronous.append(monotonic() - began)
    collector._sample_executor = ThreadPoolExecutor(max_workers=1)
    collector._memory_poller = SingleFlightPoller()
    asynchronous = []
    received = 0
    try:
        for tick in range(12):
            began = monotonic()
            received += collector.sample(tick / 25.0 + 10).values["memory"] is not None
            asynchronous.append(monotonic() - began)
            sleep(0.04)
        assert received >= 2
        assert median(synchronous) >= 0.07
        assert median(asynchronous) < median(synchronous) / 2
        print("controlled_meminfo_80ms: synchronous_median_ms=%.3f asynchronous_median_ms=%.3f "
              "async_samples_with_pss=%d/12" %
              (median(synchronous) * 1000, median(asynchronous) * 1000, received))
    finally:
        collector.stop()
