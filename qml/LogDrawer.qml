import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: drawer
    signal closeRequested()
    signal detailsRequested()
    Rectangle {
        id: surface
        anchors.fill: parent
        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.bottomMargin: 10
        color: Theme.sidebar; border.color: Theme.border
        radius: 12; antialiasing: true
    }
    ColumnLayout {
        anchors.fill: surface; anchors.margins: 10; spacing: 8
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Text { text: "运行日志"; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
            Text { text: "最近 500 条"; color: Theme.muted; font.pixelSize: 10 }
            Item { Layout.fillWidth: true }
            OriginalButton { text: "状态详情"; font.pixelSize: 11; Layout.preferredWidth: 76; Layout.preferredHeight: 28; onClicked: drawer.detailsRequested() }
            OriginalButton {
                id: copyButton
                text: "复制日志"; font.pixelSize: 11; Layout.preferredWidth: 76; Layout.preferredHeight: 28
                onClicked: if (appController.copyLogText(logArea.text)) text = "已复制"
            }
            NavButton { objectName: "closeLogDrawerButton"; Layout.preferredWidth: 28; Layout.maximumWidth: 28; Layout.preferredHeight: 28; glyph: "close"; tip: "收起日志"; onClicked: drawer.closeRequested() }
        }
        ScrollView {
            id: logScroll
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            background: Rectangle { color: Theme.content; radius: 8 }
            TextArea {
                id: logArea; objectName: "logDrawerText"
                width: logScroll.availableWidth
                text: appController.runtimeLogText
                color: Theme.text; readOnly: true; selectByMouse: true
                textFormat: TextEdit.PlainText; wrapMode: TextEdit.WrapAnywhere
                font.family: "Consolas"; font.pixelSize: 11
                selectionColor: Theme.blue; selectedTextColor: "white"
                background: Item {}
                onTextChanged: {
                    copyButton.text = "复制日志"
                    if (drawer.visible) cursorPosition = length
                }
            }
        }
    }
}
