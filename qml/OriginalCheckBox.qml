import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    implicitHeight: 27
    spacing: 7
    indicator: Rectangle {
        implicitWidth: 14; implicitHeight: 14
        x: control.leftPadding; y: (control.height - height) / 2
        color: control.checked ? Theme.color("#4371fd") : Theme.color("#222633")
        border.color: control.hovered ? Theme.color("#6b8fff") : Theme.color("#596178")
        radius: 1
        IconGlyph { anchors.centerIn: parent; width: 11; height: 11; name: "check"; visible: control.checked; tint: "white" }
    }
    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text; color: Theme.color("#bcc6e2"); font.pixelSize: 10
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
}
