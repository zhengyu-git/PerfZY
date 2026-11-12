import QtQuick
import QtQuick.Controls

OriginalTextField {
    implicitHeight: 34
    leftPadding: 10; rightPadding: 10
    font.pixelSize: 12
    horizontalAlignment: Text.AlignLeft
    color: enabled ? Theme.text : Theme.muted
    placeholderTextColor: Theme.muted
    selectedTextColor: "white"
    background: Rectangle {
        radius: 8; antialiasing: true
        color: parent.enabled ? Theme.control : Theme.paneAlt
        border.color: parent.activeFocus ? Theme.blue : Theme.border
    }
}
