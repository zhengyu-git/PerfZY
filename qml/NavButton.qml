import QtQuick
import QtQuick.Controls

ToolButton {
    id: control
    property string glyph: "chart"
    property bool selected: false
    property string tip: ""
    implicitWidth: 40
    implicitHeight: 40
    Accessible.name: tip
    contentItem: Item {
        IconGlyph { anchors.centerIn: parent; width: control.glyph === "log" ? 18 : 21; height: width; name: control.glyph; tint: control.selected ? Theme.blue : Theme.muted }
    }
    background: Rectangle {
        radius: 8
        color: control.selected ? Theme.selected : control.hovered ? Theme.hover : "transparent"
    }
    ToolTip.visible: hovered && tip.length > 0
    ToolTip.text: tip
    ToolTip.delay: 450
}
