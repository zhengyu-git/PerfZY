import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string toolTipText: ""
    property string disabledReason: ""
    property string tone: "neutral"
    property color accentColor: Theme.primaryFill
    implicitHeight: 34
    leftPadding: 12; rightPadding: 12
    font.pixelSize: 12; font.weight: Font.Medium
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
    contentItem: Text {
        text: control.text; font: control.font
        color: !control.enabled ? Theme.muted : control.tone === "primary" ? "white" : control.tone === "danger" ? Theme.red : Theme.text
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 8; antialiasing: true
        color: !control.enabled ? Theme.paneAlt
            : control.tone === "primary" ? (control.down ? Qt.darker(control.accentColor, 1.12) : control.hovered ? Qt.lighter(control.accentColor, 1.08) : control.accentColor)
            : control.tone === "danger" ? Qt.rgba(Theme.red.r, Theme.red.g, Theme.red.b, control.hovered ? .18 : .10)
            : control.down ? Theme.selected : control.hovered ? Theme.hover : Theme.control
        border.color: !control.enabled ? Theme.border
            : control.tone === "primary" ? control.accentColor
            : control.tone === "danger" ? Qt.rgba(Theme.red.r, Theme.red.g, Theme.red.b, .32)
            : control.activeFocus ? Theme.blue : Theme.border
    }
}
