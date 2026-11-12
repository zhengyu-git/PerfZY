import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: settingsScroll
    objectName: "settingsPanel"
    property string section: "capture"
    readonly property bool connectionDialogVisible: connectionDialog.visible
    clip: true
    background: Rectangle { color: Theme.window }
    ColumnLayout {
        width: settingsScroll.availableWidth; spacing: 0
        ColumnLayout {
            Layout.fillWidth: true
            visible: settingsScroll.section === "capture"
            spacing: 0
        RowLayout {
            Layout.fillWidth: true; Layout.margins: 14
            Text { text: "采集设置"; color: Theme.color("#e1e7f5"); font.pixelSize: 13; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            Text { text: "自动保存"; color: Theme.color("#8291ae"); font.pixelSize: 12 }
        }
        GridLayout {
            Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
            columns: 2; columnSpacing: 12; rowSpacing: 12
            Text { text: "采样间隔"; color: Theme.color("#b9c3d9"); font.pixelSize: 12 }
            OriginalComboBox {
                Layout.fillWidth: true; Layout.minimumWidth: 90; Layout.preferredHeight: 34
                property var values: [0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]
                model: values.map(function(v) { return v + " 秒" })
                currentIndex: Math.max(0, values.indexOf(appController.sampleIntervalSeconds))
                onActivated: function(index) { appController.setSampleInterval(values[index]) }
            }
            Text { text: "自动截图"; color: Theme.color("#b9c3d9"); font.pixelSize: 12 }
            OriginalCheckBox { text: checked ? "已开启" : "已关闭"; checked: appController.screenshotEnabled; onToggled: appController.setScreenshotEnabled(checked) }
            Text {
                Layout.columnSpan: 2; Layout.fillWidth: true
                visible: appController.collecting && !appController.screenshotEnabled
                text: appController.paused ? "截图已关闭，当前采集已暂停。" : "截图已关闭，性能指标继续采集；重新开启后记录之后的画面。"
                color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap
            }
            Text { text: "截图间隔"; color: Theme.color("#b9c3d9"); font.pixelSize: 12 }
            OriginalComboBox {
                Layout.fillWidth: true; Layout.preferredHeight: 34; enabled: appController.screenshotEnabled
                disabledReason: "请先开启自动截图，再设置截图间隔。"
                property var values: [0.5, 1, 2, 3, 5, 10]
                model: values.map(function(v) { return v + " 秒" })
                currentIndex: Math.max(0, values.indexOf(appController.screenshotIntervalSeconds))
                onActivated: function(index) { appController.setScreenshotInterval(values[index]) }
            }
            Text { text: "截图画质"; color: Theme.color("#b9c3d9"); font.pixelSize: 12 }
            OriginalComboBox {
                Layout.fillWidth: true; Layout.preferredHeight: 34; enabled: appController.screenshotEnabled
                disabledReason: "请先开启自动截图，再设置截图画质。"
                model: ["480p", "720p", "1080p", "原始分辨率"]
                currentIndex: Math.max(0, [854, 1280, 1920, 0].indexOf(appController.screenshotMaxWidth))
                onActivated: function(index) { appController.setScreenshotMaxWidth([854, 1280, 1920, 0][index]) }
                toolTipText: "分辨率越高、间隔越短，采集开销越大。常规测试可用 720p / 3 秒。"
            }
        }
        ColumnLayout {
            visible: appController.windowsResourceScopeAvailable
            Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14; Layout.topMargin: 18; spacing: 7
            Text { text: "Windows 资源范围"; color: Theme.color("#d2ddef"); font.pixelSize: 12 }
            OriginalComboBox {
                objectName: "windowsResourceScopeSelector"
                Layout.fillWidth: true; Layout.preferredHeight: 34
                model: ["仅当前进程", "包含子进程"]
                currentIndex: ["selected", "tree"].indexOf(appController.windowsResourceScope)
                placeholderText: "历史记录未注明"
                enabled: !appController.collecting && !appController.collectionStarting && !appController.viewingSession
                disabledReason: appController.viewingSession ? "正在查看历史记录；取消查看后可设置下一次采集的资源范围。" : "采集中资源范围已锁定，请停止采集后修改。"
                onActivated: function(index) { appController.setWindowsResourceScope(["selected", "tree"][index]) }
            }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.color("#8596b5"); font.pixelSize: 12
                text: appController.viewingSession ? appController.windowsResourceScopeText
                    : "CPU、内存、I/O 和线程等资源按此范围统计。FPS 保持原有渲染流口径。开始采集后锁定。"
            }
        }
        ColumnLayout {
            visible: appController.iosFpsLimitAvailable
            Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14; Layout.topMargin: 18; spacing: 7
            Text { text: "iOS FPS 上限校验"; color: Theme.color("#d2ddef"); font.pixelSize: 12 }
            OriginalComboBox {
                objectName: "iosFpsLimitSelector"
                Layout.fillWidth: true; Layout.preferredHeight: 34
                property var values: [0, 30, 60, 90, 120, 240]
                model: values.map(function(value) { return value ? value + " FPS" : "不设上限" })
                currentIndex: Math.max(0, values.indexOf(appController.iosFpsLimit))
                enabled: !appController.collecting && !appController.collectionStarting && !appController.viewingSession && appController.selectedTargetIndex >= 0
                toolTipText: "按游戏的真实帧率上限设置；超出的源读数标为无效并保留原始审计"
                disabledReason: appController.viewingSession ? "此处显示历史记录的校验上限；取消查看后可设置下一次采集。" : appController.collecting || appController.collectionStarting ? "采集中校验规则已锁定，请停止采集后修改。" : "请先选择目标应用。"
                onActivated: function(index) { appController.setIosFpsLimit(values[index]) }
            }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.color("#a3b0c9"); font.pixelSize: 12
                text: "按设备和应用分别记忆。超出上限的读数记为缺失，不强行改成上限值；原始数据保留。"
            }
        }
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: settingsScroll.section === "report"
            spacing: 0
        Rectangle { Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14; Layout.topMargin: 20; Layout.bottomMargin: 16; height: 1; color: Theme.color("#3a4051") }
        Text { Layout.leftMargin: 14; Layout.bottomMargin: 12; text: "报告导出"; color: Theme.color("#e1e7f5"); font.pixelSize: 13; font.weight: Font.DemiBold }
        GridLayout {
            Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
            columns: 2; columnSpacing: 12
            Text { text: "截图模式"; color: Theme.color("#b9c3d9"); font.pixelSize: 12 }
            OriginalComboBox {
                Layout.fillWidth: true; Layout.minimumWidth: 90; Layout.preferredHeight: 34
                model: ["仅指标", "压缩截图", "原画质截图"]
                currentIndex: ["none", "compressed", "original"].indexOf(appController.reportScreenshotMode)
                enabled: !appController.reportGenerating
                disabledReason: "报告正在生成，完成后可修改截图导出模式。"
                onActivated: appController.setReportScreenshotMode(["none", "compressed", "original"][currentIndex])
            }
        }
        Text { Layout.fillWidth: true; Layout.margins: 14; text: "仅影响 HTML，原始截图保持不变。"; color: Theme.color("#8291ae"); font.pixelSize: 13; wrapMode: Text.WordWrap }
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: settingsScroll.section === "diagnostic"
            spacing: 0
        Rectangle { Layout.fillWidth: true; Layout.margins: 14; height: 1; color: Theme.color("#3a4051") }
        Text { Layout.leftMargin: 14; Layout.bottomMargin: 9; text: "连接诊断"; color: Theme.color("#e1e7f5"); font.pixelSize: 13; font.weight: Font.DemiBold }
        OriginalButton {
            objectName: "connectionCheckButton"
            Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
            text: appController.diagnosticRunning ? "正在检查连接…" : "一键检查连接"
            enabled: !appController.diagnosticRunning && !appController.collecting && !appController.collectionStarting
            toolTipText: "检查连接、采集组件和权限，帮助排查无法采集的问题"
            disabledReason: appController.diagnosticRunning ? "正在检查连接，请等待检查完成。" : "正在采集或连接采集器，请停止采集后再检查连接。"
            onClicked: { connectionDialog.open(); appController.runConnectionCheck() }
        }
        Item { Layout.preferredHeight: 16 }
        }
        AppDialog {
            id: connectionDialog; objectName: "connectionCheckDialog"
            parent: Overlay.overlay; anchors.centerIn: parent
            width: Math.min(660, parent.width - 32); height: Math.min(550, parent.height - 32)
            modal: true; title: "连接检查"; standardButtons: Dialog.NoButton
            
            
            footer: Rectangle {
                height: 58; color: "transparent"
                CaptureButton { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: 74; text: "关闭"; onClicked: connectionDialog.close() }
            }
            contentItem: ScrollView {
                id: diagnosticsScroll
                clip: true
                ColumnLayout {
                    width: diagnosticsScroll.availableWidth; spacing: 15
                    Text { Layout.fillWidth: true; text: appController.diagnosticRunning ? "正在读取设备和环境信息，请稍候…" : "本检查不会开启性能追踪或修改手机设置；具体指标是否可用仍需采集确认。"; color: Theme.color("#b9c3d9"); wrapMode: Text.WordWrap }
                    Repeater {
                        model: appController.diagnosticResults
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 5
                            Text { Layout.fillWidth: true; text: modelData.name + " · " + modelData.status; color: modelData.status === "通过" ? Theme.color("#77d9ac") : Theme.chartWarning; font.bold: true; wrapMode: Text.WordWrap }
                            Text { Layout.fillWidth: true; text: modelData.detail; color: Theme.color("#dde4f4"); wrapMode: Text.WrapAnywhere }
                            Text { Layout.fillWidth: true; text: modelData.action; color: Theme.color("#94b9ff"); wrapMode: Text.WordWrap; visible: text.length > 0 }
                        }
                    }
                }
            }
        }
    }
}
