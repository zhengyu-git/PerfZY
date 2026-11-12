"""Guards on the controller wiring for metric selection and session viewing."""

import math

import pytest

from perfzy.application import controller as controller_module
from perfzy.application.controller import AppController
from perfzy.domain.models import Device, Platform, Sample, Target
from perfzy.storage.sessions import save_session


WINDOWS_DEVICE = Device(id="windows:local", platform=Platform.WINDOWS, name="本机 PC")
ANDROID_DEVICE = Device(id="android:SERIAL", platform=Platform.ANDROID, name="Test Phone",
                        details={"cpu_cores": "8"})
TARGET = Target(id="windows:4242", device_id="windows:local", name="game", process_id=4242)


class RecordingCollector:
    """Captures what the controller pushes to a running collector."""

    def __init__(self):
        self.gates = []
        self.activations = 0
        self.stopped = False

    def set_enabled_metrics(self, enabled_metrics):
        self.gates.append(enabled_metrics)

    def activate_pending_sources(self):
        self.activations += 1

    def stop(self):
        self.stopped = True


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(controller_module, "discover_all", lambda: [])
    monkeypatch.setattr(controller_module, "discover_targets", lambda device: [])
    instance = AppController()
    yield instance
    instance.shutdown()


def select(instance, device):
    instance._device_models = [device]
    instance._devices = [{"id": device.id, "name": device.name}]
    instance._selected_device = 0


def test_disabling_a_metric_only_affects_the_selected_platform(controller):
    select(controller, WINDOWS_DEVICE)
    controller.setMetricEnabled("fps", False)

    assert "fps" not in controller._preferences.enabled_for(Platform.WINDOWS)
    assert "fps" in controller._preferences.enabled_for(Platform.ANDROID)
    assert "fps" in controller._preferences.enabled_for(Platform.IOS)


def test_a_metric_the_platform_cannot_collect_is_rejected(controller):
    select(controller, WINDOWS_DEVICE)
    controller.setMetricEnabled("java_heap", True)
    assert "java_heap" not in controller._preferences.enabled_for(Platform.WINDOWS)


def test_enabling_a_metric_mid_collection_reaches_the_collector(controller):
    select(controller, WINDOWS_DEVICE)
    collector = RecordingCollector()
    controller._collector = collector

    controller.setMetricEnabled("fps", True)

    assert collector.gates, "the metric gate was not pushed to the collector"
    assert "fps" in collector.gates[-1]
    controller._executor.shutdown(wait=True)
    assert collector.activations == 1, "the device-side source was never activated"


def test_showing_a_chart_mid_collection_reaches_the_collector(controller):
    select(controller, WINDOWS_DEVICE)
    controller.setMetricEnabled("disk_read", True)
    collector = RecordingCollector()
    controller._collector = collector

    controller.setChartVisible("io", True)

    assert "disk_read" in collector.gates[-1]
    controller._executor.shutdown(wait=True)
    assert collector.activations == 1


def test_only_visible_charts_contribute_to_the_metric_gate(controller):
    select(controller, WINDOWS_DEVICE)
    controller.setMetricEnabled("disk_read", True)
    controller.setChartVisible("io", True)
    assert "disk_read" in controller._active_metric_keys()

    controller.setChartVisible("io", False)
    assert "disk_read" not in controller._active_metric_keys()


def test_windows_workspace_hides_mobile_only_charts(controller):
    select(controller, WINDOWS_DEVICE)
    keys = {chart["key"] for chart in controller.chartManagerCatalog}
    assert "cpu_core_usage" not in keys
    assert "gpu_clock" not in keys


def test_device_refresh_preserves_the_selected_windows_device(controller):
    select(controller, WINDOWS_DEVICE)

    controller._accept_devices([ANDROID_DEVICE, WINDOWS_DEVICE])

    assert controller.selectedDeviceIndex == 1
    assert controller.devices[1]["id"] == "windows:local"


def test_an_old_target_result_cannot_replace_the_current_device_targets(controller):
    select(controller, WINDOWS_DEVICE)
    controller._target_discovery_token = 2

    controller._accept_targets({"token": 1, "deviceId": "windows:local",
                                "targets": [TARGET], "error": ""})

    assert controller.targets == []


def test_android_workspace_expands_charts_to_the_device_core_count(controller):
    select(controller, ANDROID_DEVICE)
    cores = next(chart for chart in controller.chartManagerCatalog
                 if chart["key"] == "cpu_core_usage")
    assert len(cores["metricKeys"]) == 8


