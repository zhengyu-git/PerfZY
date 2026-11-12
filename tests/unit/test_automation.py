import json
import time

from perfzy import automation
from perfzy.automation import PerfZY, PerfZYError
import pytest
from perfzy.domain.models import Device, Platform, Sample, Target


class FakeCollector:
    def start(self, device, target, enabled):
        self.enabled = enabled

    def sample(self, elapsed):
        return Sample(elapsed, {"app_cpu": 25.0})

    def stop(self):
        pass


def test_metric_status_filters_invalid_data_and_resets(tmp_path, monkeypatch):
    api = PerfZY(tmp_path)

    class OneSampleCollector(FakeCollector):
        def sample(self, elapsed):
            api._stop_event.set()
            return Sample(elapsed, {"app_cpu": 0.0, "memory": None,
                                    "total_cpu": float("nan"), "fps": float("inf")})

    monkeypatch.setattr(automation, "WindowsCollector", OneSampleCollector)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)
    try:
        assert all(not values for values in api.metric_status().values())
        requested = ["app_cpu", "memory", "total_cpu", "fps", "temperature"]
        api.start(metrics=requested, screenshots=False)
        api._thread.join(timeout=5)
        assert not api._thread.is_alive()
        status = api.metric_status()
        assert status["requested"] == sorted(requested)
        assert status["unsupported"] == ["temperature"]
        assert status["enabled"] == ["app_cpu", "fps", "memory", "total_cpu"]
        assert status["received"] == ["app_cpu"]  # zero is valid
        assert status["no_data"] == ["fps", "memory", "total_cpu"]
        receipt = api._metric_received_at["app_cpu"]
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(automation.time, "monotonic", lambda: receipt + 6)
            freshness = api.metric_freshness(stale_after=5)
            assert freshness["app_cpu"]["stale"] is True
            assert freshness["app_cpu"]["age"] == 6
            assert freshness["app_cpu"]["last_received"] == max(0, receipt - api._started)
            assert freshness["memory"] == {"last_received": None, "age": None, "stale": False}
            assert "temperature" not in freshness
            assert api.metric_freshness(stale_after=7)["app_cpu"]["stale"] is False
        for threshold in (0, -1, float("nan"), float("inf"), "bad"):
            with pytest.raises(PerfZYError, match="断更阈值"):
                api.metric_freshness(threshold)
        status["enabled"].clear()
        assert api.metric_status()["enabled"]
        api.stop("metric status")
        with pytest.raises(PerfZYError, match="采集已结束"):
            api.metric_freshness()
        assert api.metric_status()["received"] == ["app_cpu"]
        with pytest.raises(PerfZYError):
            api.start(metrics=["unknown"], screenshots=False)
        assert api.metric_status()["received"] == ["app_cpu"]
        api.start(metrics=["memory"], screenshots=False)
        api._thread.join(timeout=5)
        assert not api._thread.is_alive()
        assert api.metric_freshness()["memory"]["last_received"] is None
        assert api.metric_status() == {
            "requested": ["memory"], "enabled": ["memory"],
            "unsupported": [], "received": [], "no_data": ["memory"],
        }
        api.stop("no data")
    finally:
        api.close()


def test_health_reports_failure_without_preventing_save_and_restart(tmp_path, monkeypatch):
    class FailingCollector(FakeCollector):
        calls = 0

        def sample(self, elapsed):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("device disconnected")
            return super().sample(elapsed)

    monkeypatch.setattr(automation, "WindowsCollector", FailingCollector)
    api = PerfZY(tmp_path)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)
    try:
        with pytest.raises(PerfZYError, match="尚未开始"):
            api.check_health()
        api.start(metrics=["app_cpu"], sample_interval=.5, screenshots=False)
        assert api._stop_event.wait(5), "worker did not report failure"
        with pytest.raises(PerfZYError, match="device disconnected") as caught:
            api.check_health()
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert api.collecting  # stop still owns cleanup and finalization
        with pytest.raises(PerfZYError, match="已保存现有数据"):
            api.stop("interrupted")
        result = api.stop()
        payload = json.loads(result.session_path.read_text(encoding="utf-8"))
        assert len(payload["samples"]["app_cpu"]) == 1
        assert payload["end_reason"] == "采样中断：device disconnected"

        monkeypatch.setattr(automation, "WindowsCollector", FakeCollector)
        api.start(metrics=["app_cpu"], screenshots=False)
        api.check_health()  # previous error must not survive a fresh start
        api.stop("restarted")
        with pytest.raises(PerfZYError, match="采集已结束"):
            api.check_health()
    finally:
        api.close()


