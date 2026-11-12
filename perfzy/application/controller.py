from __future__ import annotations

import time
import json
import re
import math
import os
import hashlib
from collections import deque
from bisect import bisect_left, bisect_right
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import QObject, Property, Qt, QTimer, QUrl, Signal, Slot, QFileSystemWatcher
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ..collectors.discovery import discover_all
from ..collectors.targets import discover_targets
from ..collectors.windows import WindowsCollector
from ..collectors.android import AndroidCollector
from ..collectors.ios import IOSCollector
from ..domain.metrics import METRICS, METRIC_BY_KEY, platform_metric_title
from ..domain.models import Platform, Target
from ..domain.validation import metric_value_is_plausible, sample_points
from ..domain.health import SamplingHealth
from ..domain.collection_status import collection_metric_status
from ..domain.collection_quality import collection_quality_notes
from ..domain.provenance import canonical_platform, metric_provenance
from ..domain.capture_configuration import reusable_capture_configuration
from ..domain.ios_identity import DECLARED_GAME_FPS_LIMITS, ios_bundle_id
from ..comparison import generate_comparison_report
from ..diagnostics import connection_check
from ..capture.windows import capture_process_window
from ..capture import windows as windows_capture
from ..capture.android import capture_android
from ..capture.ios import capture_ios
from pathlib import Path
from ..storage.sessions import (delete_session as delete_saved_session, load_session,
                                discard_incomplete_session,
                                LiveSessionWriter, load_session_records, new_session_id,
                                normalize_session_name, recover_incomplete_sessions,
                                rename_session, save_session,
                                save_session_from_spool)
from ..storage.merging import merge_sessions
from ..storage.record_overwrite import recover_pending_overwrites
from ..storage.reports import link_report_source, load_html_reports, report_path, rename_html_report, delete_html_report
from ..reporting import generate_local_report
from ..runtime_paths import WORK_ROOT
from .charts import (build_chart_catalog, build_metric_catalog, core_index,
                     group_key_for_metric, groups_with_data, metric_available)
from .preferences import Preferences, VALID_SCREENSHOT_WIDTHS
from .device_presentation import present_device
from .metric_status import update_windows_status, update_ios_status, update_android_status


LIVE_CHART_WINDOW_SECONDS = 3600.0
LIVE_CHART_MAX_POINTS = 7200


from ..domain.screenshot_timeline import match_screenshot, ScreenshotMatch


