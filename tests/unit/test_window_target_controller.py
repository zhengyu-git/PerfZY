"""Window selection/capture wiring tests; every native-window operation is mocked."""
import copy
from concurrent.futures import Future
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QObject, QMetaObject, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
import pytest

from perfzy.application import controller as module
from perfzy.domain.models import Device, Platform, Target


_QT_APP = None


class DeferredExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, function, *args, **kwargs):
        future = Future()
        self.pending.append((function, args, kwargs, future))
        return future

    def run(self):
        function, args, kwargs, future = self.pending.pop(0)
        future.set_result(function(*args, **kwargs))

    def shutdown(self, **kwargs):
        self.pending.clear()


def window(hwnd=111, pid=4242, created=10.5, title="Test Window"):
    return {"hwnd": hwnd, "pid": pid, "title": title, "process_name": "game.exe",
            "create_time": created,
            "rect": {"left": 0, "top": 0, "right": 640, "bottom": 480, "width": 640, "height": 480}}


@pytest.fixture
def controller(tmp_path, monkeypatch):
    global _QT_APP
    _QT_APP = QGuiApplication.instance() or QGuiApplication([])
    monkeypatch.setattr(module, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(module, "discover_all", lambda: [])
    monkeypatch.setattr(module, "discover_targets", lambda device: [])
    monkeypatch.setattr(module.windows_capture, "list_visible_windows", lambda **kwargs: [])
    monkeypatch.setattr(module.windows_capture, "window_at_cursor", lambda **kwargs: None)
    monkeypatch.setattr(module.windows_capture, "describe_window", lambda hwnd, expected_pid=None: None)
    instance = module.AppController()
    instance._executor.shutdown(wait=True)
    instance._screenshot_executor.shutdown(wait=True)
    _QT_APP.processEvents()
    instance._executor = DeferredExecutor()
    instance._screenshot_executor = DeferredExecutor()
    instance._device_models = [Device("windows:local", Platform.WINDOWS, "Mock PC")]
    instance._devices = [{"id": "windows:local", "name": "Mock PC"}]
    instance._selected_device = 0
    instance._target_models = [Target("windows:4242", "windows:local", "game", 4242)]
    instance._targets = [{"id": "windows:4242", "name": "game", "pid": 4242}]
    instance._selected_target = 0
    instance._busy = False
    yield instance
    instance.shutdown()


def mock_windows(monkeypatch, *items):
    by_hwnd = {item["hwnd"]: item for item in items}
    monkeypatch.setattr(module.windows_capture, "describe_window", lambda hwnd, expected_pid=None: copy.deepcopy(by_hwnd.get(hwnd)))


def collecting(instance):
    instance._collecting = True
    instance._active_collection_token = 42
    instance._started_at = time.perf_counter() - 2
    instance._preferences.screenshot_enabled = True


def test_visible_list_excludes_own_process_even_if_backend_includes_it(controller, monkeypatch):
    monkeypatch.setattr(module.windows_capture, "list_visible_windows", lambda **kwargs: [window(), window(222, os.getpid())])
    controller.refreshWindowCandidates()
    assert [item["hwnd"] for item in controller.windowCandidates] == [111]


def test_performance_window_binds_exact_pid_not_matching_executable_name(controller, monkeypatch):
    selected = window(222, 5555)
    mock_windows(monkeypatch, selected)
    controller._window_candidates = [selected]
    assert controller.selectWindowCandidate(0, "performance")
    assert controller._target_models[controller.selectedTargetIndex].process_id == 5555
    assert len(controller._target_models) == 2
    assert controller._performance_window["hwnd"] == 222
    assert "5555" in controller.performanceWindowLabel
    assert controller.selectWindowCandidate(0, "performance")
    assert len(controller._target_models) == 2


@pytest.mark.parametrize("field", ["_collecting", "_busy", "_starting_collection", "_saving_session", "_report_working"])
def test_performance_target_is_locked_while_active_or_busy(controller, monkeypatch, field):
    mock_windows(monkeypatch, window())
    setattr(controller, field, True)
    assert controller._bind_window(window(), "performance") is False
    assert controller._performance_window is None
    assert controller._target_models[0].process_id == 4242


def test_independent_screenshot_target_can_change_during_capture_and_restore_follow(controller, monkeypatch):
    first, second = window(), window(222, 5555)
    mock_windows(monkeypatch, first, second)
    assert controller._bind_window(first, "performance")
    collecting(controller)
    generation = controller._screenshot_target_generation
    assert controller._bind_window(second, "screenshot")
    assert controller._target_models[controller.selectedTargetIndex].process_id == 4242
    assert controller._screenshot_window["pid"] == 5555
    assert controller._performance_window["pid"] == 4242
    assert controller._screenshot_target_generation > generation
    assert "5555" in controller.screenshotWindowLabel
    controller.followPerformanceScreenshot()
    assert controller._screenshot_window is None
    assert controller.screenshotWindowLabel == "跟随性能采集目标"


def test_cancelled_countdown_never_reads_cursor_or_changes_target(controller, monkeypatch):
    calls = []
    monkeypatch.setattr(module.windows_capture, "window_at_cursor", lambda **kwargs: calls.append(kwargs))
    controller.startWindowPick("performance")
    assert controller.windowPickSeconds == 3
    controller._window_pick_tick()
    controller.cancelWindowPick()
    for _ in range(4):
        controller._window_pick_tick()
    assert calls == []
    assert controller.windowPickSeconds == 0
    assert not controller._window_pick_timer.isActive()
    assert controller._performance_window is None


def test_countdown_reads_cursor_once_and_passes_own_process_exclusion(controller, monkeypatch):
    selected = window()
    calls = []
    mock_windows(monkeypatch, selected)
    def cursor(**kwargs):
        calls.append(kwargs)
        return selected
    monkeypatch.setattr(module.windows_capture, "window_at_cursor", cursor)
    controller.startWindowPick("performance")
    for _ in range(5):
        controller._window_pick_tick()
    assert calls == [{"exclude_process_id": os.getpid()}]
    assert controller._performance_window["hwnd"] == 111
    assert controller.windowPickSeconds == 0
    assert not controller._window_pick_timer.isActive()


@pytest.mark.parametrize("item", [window(pid=os.getpid()), window(created=None)])
def test_own_process_or_unverifiable_identity_cannot_be_selected(controller, monkeypatch, item):
    mock_windows(monkeypatch, item)
    assert controller._bind_window(item, "performance") is False
    assert controller._performance_window is None


def test_reused_pid_is_rejected_before_binding(controller, monkeypatch):
    old, replacement = window(), window(created=99)
    mock_windows(monkeypatch, replacement)
    assert controller._bind_window(old, "screenshot") is False
    assert controller._screenshot_window is None


def test_closed_window_countdown_finishes_without_selection(controller, monkeypatch):
    monkeypatch.setattr(module.windows_capture, "window_at_cursor", lambda **kwargs: None)
    controller.startWindowPick("screenshot")
    for _ in range(3):
        controller._window_pick_tick()
    assert controller._screenshot_window is None
    assert "未拾取" in controller.windowPickStatus
    assert not controller._window_pick_timer.isActive()


def test_independent_window_metadata_is_session_diagnostics_not_preferences(controller, monkeypatch):
    first, second = window(), window(222, 5555)
    mock_windows(monkeypatch, first, second)
    before = (module.WORK_ROOT / "data" / "preferences.ini").read_bytes()
    controller._bind_window(first, "performance")
    controller._bind_window(second, "screenshot")
    metadata = controller._diagnostics_snapshot()["window_targets"]
    assert metadata["screenshot_mode"] == "independent"
    assert metadata["performance"]["hwnd"] == 111
    assert metadata["screenshot"]["hwnd"] == 222
    assert (module.WORK_ROOT / "data" / "preferences.ini").read_bytes() == before


def test_capture_uses_independent_hwnd_and_keeps_selected_performance_pid(controller, monkeypatch):
    selected = window(222, 5555)
    mock_windows(monkeypatch, selected)
    controller._bind_window(selected, "screenshot")
    collecting(controller)
    calls = []
    def capture(pid, destination, max_width, *, window_handle=None):
        calls.append((pid, window_handle))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mock-jpeg")
        return destination
    monkeypatch.setattr(module, "capture_process_window", capture)
    controller._capture_screenshot()
    controller._screenshot_executor.run()
    assert calls == [(5555, 222)]
    assert controller._target_models[0].process_id == 4242
    assert len(controller.screenshots) == 1
    assert controller.screenshots[0]["window_target"]["pid"] == 5555


def test_target_changed_during_capture_discards_old_result_and_retries(controller, monkeypatch):
    first, second = window(), window(222, 5555)
    mock_windows(monkeypatch, first, second)
    controller._bind_window(first, "screenshot")
    collecting(controller)
    def capture(pid, destination, max_width, *, window_handle=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mock-jpeg")
        controller._bind_window(second, "screenshot")
        return destination
    monkeypatch.setattr(module, "capture_process_window", capture)
    controller._capture_screenshot()
    controller._screenshot_executor.run()
    assert controller.screenshots == []
    assert not list((module.WORK_ROOT / "data" / "tmp" / "live-screenshots").rglob("*.jpg"))
    assert not controller._screenshot_working
    assert controller._screenshot_timer.isActive()


def test_pid_reuse_after_capture_discards_staging_file(controller, monkeypatch):
    selected = window()
    mock_windows(monkeypatch, selected)
    controller._bind_window(selected, "screenshot")
    collecting(controller)
    def capture(pid, destination, max_width, *, window_handle=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mock-jpeg")
        mock_windows(monkeypatch, window(created=99))
        return destination
    monkeypatch.setattr(module, "capture_process_window", capture)
    controller._capture_screenshot()
    controller._screenshot_executor.run()
    assert controller.screenshots == []
    assert not list((module.WORK_ROOT / "data" / "tmp" / "live-screenshots").rglob("*.jpg"))


def test_closed_selected_window_fails_before_capture_without_redirecting(controller, monkeypatch):
    mock_windows(monkeypatch, window())
    controller._bind_window(window(), "screenshot")
    collecting(controller)
    mock_windows(monkeypatch)
    calls, notices = [], []
    monkeypatch.setattr(module, "capture_process_window", lambda *args, **kwargs: calls.append(args))
    controller.notificationRequested.connect(lambda *args: notices.append(args))
    controller._capture_screenshot()
    controller._screenshot_executor.run()
    assert calls == []
    assert controller.screenshots == []
    assert any("窗口已失效" in notice[0] for notice in notices)
    assert controller._screenshot_timer.isActive()


def test_target_change_after_worker_finish_discards_queued_old_callback(controller, monkeypatch):
    mock_windows(monkeypatch, window())
    controller._bind_window(window(), "screenshot")
    collecting(controller)
    old_generation = controller._screenshot_target_generation
    staged = module.WORK_ROOT / "data" / "tmp" / "live-screenshots" / "42" / str(old_generation) / "shot.jpg"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"mock-jpeg")
    controller._screenshot_inflight_token = 42
    controller._screenshot_working = True
    controller.followPerformanceScreenshot()
    controller._accept_screenshot({"token": 42, "generation": old_generation,
        "window": window(), "path": str(staged), "destination": str(module.WORK_ROOT / "session" / "shot.jpg")})
    assert controller.screenshots == []
    assert not staged.exists()
    assert not controller._screenshot_working


def test_performance_start_rejects_stale_bound_process_before_constructing_collector(controller, monkeypatch):
    controller._performance_window = window()
    calls = []
    monkeypatch.setattr(module, "WindowsCollector", lambda: calls.append("constructed"))
    controller.toggleCollection()
    assert calls == []
    assert not controller._starting_collection
    assert not controller._collecting


def test_normal_app_selection_clears_explicit_performance_window_but_preserves_independent_screenshot(controller, monkeypatch):
    first, second = window(), window(222, 5555)
    mock_windows(monkeypatch, first, second)
    controller._bind_window(first, "performance")
    controller._bind_window(second, "screenshot")
    generation = controller._screenshot_target_generation
    controller.selectTarget(0)
    assert controller._performance_window is None
    assert controller._screenshot_window["hwnd"] == 222
    assert controller._screenshot_target_generation > generation


def test_stale_result_cleanup_never_deletes_external_file(controller, tmp_path):
    external = tmp_path / "user-file.jpg"
    external.write_bytes(b"keep")
    controller._accept_screenshot({"token": 99, "path": str(external)})
    assert external.read_bytes() == b"keep"


def test_default_capture_without_window_binding_keeps_original_call_contract(controller, monkeypatch):
    collecting(controller)
    calls = []
    monkeypatch.setattr(module, "capture_process_window", lambda pid, destination, max_width: calls.append(pid))
    controller._capture_screenshot()
    controller._screenshot_executor.run()
    assert calls == [4242]


@pytest.mark.parametrize("platform,prefix,method", [(Platform.ANDROID, "android", "capture_android"), (Platform.IOS, "ios", "capture_ios")])
def test_mobile_default_capture_contract_unchanged(controller, monkeypatch, platform, prefix, method):
    device_id = prefix + ":SERIAL"
    controller._device_models = [Device(device_id, platform, "Mock Phone")]
    controller._target_models = [Target("com.example.mock", device_id, "Mock App")]
    collecting(controller)
    calls = []
    monkeypatch.setattr(module, method, lambda serial, destination, width: calls.append(serial))
    controller._capture_screenshot()
    controller._screenshot_executor.run()
    assert calls == ["SERIAL"]
    assert not controller.windowTargetsAvailable


def test_selecting_android_resets_window_bindings(controller, monkeypatch):
    mock_windows(monkeypatch, window())
    controller._bind_window(window(), "screenshot")
    controller._device_models.append(Device("android:SERIAL", Platform.ANDROID, "Mock Phone"))
    controller.selectDevice(1)
    assert controller._screenshot_window is controller._performance_window is None
    assert not controller.windowTargetsAvailable


def test_window_panel_loads_and_close_cancels_pending_pick(controller, monkeypatch, tmp_path):
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    _QT_APP.setFont(QFont("Microsoft YaHei UI", 9))
    monkeypatch.setattr(module.windows_capture, "list_visible_windows", lambda **kwargs: [
        window(title="模拟性能窗口 · Test Game"), window(222, 5555, title="模拟独立截图窗口")])
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("appController", controller)
    for name in ("settingsPreview", "metricPickerPreview", "historyPreview", "exitPreview"):
        context.setContextProperty(name, False)
    context.setContextProperty("smokeTest", True)
    root = Path(module.__file__).resolve().parents[2]
    engine.addImportPath(str(root / "qml"))
    engine.load(QUrl.fromLocalFile(str(root / "qml" / "Main.qml")))
    assert engine.rootObjects()
    main = engine.rootObjects()[0]
    panel = main.findChild(QObject, "windowTargetPanel")
    assert panel is not None
    QMetaObject.invokeMethod(panel, "open")
    _QT_APP.processEvents()
    panel_image = main.grabWindow()
    assert not panel_image.isNull()
    assert panel_image.save(str(tmp_path / "window-panel-preview.png"))
    controller.startWindowPick("screenshot")
    assert controller.windowPickSeconds == 3
    QMetaObject.invokeMethod(panel, "close")
    _QT_APP.processEvents()
    assert controller.windowPickSeconds == 0
    main.close()
