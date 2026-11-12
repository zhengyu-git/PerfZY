import QtQuick
import QtQuick.Controls

TabButton {
    id: control
    implicitHeight: 34
    leftPadding: 12; rightPadding: 12
    font.pixelSize: 12
    contentItem: Text {
        text: control.text; font: control.font
        color: control.checked ? Theme.text : Theme.muted
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 8
        color: control.checked ? Theme.control : control.hovered ? Theme.hover : "transparent"
        border.color: control.checked ? Theme.border : "transparent"
    }
}
