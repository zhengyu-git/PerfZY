import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

AppDialog {
    id: panel
    title: "选择窗口"
    objectName: "windowTargetPanel"
    property string selectionMode: "performance"
    property bool canApply: selectionMode === "screenshot" || appController.canSelectPerformanceWindow
    anchors.centerIn: parent
    width: Math.min(700, parent ? parent.width - 40 : 700)
    height: Math.min(590, parent ? parent.height - 32 : 590)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton
    padding: 16
    onOpened: {
        selectionMode = appController.canSelectPerformanceWindow ? "performance" : "screenshot"
        appController.refreshWindowCandidates()
    }
    onClosed: appController.cancelWindowPick()
    
    
    contentItem: ColumnLayout {
        spacing: 10
        Text { objectName: "performanceWindowLabel"; Layout.fillWidth: true; text: "性能采集目标：" + appController.performanceWindowLabel; textFormat: Text.PlainText; color: Theme.color("#dce5fb"); font.pixelSize: 12; elide: Text.ElideRight }
        Text { objectName: "screenshotWindowLabel"; Layout.fillWidth: true; text: "截图目标：" + appController.screenshotWindowLabel; textFormat: Text.PlainText; color: Theme.color("#a9bedf"); font.pixelSize: 12; elide: Text.ElideRight }
        RowLayout {
            Layout.fillWidth: true
            CaptureButton {
                Layout.fillWidth: true
                text: panel.selectionMode === "performance" ? "● 设置性能采集目标" : "设置性能采集目标"
                enabled: appController.canSelectPerformanceWindow
                disabledReason: appController.collecting ? "采集中性能目标已锁定；停止采集后可更改。独立截图目标仍可调整。" : appController.busyReason || "请先选择 Windows 设备。"
                onClicked: { appController.cancelWindowPick(); panel.selectionMode = "performance" }
            }
            CaptureButton {
                Layout.fillWidth: true
                text: panel.selectionMode === "screenshot" ? "● 设置独立截图目标" : "设置独立截图目标"
                onClicked: { appController.cancelWindowPick(); panel.selectionMode = "screenshot" }
            }
        }
        Text {
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.color("#96a7c6"); font.pixelSize: 12
            text: panel.selectionMode === "performance"
                  ? "绑定所选窗口的真实 PID；同名程序的不同进程会分别识别。采集进行中或系统忙碌时不可更改性能目标。"
                  : "只更改截图来源，不改变 FPS、CPU 等性能指标采集的应用；可在采集中调整。"
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            color: Theme.color("#151c2c"); border.color: Theme.color("#3a4762"); radius: 4
            ListView {
                id: windowsList
                objectName: "windowCandidatesList"
                anchors.fill: parent; anchors.margins: 4; clip: true
                model: appController.windowCandidates
                spacing: 3
                delegate: ItemDelegate {
                    id: windowRow
                    objectName: "windowCandidateRow"
                    required property var modelData
                    required property int index
                    width: windowsList.width; height: 54
                    enabled: panel.canApply && appController.windowPickSeconds === 0
                    ControlHint { targetControl: windowRow; text: !windowRow.enabled ? (appController.windowPickSeconds > 0 ? "正在倒计时拾取窗口，请完成或取消拾取。" : "采集中不能更改性能目标，请停止采集后操作。") : "选择此窗口作为" + (panel.selectionMode === "screenshot" ? "截图目标" : "性能采集目标") }
                    background: Rectangle { color: windowRow.hovered ? Theme.color("#30415f") : Theme.color("#202b40"); radius: 3 }
                    contentItem: Column {
                        spacing: 4
                        Text { width: parent.width; text: windowRow.modelData.title || "无标题窗口"; textFormat: Text.PlainText; color: Theme.color("#e3eaff"); font.pixelSize: 12; elide: Text.ElideRight }
                        Text { width: parent.width; text: (windowRow.modelData.process_name || "未知进程") + " · PID " + windowRow.modelData.pid + " · HWND " + windowRow.modelData.hwnd; textFormat: Text.PlainText; color: Theme.color("#99aac8"); font.pixelSize: 12; elide: Text.ElideRight }
                    }
                    onClicked: appController.selectWindowCandidate(index, panel.selectionMode)
                }
                ScrollBar.vertical: ScrollBar { }
                Text { anchors.centerIn: parent; visible: windowsList.count === 0; text: "暂无可选择窗口，请刷新或使用鼠标拾取。"; color: Theme.color("#8e9fbf"); font.pixelSize: 12 }
            }
        }
        Text {
            objectName: "windowPickStatusText"
            Layout.fillWidth: true; Layout.preferredHeight: 40; wrapMode: Text.WordWrap
            text: appController.windowPickStatus
            color: appController.windowPickSeconds > 0 ? Theme.chartWarning : Theme.color("#b5c6e8"); font.pixelSize: 12
        }
        Text {
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.color("#8396b9"); font.pixelSize: 12
            text: "鼠标拾取：点击后在 3 秒内把鼠标移到目标窗口并停留，无需点击。Esc、取消或关闭此面板会终止拾取；PerfZY 自身窗口不会被选中。"
        }
        RowLayout {
            objectName: "windowTargetActionRow"
            Layout.fillWidth: true
            CaptureButton { objectName: "refreshWindowsButton"; text: "刷新列表"; enabled: appController.windowPickSeconds === 0; disabledReason: "正在拾取窗口，请先完成或取消拾取。"; onClicked: appController.refreshWindowCandidates() }
            CaptureButton { objectName: "windowPickButton"; text: appController.windowPickSeconds > 0 ? "拾取中 " + appController.windowPickSeconds + "…" : "3 秒后拾取鼠标窗口"; enabled: panel.canApply && appController.windowPickSeconds === 0; disabledReason: appController.windowPickSeconds > 0 ? "正在倒计时拾取窗口；将鼠标移到目标窗口，或取消后重试。" : "采集中性能目标已锁定，请停止采集或切换到独立截图目标。"; onClicked: appController.startWindowPick(panel.selectionMode) }
            CaptureButton { objectName: "cancelWindowPickButton"; text: "取消拾取"; enabled: appController.windowPickSeconds > 0; disabledReason: "当前没有进行中的窗口拾取。"; onClicked: appController.cancelWindowPick() }
            Item { Layout.fillWidth: true }
            CaptureButton { objectName: "closeWindowPanelButton"; text: "关闭"; onClicked: panel.close() }
        }
        CaptureButton {
            objectName: "followPerformanceScreenshotButton"
            Layout.fillWidth: true
            text: "恢复截图跟随性能采集目标"
            onClicked: appController.followPerformanceScreenshot()
        }
    }
}