class AppController(QObject):
    devicesChanged = Signal()
    targetsChanged = Signal()
    stateChanged = Signal()
    samplesChanged = Signal()
    # QVariantMap is required here: a generic PyObject reaches QML as an opaque
    # value, so batch[metricKey] is undefined and live charts never append.
    sampleBatchAppended = Signal("QVariantMap")
    timelineChanged = Signal()
    screenshotsChanged = Signal()
    screenshotStateChanged = Signal()
    settingsChanged = Signal()
    selectionChanged = Signal()
    scenesChanged = Signal()
    timeLockChanged = Signal()
    chartLayoutChanged = Signal()
    chartRangeChanged = Signal()
    discoveryReady = Signal(object)
    targetsReady = Signal(object)
    screenshotReady = Signal(object)
    sampleReady = Signal(object)
    sessionSaved = Signal(object)
    sessionNamingRequested = Signal(str, str)
    sessionRecordsChanged = Signal()
    captureConfigurationReady = Signal(object)
    captureConfigurationApplied = Signal()
    htmlReportsChanged = Signal()
    htmlReportsReady = Signal(object)
    sessionMergeReady = Signal(object)
    sessionsMerged = Signal(str)
    sessionOpened = Signal(object)
    reportGenerated = Signal(object)
    collectorStarted = Signal(object)
    recoveryReady = Signal(object)
    notificationRequested = Signal(str, str, str)
    windowTargetsChanged = Signal()
    collectionHealthChanged = Signal()
    sceneMarkChanged = Signal()
    sceneMarkStopRequested = Signal()
    diagnosticsReady = Signal(object)
    diagnosticsChanged = Signal()
    runtimeLogChanged = Signal()

    def __init__(self, *, history_only: bool = False, preferences_path: Path | None = None) -> None:
        super().__init__()
        self._record_viewer = None
        # Reuse a small bounded worker pool instead of creating two new OS
        # threads every sampling cycle. This keeps long captures stable.
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="PerfZY")
        # JPEG encoding and device screenshot commands can take seconds. Keep
        # them off the sampling pool so a slow capture cannot delay metrics.
        self._screenshot_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PerfZY-Shot")
        self._diagnostic_results = []
        self._diagnostic_running = False
        self.diagnosticsReady.connect(self._accept_diagnostics)
        self._devices: list[dict] = []
        self._targets: list[dict] = []
        self._device_models = []
        self._target_models = []
        self._selected_device = -1
        self._selected_target = -1
        self._viewing_platform: Platform | None = None
        self._pre_view_selection: dict[Platform, set[str]] | None = None
        self._pre_view_visible_charts: set[str] | None = None
        self._pre_view_windows_charts: set[str] | None = None
        self._pre_view_ios_charts: set[str] | None = None
        self._metric_status = {}
        self._metric_misses = {}
        self._last_metric_diagnostics = {}
        self._marking_scene_start = None
        self._marking_scene_name = ''
        self._collecting = False
        self._starting_collection = False
        self._paused = False
        self._pause_started = 0.0
        self._paused_total = 0.0
        self._busy = False
        self._device_discovery_token = 0
        self._target_discovery_token = 0
        self._collector: WindowsCollector | AndroidCollector | IOSCollector | None = None
        self._started_at = 0.0
        self._samples: dict[str, list[dict]] = {item.key: [] for item in METRICS}
        # PerfZY opens with a compact core workspace; additional metrics are
        # added from the chart-area plus button instead of the settings tab.
        self._preferences = Preferences(preferences_path or WORK_ROOT / "data" / "preferences.ini")
        self.settingsChanged.connect(self._record_capture_settings)
        self._screenshots: list[dict] = []
        self._screenshot_capture_settings = {}
        self._screenshot_settings_changes = []
        self._screenshot_settings_truncated = False
        for signal in (self.timeLockChanged, self.settingsChanged, self.screenshotsChanged, self.stateChanged):
            signal.connect(self.screenshotStateChanged.emit)
        self._selection = (-1.0, -1.0)
        self._scenes: list[dict] = []
        self._locked_time = -1.0
        self._chart_view_start_ratio = 0.0
        self._chart_view_end_ratio = 1.0
        self._selected_screenshot = -1
        self._screenshot_working = False
        self._screenshot_failure_reported = False
        self._sample_working = False
        self._sample_started_at = 0.0
        self._sample_count = 0
        self._sample_skipped = 0
        self._sample_last_ms = 0.0
        self._sample_max_ms = 0.0
        self._sampling_health = SamplingHealth()
        self._saved_telemetry = {}
        self._viewed_metadata = {}
        self._sample_slow_warning_reported = False
        self._status_text = "就绪"
        self._runtime_log = deque(maxlen=500)
        self._last_logged_status = ""
        self._session_id = ""
        self._session_end_reason = ""
        self._session_spool: LiveSessionWriter | None = None
        self._live_points_trimmed = False
        self._report_working = False
        self._saving_session = False
        self._merging_sessions = False
        self._collection_token = 0
        self._active_collection_token = 0
        self._sample_inflight_token = 0
        self._screenshot_inflight_token = 0
        self._sample_future: Future | None = None
        self._shutting_down = False
        self._window_candidates = []
        self._performance_window = None
        self._screenshot_window = None
        self._screenshot_target_generation = 0
        self._window_pick_mode = ""
        self._window_pick_seconds = 0
        self._window_pick_status = ""
        self._window_pick_timer = QTimer(self)
        self._window_pick_timer.setInterval(1000)
        self._window_pick_timer.timeout.connect(self._window_pick_tick)
        self._configuration_request = 0
        self._session_started_at = ""
        self._session_records: list[dict] = []
        self._html_reports = []
        self._html_report_cache = {}
        self._html_reports_loading = False
        self._html_refresh_pending = False
        self._html_watcher = QFileSystemWatcher(self)
        self._html_refresh_timer = QTimer(self)
        self._html_refresh_timer.setSingleShot(True)
        self._html_refresh_timer.setInterval(300)
        self._html_refresh_timer.timeout.connect(self.refreshHtmlReports)
        self._html_watcher.directoryChanged.connect(self._schedule_html_refresh)
        self.htmlReportsReady.connect(self._accept_html_reports)
        self.captureConfigurationReady.connect(self._accept_capture_configuration)
        self._screenshot_dir = WORK_ROOT / "data" / "sessions" / "unsaved" / "screenshots"
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(self._preferences.sample_interval_ms)
        self._timer.timeout.connect(self._sample_once)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._refresh_collection_health)
        if not history_only:
            self._health_timer.start()
        self.settingsChanged.connect(self.collectionHealthChanged.emit)
        self.stateChanged.connect(self.collectionHealthChanged.emit)
        self._screenshot_timer = QTimer(self)
        self._screenshot_timer.setTimerType(Qt.PreciseTimer)
        self._screenshot_timer.setSingleShot(True)
        self._screenshot_timer.setInterval(self._preferences.screenshot_interval_ms)
        self._screenshot_timer.timeout.connect(self._capture_screenshot)
        self.discoveryReady.connect(self._accept_devices)
        self.targetsReady.connect(self._accept_targets)
        self.screenshotReady.connect(self._accept_screenshot)
        self.sampleReady.connect(self._accept_sample)
        self.sessionSaved.connect(self._accept_session_saved)
        self.sessionMergeReady.connect(self._accept_sessions_merged)
        self.reportGenerated.connect(self._accept_report_generated)
        self.collectorStarted.connect(self._accept_collector_started)
        self.recoveryReady.connect(self._accept_recovery)
        self.stateChanged.connect(self._record_status_log)
        self._record_status_log()
        if history_only:
            return
        self.refreshSessionRecords()
        self.refreshDevices()
        # Recovery can parse hundreds of thousands of JSONL rows. Keep it away
        # from Qt startup so a large interrupted session cannot freeze the first
        # frame. New session ids are unique, so this is safe beside discovery.
        sessions_root = WORK_ROOT / "data" / "sessions"
        recovery_ids = ({path.name for path in sessions_root.iterdir() if path.is_dir()}
                        if sessions_root.is_dir() else set())
        def recover_work():
            try:
                restored = recover_pending_overwrites(WORK_ROOT)
                recovered, error = restored+recover_incomplete_sessions(WORK_ROOT, recovery_ids), ""
            except Exception as exc:
                recovered, error = [], str(exc)
            self.recoveryReady.emit({"paths": [str(path) for path in recovered],
                                     "error": error})
        self._executor.submit(recover_work)

    @Property(str, notify=settingsChanged)
    def testBuildLabel(self):
        return str(self._preferences._settings.value("testing/build_label", ""))

    @Property(str, notify=settingsChanged)
    def testConditions(self):
        return str(self._preferences._settings.value("testing/conditions", ""))

    @Slot(str, str)
    def setTestContext(self, build_label, conditions):
        if self._collecting or self._starting_collection:
            return
        self._preferences._settings.setValue("testing/build_label", str(build_label).strip()[:120])
        self._preferences._settings.setValue("testing/conditions", str(conditions).strip()[:300])
        self._preferences._settings.sync()
        self.settingsChanged.emit()

    def _capture_settings(self, requested=None):
        if requested is None:
            requested = self._collector.enabled_metrics if self._collector else self._active_metric_keys()
        return dict(requested_metrics=sorted(requested), sample_interval_ms=self._preferences.sample_interval_ms,
                    screenshots=self._preferences.screenshot_enabled,
                    screenshot_interval_ms=self._preferences.screenshot_interval_ms,
                    screenshot_max_width=self._preferences.screenshot_max_width,
                    build_label=self.testBuildLabel, conditions=self.testConditions,
                    windows_resource_scope=self._preferences.windows_resource_scope,
                    ios_fps_limit=self._selected_ios_fps_limit() if self._current_platform() is Platform.IOS else None,
                    collector_revision="merged-quality-v3")

    def _ios_fps_limit_setting(self):
        if not (0 <= self._selected_device < len(self._device_models)
                and 0 <= self._selected_target < len(self._target_models)):
            return "", 0
        device, target = self._device_models[self._selected_device], self._target_models[self._selected_target]
        if device.platform is not Platform.IOS:
            return "", 0
        bundle = ios_bundle_id({"id": target.id})
        digest = hashlib.sha256((device.id + "\0" + bundle).encode("utf-8")).hexdigest()[:24]
        return "ios/fps_limits/" + digest, DECLARED_GAME_FPS_LIMITS.get(bundle, 0)

    def _selected_ios_fps_limit(self):
        key, default = self._ios_fps_limit_setting()
        try:
            value = int(self._preferences._settings.value(key, default)) if key else 0
        except (TypeError, ValueError):
            value = default
        return value if value in (0, 30, 60, 90, 120, 240) else default

    @Property(bool, notify=stateChanged)
    def iosFpsLimitAvailable(self):
        return self._current_platform() is Platform.IOS

    @Property(int, notify=stateChanged)
    def iosFpsLimit(self):
        if self.viewingSession:
            try:
                return int((self._viewed_metadata.get("capture_settings") or {}).get("ios_fps_limit") or 0)
            except (TypeError, ValueError):
                return 0
        return self._selected_ios_fps_limit()

    @Slot(int)
    def setIosFpsLimit(self, value):
        if self._collecting or self._starting_collection or self.viewingSession:
            return
        key, _ = self._ios_fps_limit_setting()
        if not key or value not in (0, 30, 60, 90, 120, 240):
            return
        self._preferences._settings.setValue(key, value)
        self._preferences._settings.sync()
        self.settingsChanged.emit()
        self.stateChanged.emit()

    def _record_capture_settings(self):
        if not self._collecting:
            return
        spool = self._session_spool
        settings = self._capture_settings()
        if not self._screenshot_capture_settings:
            self._screenshot_capture_settings = dict(
                (spool._manifest.get("capture_settings") if spool else None) or settings)
        changes = list(self._screenshot_settings_changes)
        previous = changes[-1]["settings"] if changes else self._screenshot_capture_settings
        if settings == previous:
            return
        changes.append(dict(elapsed=self.duration, settings=settings))
        self._screenshot_settings_changes = changes[-100:]
        self._screenshot_settings_truncated |= len(changes) > 100
        if spool is not None:
            try:
                spool.checkpoint({"capture_settings_changes": self._screenshot_settings_changes,
                                  "capture_settings_changes_truncated": self._screenshot_settings_truncated})
            except OSError as error:
                self._notify("采集设置记录失败", str(error), "warning")
        self._sync_screenshot_selection()

    @Property(bool, notify=diagnosticsChanged)
    def diagnosticRunning(self): return self._diagnostic_running

    @Property("QVariantList", notify=diagnosticsChanged)
    def diagnosticResults(self): return self._diagnostic_results

    @Slot()
    def runConnectionCheck(self):
        if self._diagnostic_running or self._collecting or self._starting_collection:
            return
        self._diagnostic_running = True
        self._append_runtime_log("开始检查设备连接与采集环境")
        self._diagnostic_results = []
        self.diagnosticsChanged.emit()
        platform = (self._device_models[self._selected_device].platform.value
                    if 0 <= self._selected_device < len(self._device_models) else "")
        def work():
            try:
                results = connection_check(WORK_ROOT, platform)
            except Exception as error:
                results = [dict(name="连接检查", status="需处理", detail=str(error),
                                action="检查设备连接及完整的软件依赖。")]
            self.diagnosticsReady.emit(results)
        self._executor.submit(work)

    @Slot(object)
    def _accept_diagnostics(self, results):
        if self._shutting_down:
            return
        self._diagnostic_running = False
        self._diagnostic_results = list(results or [])
        for row in self._diagnostic_results:
            self._append_runtime_log("连接检查 · " + " · ".join(str(row.get(key) or "")
                                    for key in ("name", "status", "detail", "action")),
                                    "warning" if row.get("status") != "通过" else "info")
        self.diagnosticsChanged.emit()

    @Slot(str, str)
    def generateComparisonReport(self, baseline_id, candidate_id):
        if self._collecting or self._starting_collection or self._saving_session or self._report_working or self._merging_sessions:
            self._notify("暂时无法对比", "请先停止采集，并等待保存或报告生成完成。", "warning")
            return
        if baseline_id == candidate_id or not all(
                re.fullmatch(r"[A-Za-z0-9_-]+", item or "") for item in (baseline_id, candidate_id)):
            self._notify("无法对比", "请选择两份不同的本地采集记录。", "warning")
            return
        self._report_working = True
        self._status_text = "正在生成两次测试对比…"
        self.stateChanged.emit()
        def work():
            try:
                baseline = load_session(WORK_ROOT, baseline_id)
                candidate = load_session(WORK_ROOT, candidate_id)
                path = generate_comparison_report(baseline, candidate, WORK_ROOT / "exports")
                self.reportGenerated.emit({"ok": True, "path": str(path)})
            except Exception as error:
                self.reportGenerated.emit({"ok": False, "error": str(error)})
        self._executor.submit(work)

    @Slot(object)
    def _accept_recovery(self, result):
        if self._shutting_down:
            return
        self.refreshSessionRecords()
        error = str((result or {}).get("error") or "") if isinstance(result, dict) else ""
        if error:
            self._notify("异常采集恢复失败", error, "warning")

    @Property("QVariantList", notify=devicesChanged)
    def devices(self): return self._devices

    @Property("QVariantList", notify=targetsChanged)
    def targets(self): return self._targets

    @Property(bool, notify=stateChanged)
    def windowTargetsAvailable(self):
        return (0 <= self._selected_device < len(self._device_models)
                and self._device_models[self._selected_device].platform is Platform.WINDOWS)

    @Property(bool, notify=stateChanged)
    def canSelectPerformanceWindow(self):
        return (self.windowTargetsAvailable and not self._collecting and not self._busy
                and not self._starting_collection and not self._saving_session and not self._report_working)

    @Property("QVariantList", notify=windowTargetsChanged)
    def windowCandidates(self): return self._window_candidates

    @Property(int, notify=windowTargetsChanged)
    def windowPickSeconds(self): return self._window_pick_seconds

    @Property(str, notify=windowTargetsChanged)
    def windowPickStatus(self): return self._window_pick_status

    @Property(str, notify=windowTargetsChanged)
    def performanceWindowLabel(self):
        window = self._performance_window
        if window:
            return f"{window.get('title') or window.get('process_name')} · PID {window['pid']}"
        return "跟随上方选择的应用（默认主窗口）"

    @Property(str, notify=windowTargetsChanged)
    def screenshotWindowLabel(self):
        window = self._screenshot_window
        if window:
            return f"{window.get('title') or window.get('process_name')} · PID {window['pid']}"
        return "跟随性能采集目标"

    @Slot()
    def refreshWindowCandidates(self):
        if not self.windowTargetsAvailable or self._shutting_down:
            return
        try:
            self._window_candidates = windows_capture.list_visible_windows(exclude_process_id=os.getpid())
            self._window_candidates = [dict(row) for row in self._window_candidates
                                       if row.get('pid') != os.getpid()]
            self._window_pick_status = f"发现 {len(self._window_candidates)} 个可见窗口；选择后会再次验证进程身份。"
        except Exception:
            self._window_candidates = []
            self._window_pick_status = "无法获取窗口列表，请稍后刷新。"
        self.windowTargetsChanged.emit()

    def _verified_window(self, expected):
        if not isinstance(expected, dict):
            return None
        try:
            pid, hwnd = int(expected['pid']), int(expected['hwnd'])
            created = float(expected['create_time'])
            if pid == os.getpid() or pid <= 0 or not hwnd or not math.isfinite(created):
                return None
            actual = windows_capture.describe_window(hwnd, expected_pid=pid)
            if (not actual or int(actual.get('pid') or 0) != pid or int(actual.get('hwnd') or 0) != hwnd
                    or not math.isclose(float(actual.get('create_time')), created, rel_tol=0, abs_tol=.001)):
                return None
            return dict(actual)
        except Exception:
            return None

    def _invalidate_screenshot_target(self):
        self._screenshot_target_generation += 1
        self._screenshot_failure_reported = False
        self.windowTargetsChanged.emit()

    def _reset_window_targets(self):
        self.cancelWindowPick()
        self._performance_window = self._screenshot_window = None
        self._window_candidates = []
        self._invalidate_screenshot_target()

    def _bind_window(self, candidate, mode):
        if (mode not in {'performance', 'screenshot'} or not self.windowTargetsAvailable
                or self._shutting_down or (mode == 'performance' and not self.canSelectPerformanceWindow)):
            self._window_pick_status = "当前状态不可更改该窗口目标；请等待操作完成或停止性能采集。"
            self.windowTargetsChanged.emit()
            return False
        verified = self._verified_window(candidate)
        if verified is None:
            self._window_pick_status = "窗口已关闭、进程已变化或无法验证进程身份；请重新选择。"
            self.windowTargetsChanged.emit()
            return False
        if mode == 'performance':
            if self._viewing_platform is not None:
                self._clear_session_workspace("就绪")
            pid = int(verified['pid'])
            device = self._device_models[self._selected_device]
            index = next((i for i, target in enumerate(self._target_models) if target.process_id == pid), -1)
            title = str(verified.get('title') or verified.get('process_name') or f'PID {pid}')
            target = Target(id=f"windows:{pid}", device_id=device.id, name=f"{title} [PID {pid}]", process_id=pid)
            item = {"id": target.id, "name": target.name, "pid": pid, "icon": ""}
            if index < 0:
                index = len(self._target_models)
                self._target_models.append(target)
                self._targets.append(item)
            else:
                self._target_models[index] = target
                self._targets[index] = item
            self._selected_target = index
            self._performance_window = verified
            self._target_discovery_token += 1
            self.targetsChanged.emit()
            self.stateChanged.emit()
        else:
            self._screenshot_window = verified
        self._invalidate_screenshot_target()
        self._window_pick_status = "已设置性能采集窗口。" if mode == 'performance' else "已设置独立截图窗口；性能采集目标保持不变。"
        self.windowTargetsChanged.emit()
        return True

    @Slot(int, str, result=bool)
    def selectWindowCandidate(self, index, mode):
        self.cancelWindowPick()
        if 0 <= index < len(self._window_candidates):
            return self._bind_window(self._window_candidates[index], mode)
        return False

    @Slot()
    def followPerformanceScreenshot(self):
        self.cancelWindowPick()
        self._screenshot_window = None
        self._invalidate_screenshot_target()
        self._window_pick_status = "截图已恢复跟随性能采集目标。"
        self.windowTargetsChanged.emit()

    @Slot(str)
    def startWindowPick(self, mode):
        self.cancelWindowPick()
        if (mode not in {'performance', 'screenshot'} or not self.windowTargetsAvailable
                or self._shutting_down or (mode == 'performance' and not self.canSelectPerformanceWindow)):
            return
        self._window_pick_mode = mode
        self._window_pick_seconds = 3
        self._window_pick_status = "3 秒后拾取鼠标下的窗口，请把鼠标移到目标窗口；Esc 或取消可终止。"
        self._window_pick_timer.start()
        self.windowTargetsChanged.emit()

    @Slot()
    def cancelWindowPick(self):
        self._window_pick_timer.stop()
        if self._window_pick_mode:
            self._window_pick_status = "已取消窗口拾取。"
        self._window_pick_mode = ""
        self._window_pick_seconds = 0
        self.windowTargetsChanged.emit()

    def _window_pick_tick(self):
        mode = self._window_pick_mode
        if not mode or self._shutting_down:
            self.cancelWindowPick()
            return
        self._window_pick_seconds -= 1
        if self._window_pick_seconds > 0:
            self._window_pick_status = f"{self._window_pick_seconds} 秒后拾取，请将鼠标停在目标窗口。"
            self.windowTargetsChanged.emit()
            return
        self._window_pick_timer.stop()
        self._window_pick_mode = ""
        self._window_pick_seconds = 0
        try:
            candidate = windows_capture.window_at_cursor(exclude_process_id=os.getpid())
        except Exception:
            candidate = None
        if candidate is None:
            self._window_pick_status = "未拾取到可用的外部窗口（PerfZY 自身窗口已排除），请重试。"
            self.windowTargetsChanged.emit()
        else:
            self._bind_window(candidate, mode)

    @Property("QVariantList", notify=sessionRecordsChanged)
    def sessionRecords(self): return self._session_records

    @Slot()
    def refreshSessionRecords(self):
        root = WORK_ROOT
        self._session_records = load_session_records(root)
        self.sessionRecordsChanged.emit()
        self._sync_html_source_links()

    @Slot(str)
    def reuseCaptureConfiguration(self, session_id):
        if self.collecting or self.busy or self._starting_collection:
            self._notify("暂时无法沿用配置", self.busyReason or "请先停止当前采集。", "warning")
            return
        self._configuration_request += 1
        request = self._configuration_request

        def work():
            try:
                session = load_session(WORK_ROOT, session_id)
                if not session:
                    raise ValueError("采集记录不存在或无法读取。")
                platform = Platform(canonical_platform(session.get("device") or {}))
                settings, metrics = reusable_capture_configuration(session)
                result = (request, platform, settings, metrics, "")
            except (OSError, ValueError, TypeError, AttributeError) as error:
                result = (request, None, {}, set(), str(error))
            if not self._shutting_down:
                try:
                    self.captureConfigurationReady.emit(result)
                except RuntimeError:
                    pass

        self._executor.submit(work)

    @Slot(object)
    def _accept_capture_configuration(self, result):
        request, platform, settings, metrics, error = result
        if self._shutting_down or request != self._configuration_request:
            return
        if error:
            self._notify("无法沿用配置", error, "warning")
            return
        if self.collecting or self.busy or self._starting_collection:
            self._notify("暂时无法沿用配置", self.busyReason or "请先停止当前采集。", "warning")
            return
        if self._selected_device >= 0 and self._current_platform() != platform:
            name = {Platform.WINDOWS: "Windows 本机", Platform.ANDROID: "安卓设备", Platform.IOS: "iOS 设备"}[platform]
            self._notify("请先选择对应设备", f"这条记录来自 {name}。请在常规测试选择对应设备后再沿用。", "warning")
            return
        chosen = {key for key in metrics if key in METRIC_BY_KEY and platform in METRIC_BY_KEY[key].platforms}
        groups = {group for key in chosen if (group := group_key_for_metric(key, platform))}
        if not groups:
            self._notify("无法沿用配置", "这条记录没有可复用的采集指标。", "warning")
            return

        preferences = self._preferences
        enabled, visible = preferences.enabled_for(platform), preferences.visible_for(platform)
        enabled.clear()
        enabled.update(chosen)
        visible.clear()
        visible.update(groups)
        for field, setter in (("sample_interval_ms", preferences.set_sample_interval_ms),
                              ("screenshot_interval_ms", preferences.set_screenshot_interval_ms)):
            try:
                value = int(settings[field])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if value > 0:
                setter(value)
        if isinstance(settings.get("screenshots"), bool):
            preferences.screenshot_enabled = settings["screenshots"]
        width = settings.get("screenshot_max_width")
        if type(width) is int and width in VALID_SCREENSHOT_WIDTHS:
            preferences.screenshot_max_width = width
        scope = settings.get("windows_resource_scope")
        if platform is Platform.WINDOWS and scope in ("selected", "tree"):
            preferences.windows_resource_scope = scope
        self._timer.setInterval(preferences.sample_interval_ms)
        self._screenshot_timer.setInterval(preferences.screenshot_interval_ms)
        preferences.save()
        self._metric_status = {}
        self._metric_misses = {}
        self._status_text = "已沿用记录的采集配置，可以开始复测"
        self.settingsChanged.emit()
        self.chartLayoutChanged.emit()
        self.stateChanged.emit()
        self.captureConfigurationApplied.emit()

    @Property("QVariantList", notify=htmlReportsChanged)
    def htmlReports(self): return self._html_reports

    @Property(bool, notify=htmlReportsChanged)
    def htmlReportsLoading(self): return self._html_reports_loading

    def _schedule_html_refresh(self, *_args):
        if not self._shutting_down:
            self._html_refresh_timer.start(300)

    @Slot()
    def refreshHtmlReports(self):
        if self._shutting_down:
            return
        if self._html_reports_loading:
            self._html_refresh_pending = True
            return
        root = WORK_ROOT
        folder = root / 'exports'
        try:
            folder.mkdir(parents=True, exist_ok=True)
            watched = str(folder.resolve())
            if watched not in self._html_watcher.directories():
                self._html_watcher.addPath(watched)
        except OSError as error:
            self._notify('无法读取报告目录', str(error), 'warning')
            return
        self._html_reports_loading = True
        self.htmlReportsChanged.emit()
        cache, sessions = self._html_report_cache, list(self._session_records)
        def work():
            try:
                rows, updated = load_html_reports(root, cache=cache, sessions=sessions)
                result = {'ok': True, 'rows': rows, 'cache': updated}
            except (OSError, ValueError) as error:
                result = {'ok': False, 'error': str(error)}
            if not self._shutting_down:
                try:
                    self.htmlReportsReady.emit(result)
                except RuntimeError:
                    pass  # The application can close during a directory read.
        self._executor.submit(work)

    @Slot(object)
    def _accept_html_reports(self, result):
        if self._shutting_down:
            return
        self._html_reports_loading = False
        if isinstance(result, dict) and result.get('ok'):
            self._html_reports = result['rows']
            self._html_report_cache = result['cache']
            self._sync_html_source_links(emit=False)
        else:
            self._notify('无法读取报告目录', str((result or {}).get('error') or '未知错误'), 'warning')
        self.htmlReportsChanged.emit()
        if self._html_refresh_pending:
            self._html_refresh_pending = False
            self._html_refresh_timer.start(0)

    def _sync_html_source_links(self, *, emit=True):
        sources = {str(row.get('id')): row for row in self._session_records}
        rows = [link_report_source(row, sources.get(row.get('sourceSessionId'), {}))
                for row in self._html_reports]
        if rows != self._html_reports:
            self._html_reports = rows
            if emit:
                self.htmlReportsChanged.emit()

    @Slot(str, result=bool)
    def openHtmlReport(self, identifier):
        try:
            path = report_path(WORK_ROOT, identifier)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError('未能启动默认浏览器')
        except (OSError, ValueError) as error:
            self._notify('无法打开报告', str(error), 'warning')
            return False
        return True

    @Slot(str, str, result=str)
    def renameHtmlReport(self, identifier, title):
        if self._report_working:
            self._notify('正在生成报告', '生成完成后再修改报告文件名。', 'warning')
            return ''
        try:
            path = rename_html_report(WORK_ROOT, identifier, title)
        except (OSError, ValueError) as error:
            self._notify('无法修改文件名', str(error), 'warning')
            return ''
        self.refreshHtmlReports()
        return path.name

    @Slot(str, result=bool)
    def deleteHtmlReport(self, identifier):
        if self._report_working:
            self._notify('正在生成报告', '生成完成后再删除报告。', 'warning')
            return False
        try:
            delete_html_report(WORK_ROOT, identifier)
        except (OSError, ValueError) as error:
            self._notify('无法删除报告', str(error), 'warning')
            return False
        self.refreshHtmlReports()
        return True

    @Property(bool, notify=stateChanged)
    def sessionsMerging(self):
        return self._merging_sessions

    @Slot("QVariantList", str, result=bool)
    def mergeSessions(self, session_ids, name):
        if self._record_overwrite_busy():
            self._notify("正在保存记录","请等待记录保存完成后再合并。","warning")
            return False
        if self._collecting or self._starting_collection or self._saving_session or self._report_working or self._merging_sessions:
            self._notify("暂时无法合并", "请先停止采集，并等待保存、合并或报告生成完成。", "warning")
            return False
        identifiers = [str(value) for value in session_ids]
        if (len(identifiers) < 2 or len(set(identifiers)) != len(identifiers)
                or not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in identifiers)):
            self._notify("无法合并", "请选择至少两条不同的本地采集记录。", "warning")
            return False
        self._merging_sessions = True
        self._status_text = f"正在合并 {len(identifiers)} 条采集记录…"
        self.stateChanged.emit()
        def work():
            try:
                path = merge_sessions(WORK_ROOT, identifiers, str(name))
                result = {"ok": True, "id": path.parent.name}
            except Exception as error:
                result = {"ok": False, "error": str(error)}
            if not self._shutting_down:
                self.sessionMergeReady.emit(result)
        self._executor.submit(work)
        return True

    @Slot(object)
    def _accept_sessions_merged(self, result):
        self._merging_sessions = False
        if self._shutting_down:
            return
        if not isinstance(result, dict) or not result.get("ok"):
            self._status_text = "记录合并失败"
            self.stateChanged.emit()
            self._notify("记录合并失败", str((result or {}).get("error") or "未知错误"))
            return
        self.refreshSessionRecords()
        identifier = str(result["id"])
        record = next((row for row in self._session_records if row["id"] == identifier), {})
        self._status_text = f"合并完成：{record.get('name') or identifier}"
        self.stateChanged.emit()
        self.sessionsMerged.emit(identifier)

    @Slot(str, str, result=bool)
    def renameSession(self, session_id, name):
        if self._record_overwrite_busy():
            self._notify("正在保存记录","请等待记录保存完成后再重命名。","warning")
            return False
        try:
            cleaned = rename_session(WORK_ROOT, str(session_id), str(name))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._notify("重命名失败", str(error))
            return False
        self.refreshSessionRecords()
        if self._session_id == str(session_id):
            self._status_text = f"当前采集：{cleaned}"
            self.stateChanged.emit()
        return True

    @Slot(str, str, result=bool)
    def renameSessionAndGenerateReport(self, session_id, name):
        if not self.renameSession(str(session_id), str(name)):
            return False
        self._generate_report_for(str(session_id))
        return True

    @Slot()
    def openLocalSessionsFolder(self):
        sessions_path = WORK_ROOT / "data" / "sessions"
        try:
            sessions_path.mkdir(parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(sessions_path)))
        except OSError as error:
            opened = False
            detail = str(error)
        else:
            detail = str(sessions_path)
        if not opened:
            self._notify("无法打开本地用例目录",
                         f"请检查目录访问权限：{detail}")

    @Slot()
    def openReportsFolder(self):
        reports_path = WORK_ROOT / "exports"
        try:
            reports_path.mkdir(parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(reports_path)))
        except OSError as error:
            opened = False
            detail = str(error)
        else:
            detail = str(reports_path)
        if not opened:
            self._notify("无法打开 HTML 报告目录",
                         f"请检查目录访问权限：{detail}")

    @Slot(str, result=bool)
    def deleteSession(self, session_id):
        if self._record_overwrite_busy():
            self._notify("正在保存记录","请等待记录保存完成后再删除。","warning")
            return False
        session_id = str(session_id)
        if self._merging_sessions:
            self._notify("合并中无法删除记录", "请等待记录合并完成后重试。", "warning")
            return False
        if self._collecting or self._saving_session:
            self._notify("采集中无法删除记录", "请先停止当前采集并等待数据保存完成。", "warning")
            return False
        if self._report_working:
            self._notify("报告生成中无法删除记录", "请等待报告生成完成后重试。", "warning")
            return False
        try:
            delete_saved_session(WORK_ROOT, session_id)
        except (OSError, ValueError) as error:
            self._notify("删除记录失败", str(error))
            return False
        if self._session_id == session_id:
            self._clear_session_workspace("当前采集记录已删除")
        self.refreshSessionRecords()
        return True

    @Slot(object)
    def _accept_session_saved(self, result):
        self._saving_session = False
        if self._shutting_down:
            return
        if isinstance(result, dict) and not result.get("ok", False):
            message = str(result.get("error") or "未知错误")
            self._status_text = "本地保存失败"
            self.stateChanged.emit()
            self._notify("数据保存失败",
                         f"无法写入本地会话文件：{message}。请检查磁盘空间和目录写入权限。")
            return
        saved_name = str((result or {}).get("name") or "") if isinstance(result, dict) else ""
        self._status_text = f"已保存：{saved_name}" if saved_name else "已停止，数据已保存到本机"
        self.refreshSessionRecords()
        self.stateChanged.emit()
        session_id = str((result or {}).get("id") or "") if isinstance(result, dict) else ""
        if session_id:
            self.sessionNamingRequested.emit(session_id, saved_name)

    @Property(bool, notify=stateChanged)
    def reportGenerating(self):
        return self._report_working

    @Property(bool, notify=stateChanged)
    def canGenerateReport(self):
        if self._collecting or self._saving_session or self._report_working or self._merging_sessions or not self._session_id:
            return False
        root = WORK_ROOT
        return (root / "data" / "sessions" / self._session_id / "session.json").is_file()

    @Slot()
    def generateLocalReport(self):
        self._generate_report_for(self._session_id)

    @Property(str, notify=stateChanged)
    def reportScreenshotMode(self):
        return self._preferences.report_screenshot_mode

    @Slot(str)
    def setReportScreenshotMode(self, mode):
        if mode not in {"none", "compressed", "original"}:
            return
        self._preferences.report_screenshot_mode = mode
        self._preferences.save()
        self.stateChanged.emit()

    @Slot(str)
    def generateReportForSession(self, session_id):
        self._generate_report_for(str(session_id))

    def _generate_report_for(self, session_id: str):
        if self._record_overwrite_busy():
            self._notify("正在保存记录","请等待记录保存完成后再生成报告。","warning")
            return
        if self._merging_sessions:
            self._notify("正在合并记录", "请等待合并完成后再生成报告。", "warning")
            return
        if self._collecting or self._saving_session:
            self._notify("采集中无法生成报告", "请先停止采集并等待数据保存完成。", "warning")
            return
        if self._report_working:
            return
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(session_id or "")):
            self._notify("采集记录编号无效", "无法定位该报告的本地数据。")
            return
        root = WORK_ROOT
        session_path = root / "data" / "sessions" / session_id / "session.json"
        if not session_id or not session_path.is_file():
            self._notify("暂无可生成报告的数据", "请先完成一次采集并停止，等待数据保存到本地。", "warning")
            return
        self._report_working = True
        self._status_text = "正在生成本地测试报告…"
        screenshot_mode = self._preferences.report_screenshot_mode
        self.stateChanged.emit()

        def work():
            try:
                path = generate_local_report(session_path, root / "exports", screenshot_mode=screenshot_mode)
                self.reportGenerated.emit({"ok": True, "path": str(path)})
            except Exception as error:
                self.reportGenerated.emit({"ok": False, "error": str(error)})

        self._executor.submit(work)

    @Slot(str)
    def openSessionFolder(self, session_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(session_id or "")):
            self._notify("无法打开会话目录", "采集记录编号无效")
            return
        root = WORK_ROOT
        folder = root / "data" / "sessions" / str(session_id)
        if not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self._notify("无法打开会话目录", f"目录不存在或没有访问权限：{folder}")

    @Slot(object)
    def _accept_report_generated(self, result):
        self._report_working = False
        if self._shutting_down:
            return
        if not isinstance(result, dict) or not result.get("ok"):
            self._status_text = "报告生成失败"
            self.stateChanged.emit()
            self._notify("报告生成失败", str((result or {}).get("error") or "未知错误"))
            return
        path = Path(str(result.get("path") or ""))
        self._status_text = "报告已生成"
        self.refreshHtmlReports()
        self.stateChanged.emit()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._notify("报告已生成但无法自动打开", f"报告位置：{path}", "warning")

    def _notify(self, title: str, message: str, level: str = "error") -> None:
        self._append_runtime_log(f"{title}\n{message}", level)
        self.notificationRequested.emit(title, message, level)

    def _append_runtime_log(self, message: str, level: str = "info") -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        label = {"info": "信息", "warning": "警告", "error": "错误"}.get(level, "信息")
        self._runtime_log.append(f"[{stamp}] [{label}] {message}")
        self.runtimeLogChanged.emit()

    @Slot()
    def _record_status_log(self):
        if self._status_text and self._status_text != self._last_logged_status:
            self._last_logged_status = self._status_text
            error = any(word in self._status_text for word in ("失败", "错误", "中断"))
            warning = self._collecting and self._status_text not in ("正在采集", "已暂停")
            self._append_runtime_log(self._status_text, "error" if error else "warning" if warning else "info")

    @Property(str, notify=runtimeLogChanged)
    def runtimeLogText(self):
        return "\n\n".join(self._runtime_log)

    @Slot(str, result=bool)
    def copyLogText(self, text):
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(str(text))
        return True

    @Slot(str)
    def openSession(self, session_id):
        if self._record_viewer is not None:
            self._record_viewer.openSession(session_id)
            return
        if self._collecting or self._saving_session:
            self._notify("采集中无法查看记录",
                         "请先点击“停止”，等待当前数据保存完成后再打开历史记录。")
            return
        payload = load_session(WORK_ROOT, str(session_id))
        if not payload:
            self._status_text = "无法读取该采集记录"
            self.stateChanged.emit()
            self._notify("记录读取失败", "本地会话文件不存在、已损坏或没有读取权限。")
            return
        if self._viewing_platform is None:
            self._pre_view_selection = self._preferences.snapshot_selection()
            self._pre_view_visible_charts = set(self._preferences.visible_charts)
            self._pre_view_windows_charts = set(self._preferences.windows_visible_charts)
            self._pre_view_ios_charts = set(self._preferences.ios_visible_charts)
        loaded_samples = payload.get("samples") or {}
        self._saved_telemetry = payload.get('telemetry') or {}
        self._viewed_metadata = {k: payload.get(k) for k in ('device', 'target', 'name', 'duration', 'capture_settings', 'started_at', 'capture_settings_changes', 'capture_settings_changes_truncated', 'excluded_intervals', 'timeline_cuts', 'edited_from')}
        device_id = str((payload.get("device") or {}).get("id") or "")
        self._viewing_platform = (Platform.ANDROID if device_id.startswith("android:") else
                                  Platform.IOS if device_id.startswith("ios:") else Platform.WINDOWS)
        # Reuse decoded containers. Copying every list temporarily doubled the
        # memory spike when opening an hour-long recording.
        self._samples = {item.key: loaded_samples.get(item.key) or [] for item in METRICS}
        self._reset_chart_view_range()
        self._screenshots = payload.get("screenshots") or []
        self._scenes = payload.get("scenes") or []
        # Reveal exactly what this recording contains, using the same chart
        # definitions the live workspace uses.
        self._preferences.enabled_for(self._viewing_platform).update(
            key for key, rows in self._samples.items() if rows)
        self._preferences.visible_for(self._viewing_platform).update(
            groups_with_data(self._samples, self._viewing_platform))
        self._selection = (-1.0, -1.0)
        self._locked_time = -1.0
        self._selected_screenshot = len(self._screenshots) - 1 if self._screenshots else -1
        self._session_id = str(payload.get("id") or session_id)
        target = payload.get("target") or {}
        session_name = normalize_session_name(payload.get("name"))
        self._status_text = f"正在查看：{session_name or target.get('name') or self._session_id}"
        self.samplesChanged.emit(); self.timelineChanged.emit(); self.screenshotsChanged.emit(); self.scenesChanged.emit()
        self.selectionChanged.emit(); self.timeLockChanged.emit(); self.settingsChanged.emit()
        self.chartLayoutChanged.emit(); self.stateChanged.emit()
        self.sessionOpened.emit({"id": self._session_id, "name": session_name,
                                 "target": target.get("name", "")})

    @Property(bool, notify=stateChanged)
    def viewingSession(self):
        return self._viewing_platform is not None

    @Slot()
    def closeSessionView(self):
        if self._viewing_platform is None or self._collecting:
            return
        self._clear_session_workspace("就绪")

    def _clear_session_workspace(self, status_text: str) -> None:
        """Drop references to a loaded/deleted session and restore live preferences."""
        self._viewing_platform = None
        if self._pre_view_selection is not None:
            self._preferences.restore_selection(self._pre_view_selection)
        if self._pre_view_visible_charts is not None:
            self._preferences.visible_charts = self._pre_view_visible_charts
        if self._pre_view_windows_charts is not None:
            self._preferences.windows_visible_charts = self._pre_view_windows_charts
        if self._pre_view_ios_charts is not None:
            self._preferences.ios_visible_charts = self._pre_view_ios_charts
        self._pre_view_selection = None
        self._pre_view_visible_charts = None
        self._pre_view_windows_charts = None
        self._pre_view_ios_charts = None
        self._metric_status = {}; self._metric_misses = {}
        self._last_metric_diagnostics = {}
        self._sampling_health = SamplingHealth(self.sampleIntervalSeconds)
        self.discardSceneMark()
        self._samples = {item.key: [] for item in METRICS}
        self._reset_chart_view_range()
        self._screenshots = []
        self._scenes = []
        self._selection = (-1.0, -1.0)
        self._locked_time = -1.0
        self._selected_screenshot = -1
        self._session_id = ""
        self._session_started_at = ""
        self._status_text = status_text
        self.samplesChanged.emit(); self.timelineChanged.emit(); self.screenshotsChanged.emit(); self.scenesChanged.emit()
        self.selectionChanged.emit(); self.timeLockChanged.emit(); self.settingsChanged.emit()
        self.chartLayoutChanged.emit(); self.stateChanged.emit()

    @Property("QVariantList", notify=settingsChanged)
    def metricCatalog(self):
        platform = self._current_platform()
        return build_metric_catalog(platform, self._enabled_metrics(),
                                    self._core_count(), self._current_metric_status())

    def _current_metric_status(self):
        if self._current_platform() not in {Platform.WINDOWS, Platform.IOS, Platform.ANDROID}:
            return {}
        if self._viewing_platform is not None:
            return {item.key: {"state": "available" if self._samples.get(item.key) else "unavailable",
                              "label": "已记录" if self._samples.get(item.key) else "未记录",
                              "reason": "仅查看历史数据，不会重新采集。"}
                    for item in METRICS if self._current_platform() in item.platforms}
        return self._metric_status

    def _enabled_metrics(self) -> set[str]:
        """Live metric selection for the platform currently on screen."""
        return self._preferences.enabled_for(self._current_platform())

    def _metric_title(self, key: str) -> str:
        return platform_metric_title(key, self._current_platform())

    def _core_count(self) -> int:
        if 0 <= self._selected_device < len(self._device_models):
            device = self._device_models[self._selected_device]
            try:
                return max(0, int(device.details.get("cpu_cores", 0)))
            except (TypeError, ValueError):
                return 0
        recorded_cores = [index for key, rows in self._samples.items() if rows
                          for index in (core_index(key),) if index is not None]
        return max(recorded_cores, default=-1) + 1

    def _active_metric_keys(self) -> frozenset[str]:
        """Metrics that are both selected and currently displayed."""
        visible_keys = {
            metric_key
            for chart in self._build_chart_catalog()
            if chart["visible"]
            for metric_key in chart["metricKeys"]
        }
        platform = self._current_platform()
        selection = self._enabled_metrics()
        core_count = self._core_count()
        return frozenset(
            item.key for item in METRICS
            if platform in item.platforms
            and item.key in selection
            and item.key in visible_keys
            and metric_available(item.key, core_count)
        )

    def _push_enabled_metrics(self) -> None:
        """Hand the current selection to a running collector.

        The metric gate is applied immediately so the next sample honours it,
        while starting a newly needed device-side data source is dispatched to
        a worker: on Android it costs several blocking adb round-trips.
        """
        collector = self._collector
        if collector is None or self._shutting_down:
            return
        collector.set_enabled_metrics(self._active_metric_keys())
        try:
            self._executor.submit(collector.activate_pending_sources)
        except RuntimeError:
            # The application may be closing while a final QML change signal
            # is delivered. The executor has already been shut down then.
            return

    @Property("QVariantList", notify=chartLayoutChanged)
    def chartCatalog(self):
        return [item for item in self._build_chart_catalog() if item["visible"]]

    @Property("QVariantList", notify=chartLayoutChanged)
    def chartManagerCatalog(self):
        return self._build_chart_catalog()

    def _build_chart_catalog(self):
        return build_chart_catalog(self._current_platform(), self._preferences.chart_order,
                                   self._preferences.visible_for(self._current_platform()), self._enabled_metrics(),
                                   self._core_count(), self._current_metric_status())

    @Property(bool, notify=settingsChanged)
    def screenshotEnabled(self): return self._preferences.screenshot_enabled

    @Property(float, notify=settingsChanged)
    def sampleIntervalSeconds(self): return self._preferences.sample_interval_ms / 1000.0

    @Slot()
    def _refresh_collection_health(self):
        if self._collecting or self._starting_collection:
            self.collectionHealthChanged.emit()
            if self._marking_scene_start is not None:
                self.sceneMarkChanged.emit()

    @Property('QVariantList', notify=collectionHealthChanged)
    def collectionMetricStatus(self):
        return collection_metric_status(
            self._active_metric_keys(), self._samples, self._current_metric_status(),
            {} if self.viewingSession else self._sampling_health.fields,
            self.duration, self.sampleIntervalSeconds,
            collecting=self._collecting, paused=self._paused, starting=self._starting_collection)

    @Property(str, notify=collectionHealthChanged)
    def collectionMetricDetails(self):
        details = []
        collector = (self._saved_telemetry.get('collector') or {}) if self.viewingSession else self._last_metric_diagnostics
        provenance = (self._saved_telemetry.get('provenance') or {}) if self.viewingSession else metric_provenance(
            self._current_platform(), self._active_metric_keys(), (collector or {}).get('process_scope'), collector_details=collector)
        for item in self.collectionMetricStatus:
            if item['state'] == 'disabled':
                continue
            source = provenance.get(item['key']) or {}
            channel = (collector.get('metric_sources') or {}).get(item['key'])
            details.append(f"{item['title']} / {self._metric_title(item['key'])}：{item['label']}\n"
                           f"{item['age_text']}\n{item['reason']}\n"
                           f"通道说明：{source.get('source') or '历史记录未保存来源'}\n"
                           f"统计范围：{source.get('scope') or '未记录'}"
                           + (f"\n实际采集通道：{channel}" if channel else ''))
        if self._current_platform() is Platform.WINDOWS:
            details.append(self.windowsResourceScopeText)
        return '\n\n'.join(details) or '请先选择要采集的指标。'

    @Property(bool, notify=settingsChanged)
    def windowsResourceScopeAvailable(self):
        return self._current_platform() is Platform.WINDOWS

    @Property(str, notify=settingsChanged)
    def windowsResourceScope(self):
        if self._viewing_platform is not None:
            scope = (self._saved_telemetry.get('collector') or {}).get('process_scope') or {}
            mode = scope.get('resource_scope') or scope.get('resource_mode')
            if mode in {'selected', 'tree'}:
                return mode
            return 'selected' if scope.get('cpu_memory_io_threads') == 'selected PID only' else ''
        return self._preferences.windows_resource_scope

    @Property(str, notify=settingsChanged)
    def windowsResourceScopeText(self):
        mode = self.windowsResourceScope
        prefix = '历史记录资源范围：' if self._viewing_platform is not None else 'Windows 资源范围：'
        return prefix + ('仅当前进程' if mode == 'selected' else '包含子进程' if mode == 'tree' else '未记录，不能按当前设置推断')

    @Slot(str)
    def setWindowsResourceScope(self, scope):
        if self._collecting or self._starting_collection or self._viewing_platform is not None:
            return
        if scope not in {'selected', 'tree'}:
            return
        self._preferences.windows_resource_scope = scope
        self._preferences.save()
        self.settingsChanged.emit()

    @Property('QStringList', notify=settingsChanged)
    def scenePresets(self):
        return list(self._preferences.scene_presets)

    @Slot(str)
    def rememberScenePreset(self, name):
        self._preferences.remember_scene_preset(name)
        self._preferences.save()
        self.settingsChanged.emit()

    @Property(bool, notify=sceneMarkChanged)
    def markingScene(self): return self._marking_scene_start is not None

    @Property(float, notify=sceneMarkChanged)
    def markingSceneStart(self): return self._marking_scene_start if self.markingScene else 0.0

    @Property(str, notify=sceneMarkChanged)
    def markingSceneName(self): return self._marking_scene_name

    @Slot(str, result=bool)
    def toggleSceneMark(self, name):
        if not self._collecting or self._paused:
            return False
        if self.markingScene:
            return self.finishSceneMark()
        self._marking_scene_name = normalize_session_name(name)[:40] or f'标记 {len(self._scenes)+1}'
        self._marking_scene_start = self.duration
        self.sceneMarkChanged.emit()
        return True

    @Slot(result=bool)
    def finishSceneMark(self):
        if not self.markingScene:
            return True
        end = self.duration
        if end <= self._marking_scene_start:
            return False
        self.addScene(self._marking_scene_name, self._marking_scene_start, end)
        self.rememberScenePreset(self._marking_scene_name)
        self.discardSceneMark()
        return True

    @Slot()
    def discardSceneMark(self):
        self._marking_scene_start = None
        self._marking_scene_name = ''
        self.sceneMarkChanged.emit()

    @Property(float, notify=settingsChanged)
    def screenshotIntervalSeconds(self): return self._preferences.screenshot_interval_ms / 1000.0

    @Property(int, notify=settingsChanged)
    def screenshotMaxWidth(self): return self._preferences.screenshot_max_width

    def _current_platform(self):
        if self._viewing_platform is not None:
            return self._viewing_platform
        if 0 <= self._selected_device < len(self._device_models):
            return self._device_models[self._selected_device].platform
        return Platform.WINDOWS

    @Property("QVariantMap", notify=samplesChanged)
    def chartData(self): return self._samples

    @Property("QVariantList", notify=stateChanged)
    def excludedIntervals(self):
        return (self._viewed_metadata.get("excluded_intervals") or []) if self.viewingSession else []

    @Property("QVariantList", notify=stateChanged)
    def timelineCuts(self):
        return (self._viewed_metadata.get("timeline_cuts") or []) if self.viewingSession else []

    @Slot(float, result=bool)
    def isExcludedTime(self, elapsed):
        return any(row["start"] <= elapsed < row["end"] for row in self.excludedIntervals)

    def _crosses_excluded(self, first, last):
        first, last = sorted((first, last))
        return (any(first < row["end"] and last >= row["start"] for row in self.excludedIntervals)
                or any(first < row["time"] <= last for row in self.timelineCuts))

    @Property(float, notify=chartRangeChanged)
    def chartViewStartRatio(self): return self._chart_view_start_ratio

    @Property(float, notify=chartRangeChanged)
    def chartViewEndRatio(self): return self._chart_view_end_ratio

    @Slot(float, float)
    def setChartViewRange(self, start_ratio, end_ratio):
        try:
            start_ratio, end_ratio = float(start_ratio), float(end_ratio)
        except (TypeError, ValueError):
            return
        if not math.isfinite(start_ratio) or not math.isfinite(end_ratio):
            return
        start_ratio, end_ratio = sorted((max(0.0, min(1.0, start_ratio)),
                                         max(0.0, min(1.0, end_ratio))))
        if end_ratio - start_ratio < .002:
            return
        if (abs(start_ratio - self._chart_view_start_ratio) < 1e-6 and
                abs(end_ratio - self._chart_view_end_ratio) < 1e-6):
            return
        self._chart_view_start_ratio = start_ratio
        self._chart_view_end_ratio = end_ratio
        self.chartRangeChanged.emit()

    def _reset_chart_view_range(self):
        if self._chart_view_start_ratio == 0.0 and self._chart_view_end_ratio == 1.0:
            return
        self._chart_view_start_ratio = 0.0
        self._chart_view_end_ratio = 1.0
        self.chartRangeChanged.emit()

    @Property("QVariantList", notify=screenshotsChanged)
    def screenshots(self): return self._screenshots

    @Property(int, notify=timeLockChanged)
    def selectedScreenshotIndex(self): return self._selected_screenshot

    def _screenshot_match_at(self, elapsed):
        if self.isExcludedTime(elapsed):
            return ScreenshotMatch(reason="removed")
        if self.viewingSession:
            metadata = self._viewed_metadata
            settings = metadata.get('capture_settings') or {}
            changes = metadata.get('capture_settings_changes') or []
            truncated = bool(metadata.get('capture_settings_changes_truncated'))
        else:
            settings = self._screenshot_capture_settings or self._capture_settings()
            changes = self._screenshot_settings_changes
            truncated = self._screenshot_settings_truncated
        if self.excludedIntervals or self.timelineCuts:
            indices = [index for index, shot in enumerate(self._screenshots)
                       if not self._crosses_excluded(float(shot.get("seconds", 0)), elapsed)]
            match = match_screenshot([self._screenshots[index] for index in indices], elapsed,
                                     settings, changes, truncated=truncated)
            return ScreenshotMatch(indices[match.index] if match.index >= 0 else -1, match.reason)
        return match_screenshot(self._screenshots, elapsed, settings, changes, truncated=truncated)

    def _sync_screenshot_selection(self):
        if self.hasTimeLock:
            self._selected_screenshot = self._screenshot_match_at(self._locked_time).index
        elif self._collecting and not self._preferences.screenshot_enabled:
            self._selected_screenshot = -1
        else:
            self._selected_screenshot = len(self._screenshots) - 1 if self._screenshots else -1
        self.timeLockChanged.emit()

    @Property(str, notify=screenshotStateChanged)
    def screenshotStatusText(self):
        if self.hasTimeLock:
            match = self._screenshot_match_at(self._locked_time)
            if match.index < 0:
                if match.reason == 'removed': return '该时段数据和截图已移除'
                return '该时段截图已关闭' if match.reason == 'disabled' else '该时段暂无截图'
        if self._collecting and not self._preferences.screenshot_enabled:
            return '截图已关闭 · 采集已暂停' if self._paused else '截图已关闭 · 性能指标继续采集'
        if self._collecting and not self._screenshots:
            return '正在等待首张截图'
        if self.viewingSession and not self._screenshots:
            return '本次记录未包含截图'
        return ''

    @Property("QVariantList", notify=scenesChanged)
    def scenes(self): return self._scenes

    @Property(bool, notify=timeLockChanged)
    def hasTimeLock(self): return self._locked_time >= 0

    @Property(float, notify=timeLockChanged)
    def lockedTime(self): return self._locked_time

    @Property(float, notify=timeLockChanged)
    def lockedTimeRatio(self):
        return self._locked_time / max(self.duration, .001) if self.hasTimeLock else -1.0

    @Property(float, notify=timelineChanged)
    def duration(self):
        if not self._collecting or not self._started_at:
            observed = max((rows[-1]["x"] for rows in self._samples.values() if rows), default=0.0)
            saved = self._viewed_metadata.get('duration') if self.viewingSession else 0
            try:
                saved = float(saved or 0)
            except (TypeError, ValueError, OverflowError):
                saved = 0.0
            return max(observed, saved if math.isfinite(saved) else 0.0)
        now = self._pause_started if self._paused else time.monotonic()
        return max(0.0, now - self._started_at - self._paused_total)

    @Property(float, notify=timelineChanged)
    def chartDuration(self):
        """Timeline covered by accepted samples, excluding uncollected future time."""
        if self.viewingSession:
            return self.duration
        return max((float(rows[-1]["x"]) for rows in self._samples.values() if rows), default=0.0)

    @Property(bool, notify=selectionChanged)
    def hasSelection(self): return self._selection[0] >= 0 and self._selection[1] > self._selection[0]

    @Property(float, notify=selectionChanged)
    def selectionStartRatio(self):
        return self._selection[0] / max(self.duration, .001) if self.hasSelection else -1.0

    @Property(float, notify=selectionChanged)
    def selectionEndRatio(self):
        return self._selection[1] / max(self.duration, .001) if self.hasSelection else -1.0

    @Slot(float, float)
    def setSelectionRatios(self, start_ratio, end_ratio):
        try:
            start_ratio, end_ratio = float(start_ratio), float(end_ratio)
        except (TypeError, ValueError):
            return
        if not math.isfinite(start_ratio) or not math.isfinite(end_ratio):
            return
        start_ratio, end_ratio = sorted((max(0.0, min(1.0, start_ratio)), max(0.0, min(1.0, end_ratio))))
        # The chart distinguishes clicks from drags in screen pixels. A
        # full-session ratio cutoff rejects valid selections after zooming.
        if end_ratio <= start_ratio: return
        duration = max(self.duration, .001)
        self._selection = (start_ratio * duration, end_ratio * duration)
        self.selectionChanged.emit()

    @Slot()
    def clearSelection(self):
        self._selection = (-1.0, -1.0); self.selectionChanged.emit()

    @Slot(float)
    def setTimeLockRatio(self, ratio):
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            return
        if not math.isfinite(ratio):
            return
        ratio = max(0.0, min(1.0, ratio))
        candidate = ratio * max(self.duration, .001)
        if self.hasTimeLock and abs(candidate - self._locked_time) <= max(.08, self.duration * .006):
            self.clearTimeLock()
            return
        self._locked_time = candidate
        self._sync_screenshot_selection()

    @Slot()
    def clearTimeLock(self):
        self._locked_time = -1.0
        self._sync_screenshot_selection()

    @Slot(str, float, float)
    def addScene(self, name, start, end):
        interval = self._validated_scene_interval(start, end)
        if interval is None: return
        start, end = interval
        if end <= start: return
        title = normalize_session_name(name) or f"场景 {len(self._scenes) + 1}"
        identifiers = []
        for scene in self._scenes:
            try: identifiers.append(int(scene.get("id", 0)))
            except (TypeError, ValueError): pass
        scene_id = max(identifiers, default=0) + 1
        color = ("#4269d4", "#a45ac7", "#2e9d86", "#c98535", "#b74f66")[len(self._scenes) % 5]
        self._scenes.append({"id": scene_id, "name": title, "start": start, "end": end, "color": color,
                             "title": f"{title}  {self._format_time(start)}—{self._format_time(end)}"})
        self.scenesChanged.emit()

    @Slot(int, str, float, float)
    def updateScene(self, index, name, start, end):
        if not 0 <= index < len(self._scenes): return
        interval = self._validated_scene_interval(start, end)
        if interval is None: return
        start, end = interval
        if end <= start: return
        title = normalize_session_name(name) or f"场景 {index + 1}"
        color = self._scenes[index].get("color", "#4269d4")
        self._scenes[index] = {"id": self._scenes[index].get("id", index + 1), "name": title, "start": start, "end": end, "color": color,
                               "title": f"{title}  {self._format_time(start)}—{self._format_time(end)}"}
        self.scenesChanged.emit()

    @Slot(int, str, float, float, str)
    def updateSceneDetails(self, index, name, start, end, color):
        if not 0 <= index < len(self._scenes): return
        interval = self._validated_scene_interval(start, end)
        if interval is None: return
        start, end = interval
        if end <= start: return
        title = normalize_session_name(name) or f"标记 {index + 1}"
        chosen = str(color) if re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", str(color)) else "#4269d4"
        self._scenes[index] = {"id": self._scenes[index].get("id", index + 1), "name": title,
                               "start": start, "end": end, "color": chosen,
                               "title": f"{title}  {self._format_time(start)}—{self._format_time(end)}"}
        self.scenesChanged.emit()

    @Slot()
    def addSceneFromCurrentRange(self):
        if self.hasSelection:
            start, end = self._selection
        elif self.hasTimeLock:
            start = self._locked_time
            end = min(max(self.duration, .001), start + max(1.0, self.duration * .1))
        else:
            start, end = 0.0, max(self.duration, .001)
        self.addScene(f"场景 {len(self._scenes) + 1}", start, end)

    @Slot(int)
    def removeScene(self, index):
        if 0 <= index < len(self._scenes):
            self._scenes.pop(index); self.scenesChanged.emit()

    @Slot(int)
    def selectScene(self, index):
        if 0 <= index < len(self._scenes):
            scene = self._scenes[index]
            self._selection = (float(scene["start"]), float(scene["end"]))
            self.selectionChanged.emit()

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, remainder = divmod(max(0.0, seconds), 60)
        return f"{int(minutes):02d}:{remainder:05.2f}"

    def _validated_scene_interval(self, start, end) -> tuple[float, float] | None:
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(start) or not math.isfinite(end):
            return None
        start, end = sorted((max(0.0, start),
                             min(max(self.duration, .001), end)))
        return (start, end) if end > start else None

    @Slot("QVariantList", result="QVariantList")
    def selectionStats(self, keys):
        if not self.hasSelection: return []
        start, end = self._selection
        definitions = {item.key: item for item in METRICS}
        result = []
        for key in keys:
            rows = self._samples.get(str(key), [])
            first = bisect_left(rows, start, key=lambda row: row["x"])
            last = bisect_right(rows, end, key=lambda row: row["x"])
            count = last - first
            if count > 0:
                total = 0.0
                minimum = math.inf
                maximum = -math.inf
                for index in range(first, last):
                    value = rows[index]["y"]
                    total += value
                    minimum = min(minimum, value)
                    maximum = max(maximum, value)
                item = definitions.get(str(key))
                result.append({"title": self._metric_title(str(key)) if item else str(key),
                               "color": item.color if item else "#bcc6e2",
                               "avg": f"{total / count:.2f}",
                               "min": f"{minimum:.2f}", "max": f"{maximum:.2f}"})
        return result

    def _diagnostics_snapshot(self):
        if self._viewing_platform is not None:
            return dict(self._saved_telemetry)
        health = self._sampling_health.snapshot(self.duration)
        method = getattr(self._collector, 'diagnostics', None)
        health['collector'] = method() if callable(method) else {}
        health['measurement_notes'] = collection_quality_notes(health['collector'])
        health['provenance'] = metric_provenance(self._current_platform(), self._active_metric_keys(),
                                                health['collector'].get('process_scope'), collector_details=health['collector'])
        health['window_targets'] = {
            'performance': dict(self._performance_window) if self._performance_window else None,
            'screenshot': dict(self._screenshot_window) if self._screenshot_window else None,
            'screenshot_mode': 'independent' if self._screenshot_window else 'follow_performance',
        }
        return health

    @Slot(float, float)
    def focusChartInterval(self, start, end):
        duration = max(self.duration, .001)
        if not all(math.isfinite(v) for v in (start, end)):
            return
        start, end = max(0., start), min(duration, end)
        if end <= start:
            start, end = max(0., start - .5), min(duration, start + .5)
        if end <= start:
            return
        self._selection = (start, end)
        margin = max(.5, (end - start) * .2)
        self.setChartViewRange(max(0., start - margin) / duration,
                               min(duration, end + margin) / duration)
        self.selectionChanged.emit()
        self.setTimeLockRatio(start / duration)

    @Slot("QVariantList", result="QVariantList")
    def lockedValues(self, keys):
        if not self.hasTimeLock: return []
        if self.isExcludedTime(self._locked_time): return []
        definitions = {item.key: item for item in METRICS}
        result = []
        for key in keys:
            rows = self._samples.get(str(key), [])
            if not rows: continue
            right = bisect_left(rows, self._locked_time, key=lambda row: row["x"])
            candidates = [rows[index] for index in (right-1, right) if 0 <= index < len(rows)
                          and not self._crosses_excluded(rows[index]["x"], self._locked_time)]
            if not candidates: continue
            row = min(candidates, key=lambda row: abs(row["x"] - self._locked_time))
            item = definitions.get(str(key))
            result.append({"title": self._metric_title(str(key)) if item else str(key),
                           "color": item.color if item else "#bcc6e2",
                           "value": f'{row["y"]:.2f}', "unit": item.unit if item else ""})
        return result

    @Property(bool, notify=stateChanged)
    def collecting(self): return self._collecting

    @Property(bool, notify=stateChanged)
    def collectionStarting(self): return self._starting_collection

    @Property(bool, notify=stateChanged)
    def paused(self): return self._paused

    @Property(bool, notify=stateChanged)
    def busy(self): return self._busy or self._saving_session or self._report_working or self._merging_sessions or self._record_overwrite_busy()

    def _record_overwrite_busy(self):
        return bool(self._record_viewer is not None and self._record_viewer.overwriting)

    @Property(str, notify=stateChanged)
    def busyReason(self):
        if self._record_overwrite_busy(): return "正在保存记录，请稍候。"
        if self._saving_session: return "正在保存采集数据，请等待保存完成。"
        if self._merging_sessions: return "正在合并记录，请等待合并完成。"
        if self._report_working: return "正在生成报告，请等待报告完成。"
        if self._starting_collection: return "正在连接采集器，请等待连接完成。"
        if self._busy: return "正在刷新设备或应用列表，请稍候。"
        return ""

    @Property(str, notify=stateChanged)
    def statusSummary(self):
        if self._saving_session: return "正在保存数据"
        if self._merging_sessions: return "正在合并记录"
        if self._report_working: return "正在生成报告"
        if self._starting_collection: return "正在连接采集器"
        if self._collecting:
            if self._paused: return "采集已暂停"
            return "采集中 · 有提示" if self._status_text != "正在采集" else "正在采集"
        if self.viewingSession: return "正在查看记录"
        if self._busy: return "正在刷新列表"
        if any(word in self._status_text for word in ("失败", "无法", "错误", "中断")): return "操作遇到问题"
        for prefix, label in (("已保存", "数据已保存"), ("已停止", "采集已停止"),
                              ("合并完成", "记录合并完成"), ("报告已生成", "报告已生成"),
                              ("本次采集已丢弃", "采集已丢弃"), ("当前采集", "记录已改名")):
            if self._status_text.startswith(prefix): return label
        return "就绪" if self._status_text == "就绪" else "状态已更新"

    @Property(str, notify=timelineChanged)
    def statusCompactText(self):
        if self._collecting:
            return f"采样 {self._sample_count} 次 · {self._sample_last_ms:.0f} ms"
        return "本地采集记录" if self.viewingSession else "详细信息可在日志中查看"

    @Property(str, notify=stateChanged)
    def statusText(self): return self._status_text

    @Property(str, notify=timelineChanged)
    def samplingHealthText(self):
        if not self._collecting:
            return ""
        total = self._sample_count + self._sample_skipped
        completeness = self._sample_count / total * 100.0 if total else 100.0
        working_ms = ((time.monotonic() - self._sample_started_at) * 1000.0
                      if self._sample_working and self._sample_started_at else 0.0)
        latency_ms = max(self._sample_last_ms, working_ms)
        skipped = f" · 跳拍 {self._sample_skipped}" if self._sample_skipped else ""
        window = " · 实时窗口 60 min" if self._live_points_trimmed else ""
        corrected = int(getattr(self._collector, "fps_suppressed_spikes", 0))
        fps_note = f" · FPS异常修正 {corrected}" if corrected else ""
        overdue = " · 当前采样耗时过长" if working_ms >= self._sample_timeout_seconds() * 1000 else ""
        fields = self._sampling_health.fields
        received = sum(item['received'] for item in fields.values())
        expected = sum(item['received'] + item['missing'] for item in fields.values())
        validity = f"{received / expected * 100:.0f}%" if expected else "—"
        missing = [key for key, item in fields.items() if not item['received'] and item['missing']]
        missing_note = (" · 未收到 " + "/".join(missing[:3]) + ("等" if len(missing) > 3 else "")) if missing else ""
        return (f"采样 {self._sample_count} 次 · 最近 {latency_ms:.0f} ms · "
                f"采样执行率 {completeness:.0f}% · 指标有效率 {validity}"
                f"{missing_note}{skipped}{fps_note}{overdue}{window}")

    def _sample_timeout_seconds(self) -> float:
        return max(10.0, self._timer.interval() / 1000.0 * 5.0)

    @Property(bool, notify=stateChanged)
    def canStart(self): return self._selected_device >= 0 and self._selected_target >= 0

    @Property(int, notify=stateChanged)
    def selectedDeviceIndex(self): return self._selected_device

    @Property(int, notify=stateChanged)
    def selectedTargetIndex(self): return self._selected_target

    @Slot()
    def refreshDevices(self):
        if self._collecting:
            self._notify("采集中无法刷新设备", "请先停止当前采集，再刷新设备列表。当前设备和应用不会被改变。")
            return
        if self._busy: return
        previous_id = (self._device_models[self._selected_device].id
                       if 0 <= self._selected_device < len(self._device_models) else "")
        self._device_discovery_token += 1
        self._target_discovery_token += 1  # invalidate an older application-list result
        token = self._device_discovery_token
        self._busy = True; self.stateChanged.emit()

        def work():
            try:
                result = {"token": token, "previousId": previous_id,
                          "devices": discover_all(), "error": ""}
            except Exception as error:
                result = {"token": token, "previousId": previous_id,
                          "devices": [], "error": str(error)}
            self.discoveryReady.emit(result)

        self._executor.submit(work)

    @Slot(object)
    def _accept_devices(self, devices):
        if self._shutting_down:
            return
        if self._collecting:
            self._busy = False; self.stateChanged.emit()
            return
        if isinstance(devices, dict):
            if int(devices.get("token", -1)) != self._device_discovery_token:
                return
            previous_id = str(devices.get("previousId") or "")
            error = str(devices.get("error") or "")
            devices = devices.get("devices") or []
        else:
            previous_id = (self._device_models[self._selected_device].id
                           if 0 <= self._selected_device < len(self._device_models) else "")
            error = ""
        self._reset_window_targets()
        self._device_models = list(devices)
        self._devices = [present_device(device) for device in self._device_models]
        self._selected_device = next((index for index, device in enumerate(self._device_models)
                                      if device.id == previous_id), -1)
        self._selected_target = -1
        self._target_models = []
        self._targets = []
        self._busy = False
        self.devicesChanged.emit(); self.targetsChanged.emit(); self.stateChanged.emit()
        if error:
            self._notify("设备刷新失败", f"获取设备列表时发生错误：{error}")
        if self._selected_device >= 0:
            self._begin_target_discovery(self._device_models[self._selected_device])

    def _begin_target_discovery(self, device):
        self._target_discovery_token += 1
        token = self._target_discovery_token
        self._busy = True; self.stateChanged.emit()

        def work():
            try:
                result = {"token": token, "deviceId": device.id,
                          "targets": discover_targets(device), "error": ""}
            except Exception as error:
                result = {"token": token, "deviceId": device.id,
                          "targets": [], "error": str(error)}
            self.targetsReady.emit(result)

        self._executor.submit(work)

    @Slot(int)
    def selectDevice(self, index):
        if self._collecting or self._busy or not 0 <= index < len(self._device_models): return
        self._reset_window_targets()
        if self._viewing_platform is not None:
            self._clear_session_workspace("就绪")
        self._selected_device = index; self._selected_target = -1
        self._metric_status = {}; self._metric_misses = {}
        self._busy = True; self._targets = []; self.targetsChanged.emit(); self.stateChanged.emit()
        device = self._device_models[index]
        self.settingsChanged.emit(); self.chartLayoutChanged.emit()
        self._begin_target_discovery(device)

    @Slot()
    def refreshTargets(self):
        if self._collecting:
            self._notify("采集中无法刷新应用", "请先停止当前采集，再刷新应用列表。当前设备和应用不会被改变。")
            return
        if self._busy or not 0 <= self._selected_device < len(self._device_models): return
        device = self._device_models[self._selected_device]
        self._begin_target_discovery(device)

    @Slot(object)
    def _accept_targets(self, targets):
        if self._shutting_down:
            return
        if self._collecting:
            self._busy = False; self.stateChanged.emit()
            return
        if isinstance(targets, dict):
            if int(targets.get("token", -1)) != self._target_discovery_token:
                return
            selected_device_id = (self._device_models[self._selected_device].id
                                  if 0 <= self._selected_device < len(self._device_models) else "")
            if str(targets.get("deviceId") or "") != selected_device_id:
                return
            error = str(targets.get("error") or "")
            targets = targets.get("targets") or []
        else:
            error = ""
        self.cancelWindowPick()
        self._performance_window = None
        self._invalidate_screenshot_target()
        self._target_models = list(targets)
        self._metric_status = {}; self._metric_misses = {}
        self.settingsChanged.emit(); self.chartLayoutChanged.emit()
        self._targets = [{"id": t.id, "name": t.name, "pid": t.process_id or 0,
                          "icon": t.icon_path} for t in self._target_models]
        device_id = (self._device_models[self._selected_device].id
                     if 0 <= self._selected_device < len(self._device_models) else "")
        last_target = self._preferences.last_target(device_id)
        self._selected_target = next((index for index, target in enumerate(self._target_models)
                                      if target.id == last_target), 0 if self._target_models else -1)
        self._busy = False; self.targetsChanged.emit(); self.stateChanged.emit()
        if error:
            self._notify("应用列表刷新失败", f"获取可采集应用时发生错误：{error}")

    @Slot(int)
    def selectTarget(self, index):
        if self._collecting or self._busy or self._starting_collection:
            return
        if 0 <= index < len(self._target_models):
            self.cancelWindowPick()
            self._performance_window = None
            self._invalidate_screenshot_target()
            if self._viewing_platform is not None:
                self._clear_session_workspace("就绪")
            self._selected_target = index
            self._metric_status = {}; self._metric_misses = {}
            self.settingsChanged.emit(); self.chartLayoutChanged.emit()
            if 0 <= self._selected_device < len(self._device_models):
                self._preferences.remember_target(self._device_models[self._selected_device].id,
                                                  self._target_models[index].id)
            self.stateChanged.emit()

    @Slot(str, bool)
    def setMetricEnabled(self, key, enabled):
        key = str(key)
        definition = METRIC_BY_KEY.get(key)
        if definition is None or self._current_platform() not in definition.platforms:
            return
        if enabled and self._current_metric_status().get(key, {}).get("state") == "unavailable":
            return
        self._preferences.set_metric_enabled(self._current_platform(), key, bool(enabled))
        self._push_enabled_metrics()
        if self._viewing_platform is None:
            self._preferences.save()
        self.settingsChanged.emit(); self.chartLayoutChanged.emit()

    @Slot(str, bool)
    def setChartVisible(self, key, visible):
        key = str(key)
        if key not in self._preferences.chart_order:
            return
        if visible:
            self._preferences.visible_for(self._current_platform()).add(key)
        else:
            self._preferences.visible_for(self._current_platform()).discard(key)
        self._push_enabled_metrics()
        if self._viewing_platform is None:
            self._preferences.save()
        self.settingsChanged.emit(); self.chartLayoutChanged.emit()

    @Slot(bool)
    def setScreenshotEnabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._preferences.screenshot_enabled:
            return
        if self._collecting and not self._screenshot_capture_settings:
            self._screenshot_capture_settings = self._capture_settings()
        self._preferences.screenshot_enabled = enabled
        self._invalidate_screenshot_target()
        if self._collecting and not self._paused:
            if self._preferences.screenshot_enabled:
                self._screenshot_timer.setInterval(self._preferences.screenshot_interval_ms)
                self._screenshot_timer.start()
            else: self._screenshot_timer.stop()
        self._preferences.save()
        self.settingsChanged.emit()
        self._sync_screenshot_selection()

    @Slot(float)
    def setScreenshotInterval(self, seconds):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if not math.isfinite(seconds):
            return
        interval = self._preferences.set_screenshot_interval_ms(int(seconds * 1000))
        self._screenshot_timer.setInterval(interval)
        if self._collecting and not self._paused and self._preferences.screenshot_enabled:
            self._screenshot_timer.start()
        self._preferences.save()
        self.settingsChanged.emit()

    @Slot(int)
    def setScreenshotMaxWidth(self, pixels):
        try:
            requested = int(pixels)
        except (TypeError, ValueError, OverflowError):
            requested = 1280
        self._preferences.screenshot_max_width = (
            requested if requested in {0, 854, 1280, 1920} else 1280)
        self._preferences.save()
        self.settingsChanged.emit()

    @Slot(str, int)
    def moveChart(self, key, direction):
        key = str(key)
        order = self._preferences.chart_order
        if key not in order or int(direction) == 0:
            return
        visible = [item["key"] for item in self.chartManagerCatalog]
        if self._current_platform() in {Platform.WINDOWS, Platform.IOS, Platform.ANDROID} and key in self._preferences.visible_for(self._current_platform()):
            visible = [item for item in visible if item in self._preferences.visible_for(self._current_platform())]
        if key not in visible:
            return
        old_visible = visible.index(key)
        new_visible = max(0, min(len(visible) - 1, old_visible + (1 if int(direction) > 0 else -1)))
        if old_visible != new_visible:
            other = visible[new_visible]
            old, new = order.index(key), order.index(other)
            order[old], order[new] = order[new], order[old]
            if self._viewing_platform is None:
                self._preferences.save()
            self.chartLayoutChanged.emit()

    @Slot(float)
    def setSampleInterval(self, seconds):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if not math.isfinite(seconds):
            return
        self._timer.setInterval(self._preferences.set_sample_interval_ms(int(seconds * 1000)))
        if self._session_spool is not None and hasattr(self._session_spool,'extras'):
            self._session_spool.extras.monitor.options['interval']=self._preferences.sample_interval_ms/1000
        if isinstance(self._collector, IOSCollector):
            self._collector.set_sample_interval_ms(self._preferences.sample_interval_ms)
            self._executor.submit(self._collector.activate_pending_sources)
        if self._collecting and not self._paused: self._timer.start()
        self._preferences.save()
        self.settingsChanged.emit()

    @Slot()
    def startOrPauseCollection(self):
        if not self._collecting:
            if self._selected_device < 0:
                self._notify("尚未选择设备", "请先在左侧选择设备；如果列表为空，请点击刷新按钮。")
                return
            if self._busy:
                self._notify("应用列表仍在加载", "请等待应用列表加载完成后再开始采集。", "warning")
                return
            if self._selected_target < 0:
                self._notify("尚未选择应用", "请选择要采集的应用。如果列表为空，请先启动目标应用，再刷新设备或重新选择设备。")
                return
            if self._viewing_platform is not None:
                self._clear_session_workspace("就绪")
            self.toggleCollection()
            return
        if self._paused:
            self._paused_total += time.monotonic() - self._pause_started
            self._pause_started = 0.0; self._paused = False
            if self._collector and hasattr(self._collector, 'set_capture_paused'):
                self._collector.set_capture_paused(False)
            # Resume has the same first-frame expectation as a fresh start:
            # request data now instead of leaving every platform visually
            # frozen for one complete configured interval.
            if not self._sample_working:
                self._sample_once()
            self._timer.start()
            if self._preferences.screenshot_enabled:
                self._screenshot_timer.setInterval(self._preferences.screenshot_interval_ms)
                self._screenshot_timer.start()
            self._status_text = "正在采集"
        else:
            self._paused = True; self._pause_started = time.monotonic()
            if self._collector and hasattr(self._collector, 'set_capture_paused'):
                self._collector.set_capture_paused(True)
            self._timer.stop(); self._screenshot_timer.stop()
            self._status_text = "已暂停"
        self.stateChanged.emit(); self.samplesChanged.emit(); self.timelineChanged.emit()

    @Slot()
    def stopCollection(self):
        if self._collecting and self.markingScene:
            self.sceneMarkStopRequested.emit()
            return
        if self._collecting: self.toggleCollection()

    @Slot()
    def discardCollection(self):
        """Stop the active collector and permanently discard its live spool."""
        if not self._collecting:
            return
        self.discardSceneMark()
        self._timer.stop()
        self._screenshot_timer.stop()
        token = self._active_collection_token
        self._active_collection_token = 0
        collector, self._collector = self._collector, None
        sample_future = (self._sample_future
                         if self._sample_inflight_token == token else None)
        self._sample_future = None
        spool, self._session_spool = self._session_spool, None
        self._collecting = False
        self._paused = False
        self._sample_working = False
        self._screenshot_working = False
        try:
            if spool is not None:
                spool.close()
            if self._session_id:
                discard_incomplete_session(WORK_ROOT, self._session_id)
        except (OSError, RuntimeError, ValueError) as error:
            self._notify("丢弃采集数据失败", str(error), "warning")
        self._status_text = "本次采集已丢弃"
        self.stateChanged.emit()

        def stop_work():
            try:
                if sample_future is not None:
                    sample_future.result()
                if collector is not None:
                    collector.stop()
            except Exception:
                pass
        self._executor.submit(stop_work)

    @Slot()
    def toggleCollection(self):
        self.cancelWindowPick()
        if self._collecting:
            # Device/transport failures also stop through this path. Finalize an
            # active mark rather than silently losing it during emergency save.
            if self.markingScene:
                self.finishSceneMark()
            duration = self.duration
            telemetry = self._diagnostics_snapshot()
            capture_settings = self._screenshot_capture_settings or self._capture_settings()
            self._timer.stop()
            self._screenshot_timer.stop()
            token = self._active_collection_token
            self._active_collection_token = 0
            collector, self._collector = self._collector, None
            sample_future = (self._sample_future
                             if self._sample_inflight_token == token else None)
            self._sample_future = None
            self._collecting = False; self._paused = False
            self._pause_started = 0.0
            self._sample_working = False
            self._screenshot_working = False
            self._status_text = "正在停止采集并保存数据…"
            self.stateChanged.emit()
            spool, self._session_spool = self._session_spool, None
            if self._selected_device >= 0 and self._selected_target >= 0:
                root = WORK_ROOT
                device = dict(self._devices[self._selected_device])
                target = dict(self._targets[self._selected_target])
                # No late callback is allowed to mutate these containers after
                # the token was invalidated. Screenshots/scenes are small and
                # copied so a delayed UI action cannot race with serialization.
                samples = {key: rows for key, rows in self._samples.items() if rows}
                screenshots = list(self._screenshots)
                scenes = list(self._scenes)
                enabled = set(self._enabled_metrics())
                session_id = self._session_id
                started_at = self._session_started_at
                session_name = normalize_session_name(
                    f"{target.get('name') or '性能采集'} {datetime.now():%Y-%m-%d %H-%M-%S}")
                self._saving_session = True
                end_reason = self._session_end_reason or "用户停止采集"
                self.stateChanged.emit()
                def save_work():
                    try:
                        saved_end_reason = end_reason
                        # Never stop or clear a collector while sample() is still
                        # using it. This wait is off the Qt thread; collectors'
                        # subprocess calls all have their own finite timeouts.
                        final_sample = None
                        if sample_future is not None:
                            final_payload = sample_future.result()
                            if isinstance(final_payload, dict) and final_payload.get("error"):
                                saved_end_reason = f"采样中断：{final_payload['error']}"
                            if (isinstance(final_payload, dict) and
                                    not final_payload.get("error")):
                                final_sample = final_payload.get("sample")
                        if collector is not None:
                            try:
                                collector.stop()
                            except Exception:
                                # Collector cleanup failure must not sacrifice
                                # data that has already reached the live spool.
                                pass
                        if final_sample is not None and spool is not None:
                            final_points = sample_points(final_sample)
                            spool.append(final_sample.elapsed, final_sample.values,
                                         observed_elapsed=getattr(final_sample, 'observed_elapsed', {}),
                                         frame_events=getattr(final_sample, 'frames', ()),
                                         raw_values=getattr(final_sample, 'raw_values', {}),
                                         adjustments=getattr(final_sample, 'adjustments', {}),
                                         sources=getattr(final_sample, 'sources', {}),
                                         requested=final_payload.get('requested', ()),
                                         collector_ms=final_payload.get('elapsed_ms'),
                                         held_keys=getattr(final_sample, 'held_keys', ()))
                        sample_files = spool.close() if spool is not None else {}
                        if final_sample is not None and not sample_files:
                            for key, point in sample_points(final_sample).items():
                                samples.setdefault(key, []).append(point)
                        try:
                            final_elapsed = float(final_sample.elapsed) if final_sample else duration
                        except (TypeError, ValueError):
                            final_elapsed = duration
                        saved_duration = max(duration, final_elapsed) if math.isfinite(final_elapsed) else duration
                        if final_sample is not None:
                            final_points = sample_points(final_sample)
                            saved_duration = max(saved_duration, max((p['x'] for p in final_points.values()), default=0))
                        if sample_files:
                            path = save_session_from_spool(
                                root, device, target, sample_files, screenshots, enabled,
                                session_id, scenes, started_at, saved_duration, session_name,
                                telemetry=telemetry,
                                capture_settings=capture_settings,
                                end_reason=saved_end_reason)
                        else:
                            path = save_session(root, device, target, samples, screenshots, enabled,
                                                session_id, scenes, started_at, saved_duration, session_name,
                                                telemetry=telemetry,
                                                capture_settings=capture_settings,
                                                end_reason=saved_end_reason)
                        self.sessionSaved.emit({"ok": True, "path": str(path),
                                                "id": session_id, "name": session_name,
                                                "token": token})
                    except Exception as error:
                        self.sessionSaved.emit({"ok": False, "error": str(error),
                                                "id": session_id, "token": token})
                self._executor.submit(save_work)
            else:
                def stop_work():
                    if sample_future is not None:
                        sample_future.result()
                    if collector is not None:
                        collector.stop()
                    if spool is not None:
                        spool.close()
                self._executor.submit(stop_work)
            return
        if self._merging_sessions:
            self._notify("正在合并记录", "请等待合并完成后再开始采集。", "warning")
            return
        if self._saving_session:
            self._notify("正在保存上一次采集",
                         "请等待命名窗口出现后再开始新的采集。", "warning")
            return
        if self._report_working:
            self._notify("正在生成报告", "请等待报告生成完成后再开始采集。", "warning")
            return
        if self._selected_device < 0 or self._selected_target < 0:
            self._notify("无法开始采集", "设备或目标应用无效，请重新选择后重试。")
            return
        device, target = self._device_models[self._selected_device], self._target_models[self._selected_target]
        if device.platform is Platform.WINDOWS and self._performance_window:
            if (target.process_id != self._performance_window.get('pid')
                    or self._verified_window(self._performance_window) is None):
                self._notify("采集窗口已失效", "窗口已关闭或进程身份已变化，请重新选择窗口。", "warning")
                return
        enabled = self._active_metric_keys()
        if not enabled:
            self._notify("尚未选择采集指标",
                         "请点击曲线区域右下角的“性能指标”，至少启用并显示一个性能指标。",
                         "warning")
            return
        if device.platform is Platform.WINDOWS:
            collector = WindowsCollector()
            collector.set_resource_scope(self._preferences.windows_resource_scope)
        elif device.platform is Platform.ANDROID:
            collector = AndroidCollector()
        else:
            collector = IOSCollector()
            collector.set_fps_limit(self._selected_ios_fps_limit())
        self._busy = True
        self._starting_collection = True
        self._status_text = ("正在连接 iOS 设备并启动 Instruments…"
                             if device.platform is Platform.IOS else "正在启动采集…")
        self.stateChanged.emit()

        def start_collector():
            try:
                if (device.platform is Platform.WINDOWS and self._performance_window
                        and self._verified_window(self._performance_window) is None):
                    raise RuntimeError("所选窗口已失效，请重新选择窗口")
                if isinstance(collector, IOSCollector):
                    collector.set_sample_interval_ms(self._preferences.sample_interval_ms)
                collector.start(device, target, enabled)
            except Exception as error:
                try:
                    collector.stop()
                except Exception:
                    pass
                self.collectorStarted.emit({"ok": False, "error": str(error)})
                return
            if self._shutting_down:
                try:
                    collector.stop()
                except Exception:
                    pass
                return
            self.collectorStarted.emit({"ok": True, "collector": collector})

        self._executor.submit(start_collector)

    @Slot(object)
    def _accept_collector_started(self, result):
        if self._shutting_down:
            collector = result.get("collector") if isinstance(result, dict) else None
            if collector is not None:
                try:
                    collector.stop()
                except Exception:
                    pass
            return
        self._busy = False
        self._starting_collection = False
        if not isinstance(result, dict) or not result.get("ok"):
            detail = str(result.get("error") if isinstance(result, dict) else "未知启动错误")
            self._status_text = detail
            self.stateChanged.emit()
            if "尚未运行" in detail:
                detail += "。请先在设备上启动应用并进入要测试的界面，然后重新选择应用。"
            else:
                detail += "。请确认设备连接正常、应用仍在运行，并尝试刷新设备列表。"
            self._notify("采集启动失败", detail)
            return
        collector = result.get("collector")
        if collector is None:
            self._status_text = "采集器启动失败"
            self.stateChanged.emit()
            return
        if (not 0 <= self._selected_device < len(self._devices) or
                not 0 <= self._selected_target < len(self._targets)):
            # A stale discovery result or device removal can invalidate the
            # UI selection while a native collector is starting. Never let a
            # successfully started collector leak because its metadata row
            # disappeared before this queued callback arrived.
            try:
                collector.stop()
            except Exception:
                pass
            self._status_text = "设备或应用已变化，请重新选择后开始采集"
            self.stateChanged.emit()
            return
        self._collection_token += 1
        self._active_collection_token = self._collection_token
        self._session_id = new_session_id()
        self._screenshot_capture_settings = self._capture_settings(collector.enabled_metrics)
        self._screenshot_settings_changes = []
        self._screenshot_settings_truncated = False
        self._session_end_reason = ""
        self._session_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._screenshot_dir = (WORK_ROOT / "data" / "sessions" /
                                self._session_id / "screenshots")
        try:
            device = dict(self._devices[self._selected_device])
            target = dict(self._targets[self._selected_target])
            self._session_spool = LiveSessionWriter(
                WORK_ROOT, self._session_id,sample_interval=self.sampleIntervalSeconds, manifest={
                    "name": "",
                    "started_at": self._session_started_at,
                    "device": device,
                    "target": target,
                    "enabled_metrics": sorted(self._enabled_metrics()),
                    "capture_settings": self._screenshot_capture_settings,
                    "scenes": [],
                })
        except (OSError, ValueError) as error:
            self._session_spool = None
            self._notify("无法启用增量落盘",
                         f"本次采集仍会保存在内存中，但异常退出恢复不可用：{error}", "warning")
        self._samples = {item.key: [] for item in METRICS}
        self.discardSceneMark()
        self._last_metric_diagnostics = {}
        self._reset_chart_view_range()
        self.clearSelection()
        self._scenes = []; self.scenesChanged.emit()
        self._locked_time = -1.0; self._selected_screenshot = -1; self.timeLockChanged.emit()
        self._screenshots = []; self.screenshotsChanged.emit()
        self._screenshot_failure_reported = False
        self._collector = collector; self._started_at = time.monotonic(); self._collecting = True
        self._metric_status = {}; self._metric_misses = {}
        self.settingsChanged.emit(); self.chartLayoutChanged.emit()
        self._sampling_health = SamplingHealth(self.sampleIntervalSeconds)
        self._saved_telemetry = {}
        self._paused = False; self._pause_started = 0.0; self._paused_total = 0.0
        self._sample_working = False; self._sample_started_at = 0.0
        self._sample_inflight_token = 0; self._screenshot_inflight_token = 0
        self._sample_future = None
        self._sample_count = 0; self._sample_skipped = 0
        self._sample_last_ms = 0.0; self._sample_max_ms = 0.0
        self._sample_slow_warning_reported = False
        self._live_points_trimmed = False
        self._status_text = "正在采集"
        self.stateChanged.emit(); self.samplesChanged.emit(); self.timelineChanged.emit()
        # Show the first point without waiting one full configured interval.
        # sample() itself remains on the worker pool, so the first QML frame is
        # not blocked while the device responds.
        self._sample_once()
        self._timer.start()
        if self._preferences.screenshot_enabled:
            self._screenshot_timer.setInterval(self._preferences.screenshot_interval_ms)
            self._screenshot_timer.start()

    @Slot()
    def _sample_once(self):
        if not self._collector:
            return
        if self._sample_working:
            self._sample_skipped += 1
            self._sampling_health.skipped += 1
            busy_for = (time.monotonic() - self._sample_started_at
                        if self._sample_started_at else 0.0)
            if (busy_for >= self._sample_timeout_seconds() and
                    not self._sample_slow_warning_reported):
                self._sample_slow_warning_reported = True
                self._notify(
                    "本次采样耗时过长",
                    "设备命令尚未返回，系统没有并发启动第二次采样，避免 adb 或采集器越积越多。"
                    "请检查设备连接；命令返回后采集会自动继续。",
                    "warning")
            self.timelineChanged.emit()
            return
        collector = self._collector
        token = self._active_collection_token
        if not token:
            return
        requested_metrics = tuple(collector.enabled_metrics)
        elapsed = self.duration
        self._sample_working = True
        self._sample_inflight_token = token
        self._sample_started_at = time.monotonic()
        def work():
            started = time.perf_counter()
            try:
                result, error = collector.sample(elapsed), ""
            except Exception as exc:
                result, error = None, str(exc)
            payload = {"token": token, "sample": result, "error": error,
                       "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                       "requested": requested_metrics}
            if isinstance(collector, (WindowsCollector, IOSCollector, AndroidCollector)):
                try:
                    payload["metric_diagnostics"] = collector.diagnostics()
                except Exception:
                    payload["metric_diagnostics"] = {}
            self.sampleReady.emit(payload)
            return payload
        self._sample_future = self._executor.submit(work)

    @Slot(object)
    def _accept_sample(self, payload):
        if not isinstance(payload, dict):
            return
        token = int(payload.get("token") or 0)
        if token != self._active_collection_token or token != self._sample_inflight_token:
            return
        self._sample_working = False
        self._sample_future = None
        self._sample_inflight_token = 0
        self._sample_started_at = 0.0
        self._sample_slow_warning_reported = False
        sample = payload.get("sample")
        error = str(payload.get("error") or "")
        elapsed_ms = float(payload.get("elapsed_ms") or 0.0)
        if error:
            self._status_text = error
            self._session_end_reason = f"采样中断：{error}"
            self.stateChanged.emit()
            self._notify("采样已中断", f"无法继续读取性能数据：{error}。本次已采集的数据将尝试保存到本地。")
            if self._collecting: self.toggleCollection()
            return
        if not sample or not self._collecting:
            return
        self._sample_count += 1
        self._sample_last_ms = elapsed_ms
        self._sample_max_ms = max(self._sample_max_ms, elapsed_ms)
        points = sample_points(sample)
        self._last_metric_diagnostics = dict(payload.get('metric_diagnostics') or {})
        held_keys = set(getattr(sample, 'held_keys', ()))
        status_points = dict(points)
        for key in held_keys:
            if metric_value_is_plausible(key, sample.values.get(key)):
                status_points[key] = {'x': sample.observed_elapsed.get(key, sample.elapsed), 'y': sample.values[key]}
        if self._current_platform() in {Platform.WINDOWS, Platform.IOS, Platform.ANDROID}:
            update_status = {Platform.IOS: update_ios_status, Platform.WINDOWS: update_windows_status,
                             Platform.ANDROID: update_android_status}[self._current_platform()]
            statuses = update_status(self._metric_status, self._metric_misses,
                                             payload.get("requested", ()), status_points,
                                             payload.get("metric_diagnostics") or {})
            if statuses != self._metric_status:
                self._metric_status = statuses
                self.settingsChanged.emit(); self.chartLayoutChanged.emit()
        self._sampling_health.interval = self.sampleIntervalSeconds
        self._sampling_health.observe(float(sample.elapsed), points, elapsed_ms,
                                       payload.get('requested', ()), held=held_keys)
        self.collectionHealthChanged.emit()
        if self._session_spool is not None:
            try:
                self._session_spool.append(sample.elapsed, sample.values,
                                          observed_elapsed=getattr(sample, 'observed_elapsed', {}),
                                          frame_events=getattr(sample, 'frames', ()),
                                          raw_values=getattr(sample, 'raw_values', {}),
                                          adjustments=getattr(sample, 'adjustments', {}),
                                          sources=getattr(sample, 'sources', {}),
                                          requested=payload.get('requested', ()),
                                          collector_ms=elapsed_ms,
                                          held_keys=held_keys)
            except (OSError, RuntimeError, ValueError) as spool_error:
                self._notify("实时数据写入失败",
                             f"为避免继续采集后丢失更多数据，本次采集将停止：{spool_error}")
                self.toggleCollection()
                return
        batch = {}
        for key, point in points.items():
            self._samples.setdefault(key, []).append(point)
            batch[key] = point
        missing_keys = [str(key) for key in payload.get("requested", ())
                        if str(key) not in batch and str(key) not in held_keys]
        if missing_keys:
            # Let legends clear a temporarily unavailable metric instead of
            # displaying an old value indefinitely. Historical curve points
            # remain untouched, so a transient command failure loses no data.
            batch["__missingKeys"] = missing_keys
        if self._session_spool is not None and self._sample_count % 60 == 0:
            try:
                self._session_spool.checkpoint({
                    "enabled_metrics": sorted(self._enabled_metrics()),
                    "scenes": list(self._scenes),
                    "duration": float(sample.elapsed),
                    "telemetry": self._diagnostics_snapshot(),
                })
            except OSError as spool_error:
                self._notify("恢复信息写入失败",
                             f"本次数据仍在采集，但异常退出恢复信息无法更新：{spool_error}",
                             "warning")
            cutoff = max(0.0, float(sample.elapsed) - LIVE_CHART_WINDOW_SECONDS)
            trimmed = False
            for rows in self._samples.values():
                remove = 0
                while remove < len(rows) and rows[remove]["x"] < cutoff:
                    remove += 1
                remove = max(remove, len(rows) - LIVE_CHART_MAX_POINTS)
                if remove > 0:
                    del rows[:remove]
                    trimmed = True
            if trimmed:
                self._live_points_trimmed = True
                duration = max(float(sample.elapsed), .001)
                new_start = min(.999, cutoff / duration)
                if new_start > self._chart_view_start_ratio:
                    self._chart_view_start_ratio = new_start
                    self._chart_view_end_ratio = 1.0
                    self.chartRangeChanged.emit()
                # Let each QML chart trim only its own arrays in one splice.
                # This keeps the UI window bounded without resending chartData.
                batch["__trimBefore"] = cutoff
                batch["__maxPoints"] = LIVE_CHART_MAX_POINTS
        collector_status = getattr(self._collector, "status", "") if self._collector else ""
        next_status = collector_status or "正在采集"
        if next_status != self._status_text:
            self._status_text = next_status
            self.stateChanged.emit()
        if batch:
            self.sampleBatchAppended.emit(batch)
        self.timelineChanged.emit()
        # If one device read crossed a timer boundary, do not wait for the
        # following whole tick. This avoids turning a 1.1 s collector into a
        # visible 2 s curve cadence when the requested period is 1 s.
        try:
            sample_elapsed = float(sample.elapsed)
        except (TypeError, ValueError):
            sample_elapsed = self.duration
        if (self._collecting and not self._paused and
                self.duration - sample_elapsed >= self._timer.interval() / 1000.0):
            QTimer.singleShot(0, self._sample_once)

    @Slot()
    def _capture_screenshot(self):
        if (self._screenshot_working or not self._collecting or self._paused
                or not self._preferences.screenshot_enabled or self._selected_target < 0):
            return
        target = self._target_models[self._selected_target]
        device = self._device_models[self._selected_device]
        if device.platform is Platform.WINDOWS and not target.process_id: return
        token = self._active_collection_token
        if not token:
            return
        generation = self._screenshot_target_generation
        window = (dict(self._screenshot_window or self._performance_window)
                  if device.platform is Platform.WINDOWS and (self._screenshot_window or self._performance_window)
                  else None)
        process_id = int(window['pid']) if window else target.process_id
        self._screenshot_working = True
        self._screenshot_inflight_token = token
        elapsed = self.duration
        filename = f"{int(elapsed * 1000):09d}.jpg"
        destination = self._screenshot_dir / filename
        temporary = (WORK_ROOT / "data" / "tmp" / "live-screenshots" /
                     str(token) / str(generation) / filename)

        max_width = self._preferences.screenshot_max_width

        def work():
            try:
                if device.platform is Platform.WINDOWS:
                    if window:
                        result = (capture_process_window(process_id, temporary, max_width, window_handle=int(window['hwnd']))
                                  if self._verified_window(window) else None)
                        if result is not None and self._verified_window(window) is None:
                            self._remove_temporary_screenshot(result)
                            result = None
                    else:
                        result = capture_process_window(process_id, temporary, max_width)
                elif device.platform is Platform.ANDROID:
                    result = capture_android(device.id.partition(":")[2], temporary, max_width)
                elif device.platform is Platform.IOS:
                    result = capture_ios(device.id.partition(":")[2], temporary, max_width)
                else:
                    result = None
            except Exception:
                self._remove_temporary_screenshot(temporary)
                result = None
            # A capture that finished after stop/discard is never moved into a
            # session directory and therefore cannot leak into the next run.
            if result is not None and (token != self._active_collection_token
                                       or generation != self._screenshot_target_generation):
                self._remove_temporary_screenshot(result)
                result = None
            self.screenshotReady.emit({"token": token, "elapsed": elapsed,
                                       "path": str(result) if result else "",
                                       "destination": str(destination), "generation": generation,
                                       "window": window})

        self._screenshot_executor.submit(work)

    def _remove_temporary_screenshot(self, value):
        """Delete only this app's screenshot staging files, never arbitrary paths."""
        try:
            path = Path(value).resolve()
            root = (WORK_ROOT / 'data' / 'tmp' / 'live-screenshots').resolve()
            if root not in path.parents:
                return
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except (OSError, ValueError, TypeError):
            pass

    @Slot()
    def shutdown(self):
        """Release native collectors and workers before the Qt runtime exits."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._health_timer.stop()
        self.cancelWindowPick()
        sample_future = (self._sample_future
                         if self._sample_inflight_token == self._active_collection_token else None)
        self._active_collection_token = 0
        self._timer.stop()
        self._screenshot_timer.stop()
        collector, self._collector = self._collector, None
        spool, self._session_spool = self._session_spool, None
        if spool is not None:
            try:
                spool.close()
            except OSError:
                pass
        # sample() and stop() often operate on the same adb subprocess or
        # native PresentMon handle. Serialize them during exit just as normal
        # stop does, otherwise closing at the exact sampling boundary can race.
        if sample_future is not None:
            try:
                sample_future.result(timeout=30)
            except Exception:
                pass
        if collector is not None:
            try:
                collector.stop()
            except Exception:
                pass
        self._html_refresh_timer.stop()
        self._screenshot_executor.shutdown(wait=False, cancel_futures=True)
        self._executor.shutdown(wait=False, cancel_futures=True)

    @Slot(object)
    def _accept_screenshot(self, payload):
        if not isinstance(payload, dict):
            return
        token = int(payload.get("token") or 0)
        path = Path(str(payload.get("path") or "")) if payload.get("path") else None
        if token != self._active_collection_token or token != self._screenshot_inflight_token:
            if path is not None:
                self._remove_temporary_screenshot(path)
            return
        generation = int(payload.get('generation', self._screenshot_target_generation))
        window = payload.get('window')
        invalid_window = bool(window and self._verified_window(window) is None)
        if generation != self._screenshot_target_generation or invalid_window:
            if path is not None:
                self._remove_temporary_screenshot(path)
            self._screenshot_working = False
            self._screenshot_inflight_token = 0
            if (generation == self._screenshot_target_generation and invalid_window
                    and not self._screenshot_failure_reported):
                self._screenshot_failure_reported = True
                self._notify("截图窗口已失效", "所选窗口已关闭或进程身份已变化；性能指标继续采集，请重新选择截图窗口或恢复跟随。", "warning")
            if self._collecting and not self._paused and self._preferences.screenshot_enabled:
                self._screenshot_timer.start(self._preferences.screenshot_interval_ms)
            return
        elapsed = float(payload.get("elapsed") or 0.0)
        self._screenshot_working = False
        self._screenshot_inflight_token = 0
        if path:
            destination = Path(str(payload.get("destination") or ""))
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                path.replace(destination)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            except OSError:
                destination = None
            if destination is None:
                path = None
        if path:
            minutes, seconds = divmod(int(elapsed), 60)
            self._screenshots.append({"image": destination.as_uri(), "time": f"{minutes:02d}:{seconds:02d}",
                                      "seconds": elapsed,
                                      "window_target": dict(window) if isinstance(window, dict) else None})
            self._sync_screenshot_selection()
            self.screenshotsChanged.emit()
        elif not self._screenshot_failure_reported:
            self._screenshot_failure_reported = True
            self._notify("截图采集失败",
                         "性能指标仍会继续采集，但暂时无法获取截图。请确认应用窗口可见、设备未锁屏且设备连接正常。",
                         "warning")
            self.timeLockChanged.emit()
        if (self._collecting and not self._paused and
                self._preferences.screenshot_enabled):
            configured_ms = self._preferences.screenshot_interval_ms
            if path:
                spent_ms = max(0, round((self.duration - elapsed) * 1000.0))
                delay_ms = max(1, configured_ms - spent_ms)
            else:
                # A failed source gets a complete retry interval rather than
                # entering a tight immediate-failure loop.
                delay_ms = configured_ms
            self._screenshot_timer.setInterval(delay_ms)
            self._screenshot_timer.start()
