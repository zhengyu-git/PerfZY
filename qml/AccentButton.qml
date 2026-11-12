import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string toolTipText: ""
    property string disabledReason: ""
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
    property color accentColor: Theme.color("#4169e1")
    implicitHeight: 28
    font.pixelSize: 10; font.weight: Font.Medium
    contentItem: Text {
        text: control.text
        color: control.enabled ? "white" : Theme.color("#929eb7")
        font: control.font
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: !control.enabled ? Theme.color("#303544") : control.down ? Qt.darker(control.accentColor, 1.28) : control.hovered ? Qt.lighter(control.accentColor, 1.12) : control.accentColor
        border.color: !control.enabled ? Theme.color("#444b5d") : Qt.lighter(control.accentColor, 1.3)
        radius: 6
    }
}
