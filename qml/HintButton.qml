import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string toolTipText: ""
    property string disabledReason: ""
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
}
