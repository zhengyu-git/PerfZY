import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property string glyph: ""
    property string toolTipText: ""
    property string disabledReason: ""
    property bool primary: false
    implicitHeight: 30
    leftPadding: 7; rightPadding: 7
    font.pixelSize: 12
    contentItem: RowLayout {
        spacing: 6
        IconGlyph {
            visible: control.glyph.length > 0
            Layout.preferredWidth: 16; Layout.preferredHeight: 16
            name: control.glyph; tint: control.enabled && control.primary ? Theme.blue : Theme.muted
        }
        Text {
            visible: control.text.length > 0
            Layout.fillWidth: true
            text: control.text; font: control.font
            color: !control.enabled ? Theme.muted : control.primary ? Theme.blue : Theme.text
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
    }
    background: Rectangle {
        radius: 6
        color: control.down ? Theme.selected : control.hovered ? Theme.hover : "transparent"
        border.color: control.activeFocus ? Theme.blue : "transparent"
    }
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
}
