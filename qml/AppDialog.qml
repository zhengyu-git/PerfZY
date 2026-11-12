import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: control
    property string subtitle: ""
    property bool dismissible: true
    property Component titleActions: null
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent ? parent.width - 40 : 440)
    implicitHeight: Math.min(parent ? parent.height - 40 : 700,
        Math.max(160, implicitHeaderHeight + implicitContentHeight + implicitFooterHeight + topPadding + bottomPadding))
    modal: true
    focus: true
    closePolicy: dismissible ? Popup.CloseOnEscape : Popup.NoAutoClose
    standardButtons: Dialog.NoButton
    leftPadding: 20; rightPadding: 20; topPadding: 6; bottomPadding: 20
    font.family: Theme.fontFamily; font.pixelSize: 12
    palette.window: Theme.chartBackground; palette.base: Theme.control
    palette.text: Theme.text; palette.windowText: Theme.text
    palette.button: Theme.control; palette.buttonText: Theme.text
    palette.placeholderText: Theme.muted; palette.highlight: Theme.primaryFill
    background: Rectangle { radius: 12; color: Theme.chartBackground; border.color: Theme.border; antialiasing: true }
    Overlay.modal: Rectangle { color: Theme.dark ? "#70000000" : "#280f172a" }
    header: Item {
        implicitHeight: control.subtitle.length ? 82 : 58
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 20; anchors.rightMargin: 12
            anchors.topMargin: 12; anchors.bottomMargin: 10; spacing: 10
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 5
                Label {
                    objectName: "appDialogTitle"
                    Layout.fillWidth: true; text: control.title; textFormat: Text.PlainText
                    elide: Text.ElideRight; color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold
                }
                Label {
                    visible: control.subtitle.length > 0
                    Layout.fillWidth: true; text: control.subtitle; textFormat: Text.PlainText
                    wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                }
            }
            Loader { sourceComponent: control.titleActions; visible: !!control.titleActions }
            ToolButton {
                id: dismissButton
                objectName: "dialogClose:" + control.objectName
                Layout.preferredWidth: 30; Layout.preferredHeight: 30
                Layout.alignment: Qt.AlignTop
                enabled: control.dismissible
                Accessible.name: "关闭"; focusPolicy: Qt.TabFocus
                contentItem: IconGlyph { name: "close"; tint: Theme.muted; opacity: dismissButton.enabled ? 1 : .4 }
                background: Rectangle { radius: 7; color: dismissButton.hovered && dismissButton.enabled ? Theme.hover : "transparent" }
                onClicked: control.reject()
            }
        }
    }
}
