import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    property string toolTipText: ""
    property string disabledReason: ""
    ControlHint { targetControl: control; text: control.enabled ? control.toolTipText : control.disabledReason || control.toolTipText }
    property string placeholderText: ""
    implicitHeight: 32
    leftPadding: 10; rightPadding: 30
    font.pixelSize: 10
    contentItem: Text {
        text: control.currentIndex < 0 && control.placeholderText ? control.placeholderText : control.displayText
        color: Theme.color("#bcc6e2")
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Image {
        width: 10; height: 7
        anchors.right: parent.right; anchors.rightMargin: 9
        anchors.verticalCenter: parent.verticalCenter
        source: "../assets/perfzy/icon/down-arrow.png"
    }
    background: Rectangle {
        radius: 6
        color: Theme.color("#32384c")
        border.width: 1
        border.color: control.hovered || control.activeFocus ? Theme.color("#4371fd") : Theme.color("#42485a")
    }
    delegate: ItemDelegate {
        required property var modelData
        width: control.width; height: 32
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: control.textRole.length > 0 && modelData ? modelData[control.textRole] : modelData
            color: Theme.color("#bcc6e2"); font.pixelSize: 10; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        }
        background: Rectangle { color: highlighted ? Theme.color("#2a2f40") : Theme.color("#222633"); border.color: Theme.color("#151928") }
    }
    popup: Popup {
        y: control.height; width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight, 288)
        padding: 1
        contentItem: ListView {
            clip: true; implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { color: Theme.color("#222633"); border.color: Theme.color("#4371fd") }
    }
}
