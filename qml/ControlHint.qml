import QtQuick
import QtQuick.Controls

// Observe from the enabled parent: a disabled control cannot receive hover.
HoverHandler {
    id: hint
    property Item targetControl: null
    property string text: ""
    parent: targetControl ? targetControl.parent : null
    target: null
    blocking: false
    readonly property bool pointerInside: {
        if (!targetControl || !parent || !hovered || !targetControl.visible) return false
        var position = targetControl.mapFromItem(parent, point.position.x, point.position.y)
        return position.x >= 0 && position.y >= 0 && position.x < targetControl.width && position.y < targetControl.height
    }
    property ToolTip popup: ToolTip {
        objectName: "controlHint:" + (hint.targetControl ? hint.targetControl.objectName : "")
        parent: hint.targetControl ? hint.targetControl.parent : null
        x: hint.targetControl ? hint.targetControl.x + (hint.targetControl.width - width) / 2 : 0
        y: hint.targetControl ? hint.targetControl.y - height - 6 : 0
        text: hint.text
        visible: hint.pointerInside && text.length > 0
        delay: 450; timeout: 12000; margins: 8; padding: 9
        width: Math.min(360, implicitWidth)
        contentItem: Text {
            text: hint.text; color: Theme.text; font.pixelSize: 12
            textFormat: Text.PlainText; wrapMode: Text.WordWrap
        }
        background: Rectangle { color: Theme.sidebar; border.color: Theme.border; radius: 8 }
    }
}
