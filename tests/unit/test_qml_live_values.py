"""End-to-end guard for live chart curves and their latest-value legends."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

import shiboken6
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow

from perfzy.application.controller import AppController
from perfzy.application import controller as controller_module
from perfzy.application.preview import _synthetic_curves


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(controller_module, "discover_all", lambda: [])
    monkeypatch.setattr(controller_module, "discover_targets", lambda device: [])


def _visual_items(item):
    yield item
    for child in item.childItems():
        yield from _visual_items(child)


def test_first_live_batch_paints_without_switching_pages():
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "qml"))
    controller = AppController()
    context = engine.rootContext()
    context.setContextProperty("appController", controller)
    for name in ("settingsPreview", "metricPickerPreview", "historyPreview", "exitPreview"):
        context.setContextProperty(name, False)
    context.setContextProperty("smokeTest", True)
    engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
    assert engine.rootObjects()
    app.processEvents()

    window = engine.rootObjects()[0]
    quick_window = shiboken6.wrapInstance(shiboken6.getCppPointer(window)[0], QQuickWindow)
    named_objects = {item.objectName(): item for item in _visual_items(quick_window.contentItem())
                     if item.objectName()}
    assert int(named_objects["plotCanvas:fps"].property("paintedPointCount")) == 0

    controller.sampleBatchAppended.emit({"fps": {"x": 1.0, "y": 30.0}})
    app.processEvents()

    assert named_objects["legendValue:fps"].property("text") == "30.00"
    fps_chart = named_objects["chart:fps"]
    assert bool(fps_chart.property("renderActive"))
    assert float(fps_chart.property("axisMaximum")) >= 30.0
    assert int(named_objects["plotCanvas:fps"].property("paintedPointCount")) > 0

    window.close()
    engine.deleteLater()
    controller.shutdown()
    app.processEvents()


def test_live_batch_updates_every_latest_value_without_mouse_movement():
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "qml"))
    controller = AppController()
    _synthetic_curves(controller)
    # Explicitly show the tested chart; do not depend on saved user settings.
    controller._preferences.windows_visible_charts.update({"fps", "frame", "cpu", "system_cpu", "memory"})
    context = engine.rootContext()
    context.setContextProperty("appController", controller)
    for name in ("settingsPreview", "metricPickerPreview", "historyPreview", "exitPreview"):
        context.setContextProperty(name, False)
    context.setContextProperty("smokeTest", True)
    engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
    assert engine.rootObjects()
    app.processEvents()

    expected = {"fps": 31.234, "frame_time": 32.3, "app_cpu": 33.4, "total_cpu": 34.5}
    controller.sampleBatchAppended.emit({
        key: {"x": 131.0, "y": value} for key, value in expected.items()
    })
    app.processEvents()

    window = engine.rootObjects()[0]
    quick_window = shiboken6.wrapInstance(shiboken6.getCppPointer(window)[0], QQuickWindow)
    named_objects = {item.objectName(): item for item in _visual_items(quick_window.contentItem())
                     if item.objectName()}
    for key, value in expected.items():
        label = named_objects.get(f"legendValue:{key}")
        assert label is not None, (key, sorted(named_objects)[:40])
        assert label.property("text") == (f"{value:.2f}" if key == "fps" else f"{value:.1f}")

    # A second update, including a legitimate zero, guards against a binding
    # that refreshes once and then becomes detached from its array.
    second = {"fps": 28.7, "frame_time": 16.7, "app_cpu": 0.0, "total_cpu": 42.1}
    controller.sampleBatchAppended.emit({
        key: {"x": 132.0, "y": value} for key, value in second.items()
    })
    app.processEvents()
    for key, value in second.items():
        assert named_objects[f"legendValue:{key}"].property("text") == (f"{value:.2f}" if key == "fps" else f"{value:.1f}")
    controller.sampleBatchAppended.emit({"__missingKeys": ["frame_time"]})
    app.processEvents()
    assert named_objects["legendValue:frame_time"].property("text") == ""
    assert named_objects["legendValue:fps"].property("text") == "28.70"
    for key in ("fps", "frame", "cpu"):
        assert int(named_objects[f"plotCanvas:{key}"].property("paintedPointCount")) > 0

    controller.setSelectionRatios(.2, .4)
    controller.setTimeLockRatio(.3)
    app.processEvents()
    charts = ("fps", "frame", "cpu", "memory")
    lock_positions = [float(named_objects[f"lockMarker:{key}"].property("x")) for key in charts]
    selection_positions = [
        (float(named_objects[f"selectionOverlay:{key}"].property("x")),
         float(named_objects[f"selectionOverlay:{key}"].property("width")))
        for key in charts
    ]
    assert max(lock_positions) - min(lock_positions) <= 1.0
    assert max(value[0] for value in selection_positions) - min(value[0] for value in selection_positions) <= 1.0
    assert max(value[1] for value in selection_positions) - min(value[1] for value in selection_positions) <= 1.0

    before_lock = lock_positions[0]
    controller.setChartViewRange(.1, .7)
    app.processEvents()
    moved_locks = [float(named_objects[f"lockMarker:{key}"].property("x")) for key in charts]
    assert max(moved_locks) - min(moved_locks) <= 1.0
    assert abs(moved_locks[0] - before_lock) > 1.0

    # A controller trim checkpoint must bound QML's private live arrays too;
    # otherwise Python remains bounded while the GUI grows for the whole run.
    controller.sampleBatchAppended.emit({
        "fps": {"x": 133.0, "y": 30.0},
        "__trimBefore": 132.5,
        "__maxPoints": 1,
    })
    app.processEvents()
    fps_data = named_objects["chart:fps"].property("renderedData").toVariant()
    assert fps_data["fps"] == [{"x": 133.0, "y": 30.0}]

    # A failed save after “save and exit” must keep the application open and
    # reset the one-shot intent, so a later unrelated save cannot close it.
    window.setProperty("exitAfterSave", True)
    controller.sessionSaved.emit({"ok": False, "error": "disk full"})
    app.processEvents()
    assert not bool(window.property("exitAfterSave"))
    assert not bool(window.property("allowClose"))
    assert window.isVisible()

    window.close()
    engine.deleteLater()
    controller.shutdown()
    app.processEvents()