def _write_ios_session(root, session_id="20260101_120000_ios001"):
    return save_session(
        root,
        {"id": "ios:UDID", "name": "iPhone 16 Pro", "platform": "ios", "model": "iPhone17,1"},
        {"id": "ios:UDID:com.example.game", "name": "Example Game", "pid": 0},
        {"ios_physical_footprint": [{"x": 1.0, "y": 512.0}],
         "ios_gpu_renderer": [{"x": 1.0, "y": 44.0}],
         "ios_mach_ports": [{"x": 1.0, "y": 900.0}]},
        [], {"ios_physical_footprint"}, session_id, [], "2026-01-01T12:00:00+08:00", 1.0,
        "iOS 采集")


def test_viewing_an_ios_session_reveals_charts_for_ios_only_metrics(controller, tmp_path):
    _write_ios_session(tmp_path)
    controller.openSession("20260101_120000_ios001")

    assert controller.viewingSession
    visible = {chart["key"] for chart in controller.chartCatalog}
    assert {"memory", "gpu", "process"} <= visible

    memory = next(chart for chart in controller.chartCatalog if chart["key"] == "memory")
    assert "ios_physical_footprint" in memory["metricKeys"]
    assert memory["seriesEnabled"][memory["metricKeys"].index("ios_physical_footprint")]


def test_closing_a_session_view_restores_the_previous_selection(controller, tmp_path):
    select(controller, WINDOWS_DEVICE)
    before_metrics = set(controller._preferences.enabled_for(Platform.IOS))
    before_charts = set(controller._preferences.visible_charts)

    _write_ios_session(tmp_path)
    controller.openSession("20260101_120000_ios001")
    assert controller._preferences.enabled_for(Platform.IOS) != before_metrics

    controller.closeSessionView()
    assert not controller.viewingSession
    assert controller._preferences.enabled_for(Platform.IOS) == before_metrics
    assert controller._preferences.visible_charts == before_charts
    assert controller.chartData["ios_physical_footprint"] == []


def test_session_view_changes_are_not_persisted(controller, tmp_path):
    _write_ios_session(tmp_path)
    controller.openSession("20260101_120000_ios001")
    controller.closeSessionView()

    reloaded = controller_module.Preferences(tmp_path / "data" / "preferences.ini")
    assert "ios_mach_ports" not in reloaded.enabled_for(Platform.IOS)


def test_metric_toggles_while_viewing_history_remain_temporary(controller, tmp_path):
    _write_ios_session(tmp_path)
    controller.openSession("20260101_120000_ios001")
    controller.setMetricEnabled("ios_mach_ports", False)
    controller.closeSessionView()

    reloaded = controller_module.Preferences(tmp_path / "data" / "preferences.ini")
    assert "ios_mach_ports" not in reloaded.enabled_for(Platform.IOS)
    assert "ios_gpu_renderer" not in reloaded.enabled_for(Platform.IOS)
    assert "ios_physical_footprint" in reloaded.enabled_for(Platform.IOS)
    assert "fps" in reloaded.enabled_for(Platform.IOS)


def test_starting_from_history_restores_the_live_platform_first(controller, tmp_path, monkeypatch):
    select(controller, WINDOWS_DEVICE)
    controller._target_models = [TARGET]
    controller._targets = [{"id": TARGET.id, "name": TARGET.name, "pid": TARGET.process_id}]
    controller._selected_target = 0
    controller._busy = False
    _write_ios_session(tmp_path)
    controller.openSession("20260101_120000_ios001")
    starts = []
    monkeypatch.setattr(controller, "toggleCollection", lambda: starts.append(1))

    controller.startOrPauseCollection()

    assert starts == [1]
    assert not controller.viewingSession
    assert controller._current_platform() is Platform.WINDOWS


def test_moving_a_chart_reorders_the_workspace(controller):
    select(controller, WINDOWS_DEVICE)
    order = [chart["key"] for chart in controller.chartManagerCatalog]
    controller.moveChart(order[1], -1)
    assert [chart["key"] for chart in controller.chartManagerCatalog][:2] == [order[1], order[0]]


def test_sampling_interval_changes_reach_the_timer(controller):
    assert controller._screenshot_timer.isSingleShot()
    controller.setSampleInterval(2.5)
    assert controller.sampleIntervalSeconds == pytest.approx(2.5)
    assert controller._timer.interval() == 2500

    controller.setSampleInterval(0.01)
    assert controller._timer.interval() == 500


def test_collection_start_requests_the_first_sample_immediately(controller):
    starts = []
    select(controller, WINDOWS_DEVICE)
    controller._target_models = [TARGET]
    controller._targets = [{"id": TARGET.id, "name": TARGET.name, "pid": TARGET.process_id}]
    controller._selected_target = 0
    controller._sample_once = lambda: starts.append(controller.duration)
    collector = RecordingCollector()

    controller._accept_collector_started({"ok": True, "collector": collector})

    assert len(starts) == 1
    assert starts[0] < 0.1
    assert controller._timer.interval() == controller._preferences.sample_interval_ms


