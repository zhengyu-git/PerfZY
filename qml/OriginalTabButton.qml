import QtQuick
import QtQuick.Controls

TabButton {
    id: control
    implicitHeight: 34
    font.pixelSize: 10
    contentItem: Text {
        text: control.text
        color: control.checked ? Theme.color("#bcc6e2") : Theme.color("#8892ad")
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: control.checked ? Theme.color("#32384c") : Theme.color("#292d3a")
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 2; color: Theme.color("#a8b8e2"); visible: control.checked }
    }
}
