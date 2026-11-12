import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

MenuItem {
    id: control
    property string glyph: ""
    property string disabledReason: ""
    property string toolTipText: ""
    property bool destructive: false
    property bool marked: false
    implicitWidth: 208; implicitHeight: 34
    width: parent ? parent.width : implicitWidth
    leftPadding: 10; rightPadding: 10
    font.pixelSize: 12
    contentItem: RowLayout {
        spacing: 8
        IconGlyph {
            Layout.preferredWidth: 16; Layout.preferredHeight: 16
            visible: control.glyph.length > 0
            name: control.glyph
            tint: control.destructive && control.enabled ? Theme.red : Theme.muted
        }
        ElidedLabel {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            fullText: control.text; font: control.font
            color: !control.enabled ? Theme.muted : control.destructive ? Theme.red : Theme.text
        }
        IconGlyph {
            visible: control.marked
            Layout.preferredWidth: 14; Layout.preferredHeight: 14
            name: "check"; tint: Theme.blue
        }
    }
    background: Rectangle { radius: 6; color: control.highlighted ? Theme.hover : "transparent" }
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason }
}
