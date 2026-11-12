from __future__ import annotations

import os
import sys
import subprocess
import ctypes
import traceback
from pathlib import Path

from perfzy.runtime_paths import RESOURCE_ROOT, WORK_ROOT


ROOT = RESOURCE_ROOT
vendor = ROOT / "vendor"
if vendor.exists():
    sys.path.insert(0, str(vendor))


def main() -> int:
    if sys.argv[1:] == ["--capture-window-worker"]:
        # Frozen builds reuse their own executable as a bounded screenshot
        # helper. Dispatch before elevation or any Qt initialization.
        from perfzy.capture.windows import _capture_worker_main
        return _capture_worker_main()
    bundled_tools = ROOT / "tools"
    if bundled_tools.is_dir():
        os.environ["PATH"] = str(bundled_tools) + os.pathsep + os.environ.get("PATH", "")
    if "--ios-bridge" in sys.argv:
        from tools.ios_bridge import main as ios_bridge_main
        arguments = [item for item in sys.argv[1:] if item != "--ios-bridge"]
        return ios_bridge_main(arguments)
    smoke_test = "--smoke-test" in sys.argv
    preview_path = next((item.partition("=")[2] for item in sys.argv
                         if item.startswith("--render-preview=")), "")
    if (sys.platform == "win32" and not smoke_test and "--elevated" not in sys.argv
            and os.environ.get("PERFZY_NO_ELEVATE") != "1"):
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                parameters = subprocess.list2cmdline(
                    (["--elevated"] if getattr(sys, "frozen", False)
                     else [str(Path(__file__).resolve()), "--elevated"]))
                result = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, parameters, str(ROOT), 1)
                return 0 if result > 32 else 3
        except OSError:
            pass
    if smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QSG_RENDER_LOOP", "basic")

    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from perfzy.application.appearance import Appearance
    from perfzy.application.window_frame import apply_window_corners
    from perfzy.application.controller import AppController
    from perfzy.application.session_viewer import SessionViewerController
    from perfzy.application.record_upload import RecordUploadBridge
    from perfzy.application.preview import PREVIEW_FLAGS, apply_previews

    if sys.platform == "win32":
        try:
            set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
            set_app_id.argtypes = [ctypes.c_wchar_p]
            set_app_id.restype = ctypes.c_long
            set_app_id("PerfZY.Desktop")
        except (AttributeError, OSError):
            pass

    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("PerfZY")
    for font_path in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
                      Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyhbd.ttc"):
        if font_path.exists(): QFontDatabase.addApplicationFont(str(font_path))
    application_font = QFont("Microsoft YaHei UI", 9)
    application_font.setHintingPreference(QFont.PreferFullHinting)
    application_font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(application_font)
    icon = ROOT / "assets" / "perfzy-icon-rounded-v2.ico"
    if not icon.exists():
        icon = ROOT / "assets" / "perfzy-icon-rounded.ico"
    if not icon.exists():
        icon = ROOT / "assets" / "perfzy-icon-rounded.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "qml"))
    controller = AppController()
    record_viewer = SessionViewerController(controller)
    appearance = Appearance(WORK_ROOT / "data" / "preferences.ini")
    if "--light-preview" in sys.argv:
        appearance.set_preview_mode("light")
    elif "--dark-preview" in sys.argv:
        appearance.set_preview_mode("dark")
    record_upload = RecordUploadBridge(controller, WORK_ROOT)
    app.aboutToQuit.connect(controller.shutdown)
    app.aboutToQuit.connect(record_viewer.shutdown)
    app.aboutToQuit.connect(record_upload.shutdown)
    requested_previews = {flag for flag in PREVIEW_FLAGS if flag in sys.argv}
    if requested_previews:
        apply_previews(controller, requested_previews, ROOT)
    engine.rootContext().setContextProperty("appController", controller)
    engine.rootContext().setContextProperty("recordViewerController", record_viewer)
    engine.rootContext().setContextProperty("uiAppearance", appearance)
    engine.rootContext().setContextProperty("logsPreview", "--logs-preview" in sys.argv)
    engine.rootContext().setContextProperty("recordUploadBridge", record_upload)
    engine.rootContext().setContextProperty("settingsPreview", "--settings-preview" in sys.argv)
    engine.rootContext().setContextProperty("metricPickerPreview", "--metric-picker-preview" in sys.argv)
    engine.rootContext().setContextProperty("historyPreview", "--history-preview" in sys.argv)
    engine.rootContext().setContextProperty("exitPreview", "--exit-preview" in sys.argv)
    engine.rootContext().setContextProperty("smokeTest", smoke_test)
    engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
    if not engine.rootObjects():
        return 2
    # Keep the resize/state observer alive for the native window lifetime.
    _window_frame = apply_window_corners(engine.rootObjects()[0])
    if smoke_test:
        if "--error-preview" in sys.argv:
            QTimer.singleShot(100, lambda: controller._notify(
                "采集中无法查看记录", "请先点击“停止”，等待当前数据保存完成后再打开历史记录。"))
        def finish() -> None:
            if preview_path:
                engine.rootObjects()[0].grabWindow().save(preview_path)
            app.quit()
        QTimer.singleShot(1000, finish)
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as error:
        # pythonw has no console. Preserve the traceback and show a visible
        # message instead of making a double-click launch fail silently.
        log_path = WORK_ROOT / "data" / "startup-error.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        if (sys.platform == "win32" and "--ios-bridge" not in sys.argv
                and "--capture-window-worker" not in sys.argv and "--smoke-test" not in sys.argv):
            try:
                ctypes.windll.user32.MessageBoxW(
                    None, f"PerfZY 启动失败：{error}\n\n详细日志：{log_path}",
                    "PerfZY", 0x10)
            except OSError:
                pass
        raise
