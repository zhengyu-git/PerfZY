import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    objectName: "statusPanel"
    property string statusText: ""
    property string summaryText: "就绪"
    property string compactText: ""
    property string healthText: ""
    property string metricDetails: ""
    property string logText: ""
    property bool collecting: false
    property bool viewingSession: false
    property bool warning: false
    readonly property string details: statusText + (healthText.length ? "\n\n" + healthText : "") + (metricDetails.length ? "\n\n" + metricDetails : "")
    readonly property bool logDialogVisible: detailDialog.visible
    signal closeSession()
    function openDetails() { detailDialog.open() }
    implicitHeight: 62
    radius: 12
    antialiasing: true
    color: Theme.control; border.color: Theme.border
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8
        anchors.topMargin: 5; anchors.bottomMargin: 5; spacing: 6
        ColumnLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
            RowLayout {
                Layout.fillWidth: true; spacing: 5
                Rectangle { width: 6; height: 6; radius: 3; color: panel.warning ? Theme.color("#e4ad59") : panel.collecting ? Theme.color("#64ca85") : Theme.color("#8ba0c5") }
                Text {
                    objectName: "statusMessage"
                    Layout.fillWidth: true; text: panel.summaryText; elide: Text.ElideRight; textFormat: Text.PlainText
                    color: panel.warning ? Theme.color("#e4bd7d") : Theme.color("#c7d3e8"); font.pixelSize: 12
                }
            }
            Text {
                objectName: "statusHealth"
                Layout.fillWidth: true; text: panel.compactText; textFormat: Text.PlainText
                color: Theme.color("#94a3bf"); font.pixelSize: 10
            }
        }
        RowLayout {
            Layout.alignment: Qt.AlignVCenter; spacing: 5
            CaptureButton {
                objectName: "closeSessionViewButton"
                Layout.preferredWidth: 84; Layout.minimumHeight: 28; Layout.maximumHeight: 28
                visible: panel.viewingSession; text: "取消查看"
                toolTipText: "关闭历史记录，返回采集工作区"
                onClicked: panel.closeSession()
            }
        }
    }
    AppDialog {
        id: detailDialog; objectName: "statusDetailDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(740, parent ? parent.width - 32 : 740)
        height: Math.min(470, parent ? parent.height - 32 : 470)
        modal: true; title: "运行日志与状态"; standardButtons: Dialog.NoButton
        onOpened: { copiedLabel.text = "复制日志"; logArea.cursorPosition = logArea.length }
        
        
        contentItem: ColumnLayout {
            spacing: 9
            Text { Layout.fillWidth: true; text: "保留本次启动以来最近 500 条日志；可选中文字或复制全部。"; color: Theme.color("#a9b6d0"); font.pixelSize: 12; wrapMode: Text.WordWrap }
            TabBar {
                id: logTabs; Layout.fillWidth: true
                background: Rectangle { color: Theme.color("#202638") }
                DialogTabButton { text: "运行日志" }
                DialogTabButton { text: "当前状态详情" }
            }
            ScrollView {
                id: logScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth
                TextArea {
                    id: logArea; objectName: "runtimeLogText"
                    width: logScroll.availableWidth
                    text: logTabs.currentIndex === 0 ? panel.logText : panel.details
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere
                    textFormat: TextEdit.PlainText; color: Theme.color("#d9e3f6")
                    font.pixelSize: 12; font.family: "Consolas"
                    selectionColor: Theme.color("#4169e1"); selectedTextColor: Theme.color("#ffffff")
                    background: Rectangle { color: Theme.color("#1b2232"); radius: 3 }
                    onTextChanged: if (detailDialog.visible && logTabs.currentIndex === 0) cursorPosition = length
                }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton {
                    id: copiedLabel; objectName: "copyRuntimeLogButton"
                    Layout.preferredWidth: 80; text: "复制日志"
                    onClicked: if (appController.copyLogText(logArea.text)) text = "已复制"
                }
                CaptureButton { Layout.preferredWidth: 64; text: "关闭"; onClicked: detailDialog.close() }
            }
        }
    }
}
