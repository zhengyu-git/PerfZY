import QtQuick
import QtQuick.Controls

// Readout text passes mouse drags through to the plot. Its wheel and thumb
// still scroll long readouts without taking over a new chart selection.
Flickable {
    id: view
    clip: true
    interactive: false
    boundsBehavior: Flickable.StopAtBounds
    // Reserve the gutter so wrapped readouts cannot oscillate as it appears.
    contentWidth: Math.max(0, width - scrollBar.width - 4)
    signal pageScrollRequested(real delta)
    WheelHandler {
        target: null
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: function(event) {
            var delta = event.pixelDelta.y || event.angleDelta.y / 120 * 48
            if (!isFinite(delta) || delta === 0) { event.accepted = false; return }
            var before = view.contentY
            view.contentY = Math.max(0, Math.min(Math.max(0, view.contentHeight - view.height), before - delta))
            var remainder = delta - (before - view.contentY)
            if (Math.abs(remainder) > .01) view.pageScrollRequested(remainder)
            event.accepted = true
        }
    }
    ScrollBar.vertical: ScrollBar {
        id: scrollBar
        objectName: "infoScrollBar:" + view.objectName
        parent: view
        x: view.width - width; y: 0; width: 6; height: view.height
        policy: ScrollBar.AsNeeded
        visible: size < 1
        active: size < 1
        contentItem: Rectangle {
            implicitWidth: 6; implicitHeight: 18; radius: 3
            color: scrollBar.pressed ? Theme.blue : Theme.chartAxis
        }
        background: Rectangle { radius: 3; color: Theme.chartGrid }
    }
}
