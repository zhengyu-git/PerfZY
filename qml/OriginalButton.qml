import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string toolTipText: ""
    property string disabledReason: ""
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
    implicitHeight: 28
    font.pixelSize: 10
    contentItem: Text {
        text: control.text
        color: control.enabled ? Theme.color("#c7d1e8") : Theme.color("#8995ae")
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: control.down ? Theme.color("#202534") : control.hovered ? Theme.color("#373d50") : Theme.color("#32384c")
        border.color: control.hovered ? Theme.color("#4371fd") : Theme.color("#42485a")
        radius: 6
    }
}