def test_slow_screenshots_do_not_block_automation_samples(tmp_path, monkeypatch):
    def slow_screenshot(_pid, destination, _width):
        time.sleep(1.0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpg")
        return destination

    monkeypatch.setattr(automation, "WindowsCollector", FakeCollector)
    monkeypatch.setattr(automation, "capture_process_window", slow_screenshot)
    api = PerfZY(tmp_path)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)

    api.start(metrics=["app_cpu"], sample_interval=.5, screenshots=True,
              screenshot_interval=.5)
    time.sleep(1.7)
    result = api.stop("API test")
    with result.session_path.open(encoding="utf-8") as stream:
        payload = json.load(stream)

    assert result.sample_count >= 3
    assert len(payload["samples"]["app_cpu"]) >= 3
    assert result.screenshot_count >= 1
    assert not (result.session_path.parent / "samples-live").exists()
    api.close()


def test_overdue_screenshot_starts_when_the_worker_frees(tmp_path, monkeypatch):
    starts = []

    def slower_than_interval(_pid, destination, _width):
        starts.append(time.monotonic())
        time.sleep(.65)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpg")
        return destination

    monkeypatch.setattr(automation, "WindowsCollector", FakeCollector)
    monkeypatch.setattr(automation, "capture_process_window", slower_than_interval)
    api = PerfZY(tmp_path)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)

    api.start(metrics=["app_cpu"], sample_interval=.5, screenshots=True,
              screenshot_interval=.5)
    time.sleep(1.9)
    api.stop("Screenshot cadence")
    api.close()

    assert len(starts) >= 2
    # The capture itself is the physical lower bound. It must not be rounded
    # up to the next 0.5 s scheduler multiple merely because it was still busy.
    assert starts[1] - starts[0] < .9


def test_context_manager_always_closes_the_screenshot_executor(tmp_path):
    api = PerfZY(tmp_path)
    with api:
        pass
    assert api._screenshot_executor is None


def test_string_session_ids_cannot_escape_the_sessions_directory(tmp_path):
    api = PerfZY(tmp_path)
    try:
        with pytest.raises(PerfZYError):
            api.generate_html("../outside")
    finally:
        api.close()


def test_invalid_start_arguments_never_activate_a_collector(tmp_path, monkeypatch):
    starts = []

    class RecordingCollector(FakeCollector):
        def start(self, device, target, enabled):
            starts.append((device, target, enabled))

    monkeypatch.setattr(automation, "WindowsCollector", RecordingCollector)
    api = PerfZY(tmp_path)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)
    try:
        for arguments in (
            {"metrics": []},
            {"metrics": ["not-a-metric"]},
            {"sample_interval": float("nan")},
            {"screenshot_interval": 0.1},
            {"screenshot_width": 99999},
        ):
            with pytest.raises(PerfZYError):
                api.start(**arguments)
        assert starts == []
        assert not api.collecting
    finally:
        api.close()


def test_sampling_thread_start_failure_releases_collector_and_spool(tmp_path, monkeypatch):
    collectors = []

    class RecordingCollector(FakeCollector):
        def __init__(self):
            self.stopped = False
            collectors.append(self)

        def stop(self):
            self.stopped = True

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(automation, "WindowsCollector", RecordingCollector)
    monkeypatch.setattr(automation.threading, "Thread", BrokenThread)
    api = PerfZY(tmp_path)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)
    try:
        with pytest.raises(PerfZYError, match="无法启动采样线程"):
            api.start(metrics=["app_cpu"], screenshots=False)
        assert collectors and collectors[0].stopped
        assert not api.collecting
        assert api._session_spool is None
    finally:
        api.close()


def test_latest_metrics_snapshot_and_session_reset(tmp_path, monkeypatch):
    api = PerfZY(tmp_path)
    samples = iter([
        {"fps": 30, "app_cpu": 0, "memory": 512, "total_cpu": 90},
        {"fps": float("nan"), "app_cpu": None},
        {"memory": 128},
    ])

    class LatestCollector(FakeCollector):
        def sample(self, elapsed):
            api._stop_event.set()
            return Sample(elapsed, next(samples))

    monkeypatch.setattr(automation, "WindowsCollector", LatestCollector)
    api.device = Device("windows:local", Platform.WINDOWS, "PC")
    api.game = Target("windows:1", "windows:local", "Game", 1)
    try:
        assert api.latest_metrics() == {}
        api.start(metrics=["fps", "app_cpu", "memory"], screenshots=False)
        api._thread.join(timeout=5)
        assert api.latest_metrics() == {"fps": 30, "app_cpu": 0, "memory": 512}
        copy = api.latest_metrics()
        copy["fps"] = 999
        assert api.latest_metrics()["fps"] == 30
        api._stop_event.clear()
        api._collect_loop()
        assert api.latest_metrics() == {"fps": None, "app_cpu": None, "memory": None}
        api.stop("latest")
        assert api.latest_metrics()["fps"] is None
        api.start(metrics=["memory"], screenshots=False)
        api._thread.join(timeout=5)
        assert api.latest_metrics() == {"memory": 128}
        api.stop("reset")
        assert api.latest_metrics() == {"memory": 128}
    finally:
        api.close()
