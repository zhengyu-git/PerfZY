import QtQuick
import QtQuick.Controls

Button {
    id: control
    property bool expanded: false
    text: "性能指标"
    implicitWidth: 36; implicitHeight: 36
    padding: 8
    Accessible.name: text
    contentItem: Item {
        IconGlyph { anchors.centerIn: parent; width: 18; height: 18; name: "sliders"; tint: Theme.blue }
    }
    background: Rectangle {
        radius: 10
        color: control.down || control.expanded ? Theme.selected : control.hovered ? Theme.hover : Theme.control
        border.color: control.expanded || control.activeFocus ? Theme.blue : Theme.border
        antialiasing: true
    }
    ToolTip.visible: hovered
    ToolTip.text: "性能指标 · 选择采集指标、显示图表及排序"
    ToolTip.delay: 450
}
