import QtQuick
import QtQuick.Controls

Button {
    id: control
    required property bool selected
    implicitWidth: 68; implicitHeight: 32
    font.pixelSize: selected ? 11 : 10
    contentItem: Text {
        text: control.text
        color: control.selected ? Theme.color("#a8b8e2") : Theme.color("#8892ad")
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: control.selected ? Theme.color("#2d313e") : "transparent"
        border.color: control.selected ? Theme.color("#42485a") : "transparent"
        border.width: control.selected ? 1 : 0
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: control.selected ? Theme.color("#a8b8e2") : Theme.color("#181b2b") }
    }
}
