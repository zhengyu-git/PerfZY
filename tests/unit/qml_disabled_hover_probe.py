"""A fresh Qt GUI process avoids mixing QCoreApplication and QGuiApplication."""
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QPoint, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

app = QGuiApplication([])
engine = QQmlApplicationEngine()
engine.loadData(b'''
import QtQuick
import QtQuick.Controls
ApplicationWindow {
    id: root
    width: 420; height: 300; visible: true
    property int clicks: 0
    OriginalButton {
        objectName: "target"; x: 30; y: 70; width: 110; height: 30
        enabled: false; text: "Merge"
        disabledReason: "Stop collection before merging."
        toolTipText: "Merge selected records."
        onClicked: root.clicks += 1
    }
    OriginalTextField {
        objectName: "field"; x: 30; y: 140; width: 220; height: 32
        placeholderText: "Optional test conditions"
    }
    StatusPanel {
        objectName: "statusProbe"; x: 20; y: 220; width: 238; height: 54
        viewingSession: true; summaryText: "Viewing record"; compactText: "Local record"
    }
}
''', QUrl.fromLocalFile(str(Path(sys.argv[1]) / 'qml' / 'HoverVerification.qml')))
assert engine.rootObjects(), 'Native QML components failed to load'
window = engine.rootObjects()[0]

def settle(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)

settle(.15)
QTest.mouseMove(window, QPoint(80, 85))
settle(.7)
tip = window.findChild(QObject, 'controlHint:target')
assert tip is not None and tip.property('opened'), 'Disabled button must show an actual tooltip'
assert 'Stop collection' in tip.property('text')
from PySide6.QtCore import Qt
QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(80, 85))
settle(.05)
assert window.property('clicks') == 0, 'Hover support must not enable a disabled action'
QTest.mouseMove(window, QPoint(390, 200))
settle(.2)
assert not tip.property('opened'), 'Tooltip must close after leaving the target'
target = window.findChild(QObject, 'target')
target.setProperty('enabled', True)
QTest.mouseMove(window, QPoint(80, 85))
settle(.7)
assert tip.property('opened') and tip.property('text') == 'Merge selected records.'
QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(80, 85))
assert window.property('clicks') == 1

def luminance(color):
    channels = [color.redF(), color.greenF(), color.blueF()]
    converted = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * w for c, w in zip(converted, (.2126, .7152, .0722)))

from PySide6.QtGui import QColor
field = window.findChild(QObject, 'field')
background = luminance(QColor('#202534'))
for enabled in (True, False):
    field.setProperty('enabled', enabled)
    foreground = luminance(field.property('placeholderTextColor'))
    assert (foreground + .05) / (background + .05) >= 4.5, 'Placeholder must remain readable'
from PySide6.QtCore import QPointF
from PySide6.QtQuick import QQuickItem
status = window.findChild(QQuickItem, 'statusProbe')
log_button = window.findChild(QQuickItem, 'runtimeLogButton')
close_button = window.findChild(QQuickItem, 'closeSessionViewButton')
assert log_button.isVisible() and close_button.isVisible()
assert log_button.parentItem() == close_button.parentItem(), 'Status actions must share one row'
first = log_button.mapToScene(QPointF(0, 0))
second = close_button.mapToScene(QPointF(0, 0))
assert abs(first.y() - second.y()) < 1
assert first.x() + log_button.width() + 4 <= second.x(), 'Log and close must not overlap'
assert second.y() + close_button.height() <= status.y() + status.height()
window.close()
print('disabled hover, interaction isolation, contrast and viewing-state button layout verified')
