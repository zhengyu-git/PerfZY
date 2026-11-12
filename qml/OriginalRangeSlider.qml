import QtQuick
import QtQuick.Controls

RangeSlider {
    id: control
    from: 0; to: 1
    first.value: 0; second.value: 1
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - 2
        width: control.availableWidth; height: 4
        radius: height / 2
        color: Theme.chartGrid
        Rectangle {
            x: control.first.visualPosition * parent.width
            width: (control.second.visualPosition - control.first.visualPosition) * parent.width
            height: parent.height
            radius: height / 2
            color: Qt.rgba(Theme.blue.r, Theme.blue.g, Theme.blue.b, .48)
        }
    }
    first.handle: Rectangle {
        objectName: "rangeStartHandle"
        x: control.leftPadding + control.first.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 12; implicitHeight: 16; radius: 6
        antialiasing: true
        color: Theme.chartBackground; border.color: control.first.pressed || control.activeFocus ? Theme.blue : Theme.chartAxis
    }
    second.handle: Rectangle {
        objectName: "rangeEndHandle"
        x: control.leftPadding + control.second.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 12; implicitHeight: 16; radius: 6
        antialiasing: true
        color: Theme.chartBackground; border.color: control.second.pressed || control.activeFocus ? Theme.blue : Theme.chartAxis
    }
}
