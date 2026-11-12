import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    implicitHeight: 28
    spacing: 8
    font.pixelSize: 11
    indicator: Rectangle {
        implicitWidth: 16; implicitHeight: 16
        x: control.leftPadding; y: Math.round((control.height - height) / 2)
        radius: 4; antialiasing: true
        color: control.checked ? Theme.blue : Theme.control
        border.color: control.checked || control.activeFocus ? Theme.blue : Theme.border
        opacity: control.enabled ? 1 : .55
        IconGlyph { anchors.centerIn: parent; width: 12; height: 12; name: "check"; tint: "white"; visible: control.checked }
    }
    contentItem: Text {
        text: control.text; font: control.font
        leftPadding: control.indicator.width + control.spacing
        color: control.enabled ? Theme.text : Theme.muted
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
}
