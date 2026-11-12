import QtQuick
import QtQuick.Controls

ToolButton {
    id: control
    required property url normalSource
    property url hoverSource: normalSource
    property string tip: ""
    implicitWidth: 28; implicitHeight: 28
    display: AbstractButton.IconOnly
    icon.source: hovered ? hoverSource : normalSource
    icon.width: 20; icon.height: 20
    background: Rectangle { color: control.hovered ? Theme.color("#373c4b") : "transparent"; border.color: "transparent" }
    ToolTip.visible: hovered && tip.length > 0
    ToolTip.text: tip
}
