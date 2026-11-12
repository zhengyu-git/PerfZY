import QtQuick
import QtQuick.Controls

TextField {
    id: control
    property string toolTipText: ""
    property string disabledReason: ""
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
    implicitWidth: 54; implicitHeight: 22
    color: enabled ? Theme.color("#d4def2") : Theme.color("#a1adc5")
    placeholderTextColor: enabled ? Theme.color("#98a6c0") : Theme.color("#8996b0")
    palette.text: Theme.color("#d4def2"); palette.placeholderText: Theme.color("#98a6c0")
    palette.disabled.text: Theme.color("#a1adc5"); palette.disabled.placeholderText: Theme.color("#8996b0")
    selectionColor: Theme.color("#4169e1"); selectedTextColor: Theme.color("#ffffff")
    font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter
    selectByMouse: true
    background: Rectangle { color: Theme.color("#202534"); border.color: activeFocus ? Theme.color("#4371fd") : Theme.color("#42485a"); radius: 6 }
}
