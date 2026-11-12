"""Real mouse gestures across the capture page and its nested chart readouts."""
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')

import pytest
import shiboken6
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from perfzy.application import controller as controller_module
from perfzy.application.controller import AppController
from perfzy.application.session_viewer import SessionViewerController
from perfzy.application.preview import _synthetic_curves

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def capture_page(tmp_path, monkeypatch):
    monkeypatch.setattr(controller_module, 'WORK_ROOT', tmp_path)
    monkeypatch.setattr(controller_module, 'discover_all', lambda: [])
    monkeypatch.setattr(controller_module, 'discover_targets', lambda device: [])
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = AppController()
    viewer = SessionViewerController(controller)
    _synthetic_curves(controller)
    controller._preferences.windows_visible_charts.update({'fps', 'frame', 'cpu', 'memory'})
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty('appController', controller)
    context.setContextProperty('recordViewerController', viewer)
    for name in ('settingsPreview', 'historyPreview', 'metricPickerPreview', 'exitPreview',
                 'logsPreview'):
        context.setContextProperty(name, False)
    context.setContextProperty('smokeTest', True)
    engine.load(QUrl.fromLocalFile(str(ROOT / 'qml/Main.qml')))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    window = shiboken6.wrapInstance(shiboken6.getCppPointer(root)[0], QQuickWindow)
    QTest.qWait(60)

    def find(name):
        pending = [window.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        raise AssertionError(name)

    yield window, controller, find
    root.close()
    viewer.shutdown()
    controller.shutdown()
    engine.deleteLater()
    app.processEvents()

def _drag(window, start, end):
    QTest.mouseMove(window, start.toPoint())
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start.toPoint())
    for step in range(1, 13):
        QTest.mouseMove(window, (start + (end - start) * (step / 12)).toPoint(), 15)
        QGuiApplication.processEvents()
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end.toPoint())
    QTest.qWait(35)

def test_selection_survives_slow_drags_readouts_and_zoom(capture_page):
    window, controller, find = capture_page
    plot = find('plotCanvas:fps')
    page_scroll = find('chartScroll').property('contentItem')
    start = plot.mapToScene(QPointF(plot.width() * .1, plot.height() * .2))
    end = plot.mapToScene(QPointF(plot.width() * .6, plot.height() * .75))
    _drag(window, start, end)
    assert controller.hasSelection
    assert controller.selectionStartRatio == pytest.approx(.1, abs=.003)
    assert controller.selectionEndRatio == pytest.approx(.6, abs=.003)
    assert abs(page_scroll.property('contentY')) < 1

    old_selection = controller._selection
    panel = find('selectionStatsPanel:fps')
    start = panel.mapToScene(QPointF(30, 20))
    _drag(window, start, start - QPointF(130, -45))
    assert controller.hasSelection and controller._selection != old_selection

    controller.clearSelection()
    controller.setTimeLockRatio(.35)
    QTest.qWait(35)
    panel = find('lockedInfoPanel:fps')
    start = panel.mapToScene(QPointF(30, 20))
    _drag(window, start, start + QPointF(140, 45))
    assert controller.hasSelection

    controller.clearSelection()
    controller.clearTimeLock()
    controller.setChartViewRange(.20, .205)
    QTest.qWait(40)
    _drag(window, plot.mapToScene(QPointF(plot.width() * .2, plot.height() * .5)),
          plot.mapToScene(QPointF(plot.width() * .55, plot.height() * .5)))
    assert controller.hasSelection
    assert .20 < controller.selectionStartRatio < controller.selectionEndRatio < .205
    assert controller.selectionEndRatio - controller.selectionStartRatio < .002
    assert find('selectionOverlay:fps').property('width') > plot.width() * .3

def _wheel(window, point, delta=-120):
    QTest.mouseMove(window, point.toPoint())
    event = QWheelEvent(point, QPointF(window.mapToGlobal(point.toPoint())), QPoint(),
        QPoint(0, delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QGuiApplication.sendEvent(window, event)
    QTest.qWait(40)

def test_legend_wheel_scrolls_rows_and_continues_to_page(capture_page):
    window, controller, find = capture_page
    page = find('chartScroll').property('contentItem')
    row = find('legendRow:fps')
    _wheel(window, row.mapToScene(QPointF(35, 7)))
    assert page.property('contentY') > 1

    cpu = find('chart:cpu')
    for index in range(8):
        controller._samples[f'qa_core_{index}'] = [{'x': float(x), 'y': 5. + index} for x in range(1, 131, 2)]
    cpu.setProperty('seriesColors', ['#8b74b4'] * 8)
    cpu.setProperty('seriesNames', [f'CPU {index}' for index in range(8)])
    cpu.setProperty('seriesStatus', ['已记录'] * 8)
    cpu.setProperty('seriesEnabled', [True] * 8)
    cpu.setProperty('metricKeys', [f'qa_core_{index}' for index in range(8)])
    controller.samplesChanged.emit()
    page.setProperty('contentY', cpu.y() - 12)
    QTest.qWait(40)
    legend = find('chartLegend:cpu')
    contents = legend.property('contentItem')
    before = page.property('contentY')
    _wheel(window, find('legendRow:qa_core_0').mapToScene(QPointF(35, 7)))
    assert contents.property('contentY') > 1
    assert page.property('contentY') == pytest.approx(before, abs=1)

    contents.setProperty('contentY', legend.property('contentHeight') - legend.height())
    QTest.qWait(35)
    _wheel(window, legend.mapToScene(QPointF(35, legend.height() - 7)))
    assert page.property('contentY') > before + 1
    page.setProperty('contentY', before)
    contents.setProperty('contentY', 0.)
    QTest.qWait(35)
    _wheel(window, legend.mapToScene(QPointF(35, 7)), 120)
    assert page.property('contentY') < before - 1
