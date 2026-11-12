import QtQuick
import QtQuick.Controls

OriginalComboBox {
    id: control
    implicitHeight: 34
    property real popupWidth: width
    leftPadding: 12; rightPadding: 30
    font.pixelSize: 12
    contentItem: ElidedLabel {
        fullText: control.currentIndex < 0 && control.placeholderText ? control.placeholderText : control.displayText
        color: control.enabled ? Theme.text : Theme.muted
        font: control.font
    }
    indicator: IconGlyph {
        width: 14; height: 14; name: "chevronDown"; tint: Theme.muted
        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
    }
    background: Rectangle {
        radius: 8; antialiasing: true
        color: control.enabled ? Theme.control : Theme.paneAlt
        border.color: control.activeFocus ? Theme.blue : Theme.border
    }
    delegate: ItemDelegate {
        required property var modelData
        required property int index
        objectName: "captureComboOption:" + control.objectName + ":" + index
        width: ListView.view ? ListView.view.width : control.width
        height: 34
        highlighted: control.highlightedIndex === index
        contentItem: ElidedLabel {
            fullText: control.textRole.length > 0 && modelData ? modelData[control.textRole] : modelData
            color: Theme.text; font.pixelSize: 12
        }
        background: Rectangle { radius: 6; color: highlighted ? Theme.hover : Theme.control }
        ToolTip.visible: hovered && contentItem.contentElided
        ToolTip.text: contentItem.fullText
        ToolTip.delay: 450
    }
    popup: Popup {
        objectName: "captureComboPopup:" + control.objectName
        y: control.height + 4; width: control.popupWidth
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 306)
        padding: 4
        contentItem: ListView {
            clip: true; implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { radius: 10; color: Theme.control; border.color: Theme.border }
    }
}