def test_resuming_collection_requests_a_sample_immediately(controller):
    starts = []
    controller._collecting = True
    controller._paused = True
    controller._pause_started = controller_module.time.monotonic() - 2.0
    controller._sample_once = lambda: starts.append(1)

    controller.startOrPauseCollection()

    assert starts == [1]
    assert not controller.paused


def test_non_finite_sampling_intervals_are_ignored(controller):
    sample_interval = controller._timer.interval()
    screenshot_interval = controller._screenshot_timer.interval()

    controller.setSampleInterval(float("nan"))
    controller.setScreenshotInterval(float("inf"))

    assert controller._timer.interval() == sample_interval
    assert controller._screenshot_timer.interval() == screenshot_interval


def test_live_samples_update_health_without_broadcasting_unchanged_global_state(controller):
    controller._collecting = True
    controller._collector = RecordingCollector()
    controller._active_collection_token = 7
    controller._sample_inflight_token = 7
    controller._status_text = "正在采集"
    state_changes = []
    batches = []
    controller.stateChanged.connect(lambda: state_changes.append(1))
    controller.sampleBatchAppended.connect(lambda batch: batches.append(batch))

    controller._accept_sample({"token": 7, "sample": Sample(1.0, {"fps": 60.0}),
                               "error": "", "elapsed_ms": 12.5})

    assert controller._sample_count == 1
    assert "12 ms" in controller.samplingHealthText
    assert batches[0]["fps"]["y"] == 60.0
    assert state_changes == []


def test_a_late_sample_from_an_old_session_is_ignored(controller):
    controller._collecting = True
    controller._active_collection_token = 8
    controller._sample_inflight_token = 8

    controller._accept_sample({"token": 7, "sample": Sample(99.0, {"fps": 1.0}),
                               "error": "", "elapsed_ms": 1.0})

    assert controller._samples["fps"] == []
    assert controller._sample_inflight_token == 8


def test_non_finite_live_values_never_reach_the_chart(controller):
    controller._collecting = True
    controller._collector = RecordingCollector()
    controller._active_collection_token = 9
    controller._sample_inflight_token = 9
    controller._status_text = "正在采集"

    controller._accept_sample({
        "token": 9,
        "sample": Sample(1.0, {"fps": math.nan, "app_cpu": math.inf,
                                "total_cpu": 25.0}),
        "error": "",
        "elapsed_ms": 2.0,
    })

    assert controller._samples["fps"] == []
    assert controller._samples["app_cpu"] == []
    assert controller._samples["total_cpu"] == [{"x": 1.0, "y": 25.0}]


def test_collector_that_finishes_starting_after_shutdown_is_stopped(controller):
    collector = RecordingCollector()
    controller._shutting_down = True

    controller._accept_collector_started({"ok": True, "collector": collector})

    assert collector.stopped
    assert controller._collector is None


def test_started_collector_is_stopped_if_selection_disappeared(controller):
    collector = RecordingCollector()
    controller._busy = True
    controller._starting_collection = True

    controller._accept_collector_started({"ok": True, "collector": collector})

    assert collector.stopped
    assert controller._collector is None
    assert not controller.busy


def test_shutdown_waits_for_an_inflight_sample_before_stopping_collector(controller):
    events = []

    class PendingSample:
        def result(self, timeout=None):
            events.append(("sample", timeout))

    class OrderedCollector(RecordingCollector):
        def stop(self):
            events.append(("stop", None))
            super().stop()

    controller._collector = OrderedCollector()
    controller._active_collection_token = 4
    controller._sample_inflight_token = 4
    controller._sample_future = PendingSample()

    controller.shutdown()

    assert events == [("sample", 30), ("stop", None)]


def test_a_late_screenshot_from_an_old_session_is_deleted(controller, tmp_path):
    stale = tmp_path / "data" / "tmp" / "live-screenshots" / "7" / "stale.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"jpg")
    controller._collecting = True
    controller._active_collection_token = 8
    controller._screenshot_inflight_token = 8

    controller._accept_screenshot({
        "token": 7, "elapsed": 1.0, "path": str(stale),
        "destination": str(tmp_path / "session" / "shot.jpg"),
    })

    assert controller._screenshots == []
    assert controller._screenshot_inflight_token == 8
    assert not stale.exists()


def test_a_busy_sample_is_counted_as_a_skipped_timer_tick(controller):
    controller._collector = RecordingCollector()
    controller._sample_working = True

    controller._sample_once()

    assert controller._sample_skipped == 1


def test_opening_an_unknown_session_reports_an_error(controller):
    notifications = []
    controller.notificationRequested.connect(
        lambda title, message, level: notifications.append(title))
    controller.openSession("does-not-exist")
    assert notifications and not controller.viewingSession
