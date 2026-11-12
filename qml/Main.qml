import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "."

ApplicationWindow {
    id: root
    width: 1280
    height: 820
    minimumWidth: 1080
    minimumHeight: 700
    visible: true
    color: Theme.header
    title: "PerfZY"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
    font.family: Theme.fontFamily
    property bool leftExpanded: true
    property bool screenshotVisible: appController.screenshots.length > 0 || appController.screenshotStatusText.length > 0
    property int profilingMode: settingsPreview ? 2 : historyPreview ? 1 : recordViewerController.viewingSession ? 3 : 0
    property int recordReturnPage: 1
    readonly property var uploader: typeof recordUploadBridge === "undefined" ? null : recordUploadBridge
    property bool logsVisible: typeof logsPreview !== "undefined" && logsPreview
    property int editingSceneIndex: -1
    readonly property bool markingScene: appController.markingScene
    readonly property real markingStart: appController.markingSceneStart
    property bool allowClose: false
    property bool exitAfterSave: false
    property string stoppedSessionId: ""
    property string stoppedSessionName: ""
    function openHistoryPage() {
        root.profilingMode = 1
        historyPage.refreshFromTop()
    }
    function showNotification(title,message,level) {
        notificationDialog.noticeTitle = title || "提示"
        notificationDialog.noticeMessage = message || ""
        notificationDialog.noticeLevel = level || "error"
        notificationDialog.open()
    }
    function formatDuration(seconds) {
        var value = Math.max(0, Math.floor(Number(seconds) || 0))
        var minutes = Math.floor(value / 60)
        var remain = value % 60
        return (minutes < 10 ? "0" : "") + minutes + ":" + (remain < 10 ? "0" : "") + remain
    }
    WindowTargetPanel { id: windowTargetPanel; parent: Overlay.overlay }
    SceneMarkControls {
        id: sceneMarks
        hostWindow: root
        modalBlocked: exitConfirmDialog.visible || sceneDialog.visible || notificationDialog.visible
            || stopNamingDialog.visible || windowTargetPanel.visible
            || metricPicker.visible || metricHealth.dialogVisible
            || settingsPage.connectionDialogVisible || statusPanel.logDialogVisible || uploadDialog.visible
        onStopApproved: function(exiting) {
            root.exitAfterSave = exiting
            appController.stopCollection()
        }
    }
    UploadRecordDialog {
        id: uploadDialog
        bridge: root.uploader
        onSettingsRequested: {
            settingsPage.section = "upload"
            root.profilingMode = 2
        }
    }
    function positionMetricPicker() {
        var anchor = metricPickerButton.mapToItem(Overlay.overlay, 0, 0)
        var above = anchor.y - 58
        var below = root.height - anchor.y - metricPickerButton.height - 22
        var openAbove = above >= 360 || above >= below
        metricPicker.width = Math.min(360, root.width - 32)
        metricPicker.height = Math.max(240, Math.min(620, openAbove ? above : below))
        var popupX = Math.max(12, Math.min(anchor.x + metricPickerButton.width - metricPicker.width, root.width - metricPicker.width - 12))
        var popupY = openAbove ? anchor.y - metricPicker.height - 10 : anchor.y + metricPickerButton.height + 10
        popupY = Math.max(48, Math.min(popupY, root.height - metricPicker.height - 12))
        metricPicker.x = popupX - anchor.x
        metricPicker.y = popupY - anchor.y
    }
    function openMetricPicker() {
        root.positionMetricPicker()
        metricPicker.open()
    }
    onClosing: function(close) {
        if (smokeTest || root.allowClose) {
            close.accepted = true
            return
        }
        close.accepted = false
        exitConfirmDialog.open()
    }
    Component.onCompleted: {
        if (metricPickerPreview) {
            root.profilingMode = 0
            Qt.callLater(root.openMetricPicker)
        }
        if (exitPreview) exitConfirmDialog.open()
    }

    WindowTitleBar { width: root.width; height: 40; hostWindow: root; onCaptureRequested: root.profilingMode = 0 }
    WindowResizeEdges { anchors.fill: parent; hostWindow: root; z: 200 }
    RowLayout {
        anchors.fill: parent
        anchors.topMargin: 40
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 56; Layout.fillHeight: true; color: Theme.header
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 8; spacing: 9
                NavButton { objectName: "capturePageButton"; glyph: "chart"; tip: "常规测试"; selected: root.profilingMode === 0; onClicked: root.profilingMode = 0 }
                NavButton { objectName: "historyPageButton"; glyph: "history"; tip: "采集记录"; selected: root.profilingMode === 1; onClicked: root.openHistoryPage() }
                NavButton { objectName: "recordViewerPageButton"; glyph: "view"; tip: "记录查看"; selected: root.profilingMode === 3; onClicked: root.profilingMode = 3 }
                NavButton { objectName: "htmlReportsPageButton"; glyph: "report"; tip: "HTML 报告"; selected: root.profilingMode === 4; onClicked: root.profilingMode = 4 }
                Item { Layout.fillHeight: true }
                NavButton { objectName: "logRailButton"; visible: root.profilingMode === 0; glyph: "log"; tip: "展开 / 收起日志"; selected: root.logsVisible && root.profilingMode === 0; onClicked: { if (root.profilingMode !== 0) { root.profilingMode = 0; root.logsVisible = true } else root.logsVisible = !root.logsVisible } }
                NavButton {
                    objectName: "collapseSidebarButton"
                    readonly property bool expanded: recordViewer.showingRecord ? recordViewer.sidebarExpanded : root.leftExpanded
                    visible: root.profilingMode === 0 || recordViewer.showingRecord
                    glyph: expanded ? "collapse" : "expand"
                    tip: expanded ? "收起设备面板" : "展开设备面板"
                    onClicked: {
                        if (recordViewer.showingRecord)
                            recordViewer.sidebarExpanded = !recordViewer.sidebarExpanded
                        else
                            root.leftExpanded = !root.leftExpanded
                    }
                }
                NavButton { objectName: "settingsPageButton"; glyph: "settings"; tip: "设置"; selected: root.profilingMode === 2; onClicked: root.profilingMode = 2 }
            }
        }

        RoundedWorkspace {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.rightMargin: 6; Layout.bottomMargin: 6
            RowLayout {
                anchors.fill: parent; spacing: 0
        Rectangle {
            id: leftPane
            objectName: "deviceSidebar"
            Layout.preferredWidth: root.leftExpanded ? 260 : 0
            Layout.minimumWidth: root.leftExpanded ? 260 : 0
            Layout.maximumWidth: root.leftExpanded ? 260 : 0
            Layout.fillHeight: true
            visible: root.leftExpanded && root.profilingMode === 0
            color: Theme.sidebar
            border.color: Theme.sidebar

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    id: leftPaneTop
                    objectName: "targetSelectorPane"
                    Layout.fillWidth: true
                    Layout.preferredHeight: targetControls.implicitHeight + 28
                    Layout.minimumHeight: Layout.preferredHeight
                    Layout.maximumHeight: Layout.preferredHeight
                    color: Theme.sidebar
                    ColumnLayout {
                        id: targetControls
                        anchors.fill: parent; anchors.margins: 14; spacing: 8
                        Text {
                            objectName: "capturePageHeading"
                            text: "常规测试"; color: Theme.text
                            font.pixelSize: 16; font.weight: Font.DemiBold
                            lineHeight: 24; lineHeightMode: Text.FixedHeight
                            Layout.fillWidth: true; Layout.bottomMargin: 6
                        }
                        SidebarSectionHeading { objectName: "deviceSectionHeading"; text: "设备与应用" }
                        Row {
                            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 8
                            CaptureComboBox {
                                id: deviceBox
                                objectName: "deviceSelector"
                                readonly property var selectedDeviceInfo: currentIndex >= 0 && currentIndex < appController.devices.length ? appController.devices[currentIndex] : null
                                width: 190; height: 34
                                model: appController.devices; textRole: "name"
                                enabled: !appController.collecting && !appController.busy
                                disabledReason: appController.collecting ? "采集中设备已锁定，请停止采集后再切换。" : appController.busyReason
                                currentIndex: appController.selectedDeviceIndex
                                placeholderText: appController.busy && appController.devices.length === 0 ? "正在刷新设备…" : "请选择设备"
                                onActivated: function(index) { appController.selectDevice(index) }
                                contentItem: RowLayout {
                                    spacing: 6
                                    ElidedLabel {
                                        objectName: "selectedDeviceName"
                                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                                        fullText: deviceBox.currentIndex < 0 ? deviceBox.placeholderText : deviceBox.displayText
                                        color: Theme.text; font: deviceBox.font
                                    }
                                    DeviceConnectionBadge {
                                        objectName: "selectedDeviceConnectionBadge"
                                        Layout.alignment: Qt.AlignVCenter
                                        visible: !!deviceBox.selectedDeviceInfo
                                        connectionType: deviceBox.selectedDeviceInfo ? deviceBox.selectedDeviceInfo.connection_type
                                            || (String(deviceBox.selectedDeviceInfo.id).indexOf("windows:") === 0 ? "local" : "unknown") : "unknown"
                                    }
                                }
                                delegate: ItemDelegate {
                                    required property var modelData
                                    required property int index
                                    objectName: "captureComboOption:deviceSelector:" + index
                                    width: ListView.view ? ListView.view.width : deviceBox.width
                                    height: 34
                                    leftPadding: 8; rightPadding: 8; topPadding: 0; bottomPadding: 0
                                    highlighted: deviceBox.highlightedIndex === index
                                    contentItem: RowLayout {
                                        spacing: 6
                                        ElidedLabel {
                                            objectName: "deviceName:" + index
                                            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                                            fullText: modelData.name || ""; color: Theme.text; font.pixelSize: 12
                                        }
                                        DeviceConnectionBadge {
                                            objectName: "deviceConnectionBadge:" + index
                                            Layout.alignment: Qt.AlignVCenter
                                            connectionType: modelData.connection_type || (String(modelData.id).indexOf("windows:") === 0 ? "local" : "unknown")
                                        }
                                    }
                                    background: Rectangle { radius: 6; color: parent.highlighted ? Theme.hover : Theme.control }
                                    ToolTip.visible: hovered
                                    ToolTip.text: modelData.name + "\n" + (modelData.connection_description || "连接方式未确认")
                                    ToolTip.delay: 450
                                }
                                toolTipText: selectedDeviceInfo ? selectedDeviceInfo.name + "\n" + (selectedDeviceInfo.connection_description || "连接方式未确认") : "请选择要采集的设备"
                            }
                            ToolButton {
                                id: refreshDeviceButton
                                width: 34; height: 34; enabled: !appController.collecting && !appController.busy
                                icon.source: "../assets/perfzy/icon/refresh.svg"
                                icon.width: 16; icon.height: 16; icon.color: Theme.muted; display: AbstractButton.IconOnly
                                onClicked: appController.refreshDevices()
                                background: Rectangle { color: refreshDeviceButton.hovered ? Theme.hover : Theme.control; border.color: Theme.border; radius: 8 }
                                ControlHint { targetControl: refreshDeviceButton; text: appController.collecting ? "采集中设备已锁定，请停止采集后刷新。" : appController.busyReason || "刷新设备列表" }
                            }
                        }
                        Row {
                            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 8
                            CaptureComboBox {
                                id: applicationBox
                                objectName: "applicationSelector"
                                width: 190; height: 34
                                visible: true
                                enabled: deviceBox.currentIndex >= 0 && !appController.collecting && !appController.busy
                                disabledReason: appController.collecting ? "采集中应用已锁定，请停止采集后再切换。" : appController.busyReason || "请先选择设备，再选择目标应用。"
                                toolTipText: currentIndex >= 0 && currentIndex < appController.targets.length
                                    ? appController.targets[currentIndex].name : "选择要采集性能数据的应用"
                                model: appController.targets; textRole: "name"
                                currentIndex: appController.selectedTargetIndex
                                placeholderText: deviceBox.currentIndex < 0
                                                 ? "请先选择设备"
                                                 : (appController.busy
                                                    ? "正在获取应用列表…"
                                                    : (appController.targets.length > 0
                                                       ? "请选择要采集的应用"
                                                       : "未发现可采集的应用"))
                                onActivated: function(index) { appController.selectTarget(index) }
                            }
                            ToolButton {
                                id: refreshApplicationButton
                                width: 34; height: 34
                                enabled: deviceBox.currentIndex >= 0 && !appController.collecting && !appController.busy
                                icon.source: "../assets/perfzy/icon/refresh.svg"
                                icon.width: 16; icon.height: 16; icon.color: Theme.muted; display: AbstractButton.IconOnly
                                onClicked: appController.refreshTargets()
                                background: Rectangle { color: refreshApplicationButton.hovered ? Theme.hover : Theme.control; border.color: Theme.border; radius: 8 }
                                ControlHint { targetControl: refreshApplicationButton; text: appController.collecting ? "采集中应用已锁定，请停止采集后刷新。" : appController.busyReason || (deviceBox.currentIndex < 0 ? "请先选择设备。" : "刷新应用列表") }
                            }
                        }
                        CaptureButton {
                            objectName: "selectWindowButton"
                            Layout.fillWidth: true; Layout.preferredHeight: 34
                            visible: appController.windowTargetsAvailable
                            text: "选择采集 / 截图窗口"
                            onClicked: windowTargetPanel.open()
                            ToolTip.visible: hovered
                            ToolTip.text: "按窗口精确选择 PID，或单独指定截图窗口"
                        }
                    }
                }

                Rectangle {
                    id: controlPane
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: Theme.sidebar
                    ColumnLayout {
                        anchors.fill: parent; spacing: 0
                        SidebarSectionHeading { objectName: "deviceInformationHeading"; Layout.leftMargin: 14; Layout.topMargin: 10; Layout.bottomMargin: 8; text: "设备信息" }
                            ScrollView {
                                id: devicePanel; objectName: "devicePanel"
                                Layout.fillWidth: true; Layout.fillHeight: true
                                clip: true; contentWidth: availableWidth
                                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                                ColumnLayout {
                                    width: devicePanel.availableWidth; spacing: 20
                                    DeviceInfoTable {
                                        objectName: "deviceInfoTable"
                                        Layout.fillWidth: true
                                        device: deviceBox.currentIndex >= 0 ? appController.devices[deviceBox.currentIndex] : ({})
                                    }
                                    TestNotesPanel {
                                        Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                                        Layout.bottomMargin: 14
                                    }
                                }
                            }
                        StatusPanel {
                            id: statusPanel
                            Layout.fillWidth: true; Layout.preferredHeight: 62
                            Layout.leftMargin: 14; Layout.rightMargin: 14; Layout.bottomMargin: 12
                            statusText: appController.statusText
                            summaryText: appController.statusSummary
                            compactText: appController.statusCompactText
                            logText: appController.runtimeLogText
                            healthText: appController.samplingHealthText
                            metricDetails: appController.collectionMetricDetails
                            collecting: appController.collecting
                            viewingSession: appController.viewingSession
                            warning: collecting && statusText !== "正在采集" && statusText !== "已暂停"
                            onCloseSession: { appController.closeSessionView(); chartScroll.resetViewport() }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: rightPane
            visible: root.profilingMode !== 2
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.color("#151928")

            ColumnLayout {
                anchors.fill: parent; spacing: 0
                ScreenshotStrip {
                    id: screenshotPane
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.screenshotVisible ? preferredStripHeight : 0
                    visible: Layout.preferredHeight > 0
                    shots: appController.screenshots
                    selectedIndex: appController.selectedScreenshotIndex
                    noticeText: appController.screenshotStatusText
                    showGap: appController.hasTimeLock && appController.selectedScreenshotIndex < 0
                }

                Rectangle {
                    id: sceneToolbar
                    objectName: "captureToolbar"
                    Layout.fillWidth: true; Layout.preferredHeight: 54
                    color: Theme.content
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 10; spacing: 6
                        CaptureButton {
                            objectName: "markSceneButton"
                            Layout.preferredWidth: 72; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8
                            text: appController.markingScene ? "结束标记" : "开始标记"
                            enabled: appController.collecting && !appController.paused
                            disabledReason: appController.paused ? "采集已暂停，请继续采集后再添加场景标记。" : "开始采集后可标记场景的开始和结束。"
                            onClicked: sceneMarks.mark()
                            toolTipText: appController.markingScene
                                ? "正在标记：" + appController.markingSceneName + "；点击结束后可修改名称和范围"
                                : "Ctrl+Alt+M：开始 / 结束场景标记"
                        }
                        CaptureComboBox {
                            id: sceneBox; objectName: "sceneSelector"
                            Layout.preferredWidth: 120; Layout.preferredHeight: 34
                            enabled: appController.scenes.length > 0
                            disabledReason: "完成场景标记后，可在这里选择要查看的场景。"
                            model: appController.scenes; textRole: "title"; placeholderText: "尚未添加场景"
                            displayText: currentIndex >= 0 && currentIndex < appController.scenes.length
                                ? appController.scenes[currentIndex].name || appController.scenes[currentIndex].title : ""
                            popupWidth: 280
                            toolTipText: currentIndex >= 0 && currentIndex < appController.scenes.length
                                ? appController.scenes[currentIndex].title : "选择已标记的场景"
                            onActivated: function(index) { appController.selectScene(index) }
                        }
                        CaptureButton {
                            objectName: "editSceneButton"
                            Layout.preferredWidth: 44; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8
                            text: "修改"; enabled: sceneBox.currentIndex >= 0
                            toolTipText: "修改所选场景的名称与时间范围"
                            disabledReason: "请先采集或打开包含场景标记的记录，并选择一个场景。"
                            onClicked: {
                                var scene = appController.scenes[sceneBox.currentIndex]
                                root.editingSceneIndex = sceneBox.currentIndex
                                sceneName.text = scene.name
                                sceneStart.text = Number(scene.start).toFixed(2)
                                sceneEnd.text = Number(scene.end).toFixed(2)
                                var colors = ["#4269d4", "#a45ac7", "#2e9d86", "#c98535", "#b74f66"]
                                sceneColor.currentIndex = Math.max(0, colors.indexOf(String(scene.color || "#4269d4")))
                                sceneDialog.open()
                            }
                        }
                        CaptureButton {
                            objectName: "clearTimeLockButton"
                            Layout.preferredWidth: 72; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8
                            text: "取消锁定"; enabled: appController.hasTimeLock
                            toolTipText: "已锁定 " + appController.lockedTime.toFixed(2) + " s；点击取消"
                            disabledReason: "当前未锁定时间；点击曲线可以锁定一个时刻。"
                            onClicked: appController.clearTimeLock()
                        }
                        CaptureButton {
                            objectName: "clearSelectionButton"
                            Layout.preferredWidth: 72; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8
                            text: "取消框选"; enabled: appController.hasSelection
                            toolTipText: "框选 " + (appController.selectionStartRatio * appController.duration).toFixed(2)
                                + "–" + (appController.selectionEndRatio * appController.duration).toFixed(2) + " s；点击取消"
                            disabledReason: "当前没有框选区间；可在曲线上拖动选择时间范围。"
                            onClicked: appController.clearSelection()
                        }
                        Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        CollectionHealthButton {
                            id: metricHealth
                            Layout.preferredWidth: 28; Layout.preferredHeight: 30
                            enabled: appController.collecting || appController.collectionStarting
                                     || appController.viewingSession || appController.chartDuration > 0
                            statuses: appController.collectionMetricStatus
                            details: appController.collectionMetricDetails
                        }
                        CaptureButton {
                            objectName: "generateReportButton"
                            Layout.preferredWidth: 76; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8
                            text: appController.reportGenerating ? "生成中…" : "生成报告"
                            enabled: appController.canGenerateReport
                            onClicked: appController.generateLocalReport()
                            toolTipText: "生成并打开本地曲线报告"
                            disabledReason: appController.collecting ? "停止采集并保存后才可生成报告。" : appController.busyReason || "请先完成一次采集并保存，或打开一条历史记录。"
                        }
                        CaptureButton {
                            objectName: "captureStartButton"
                            Layout.preferredWidth: 64; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8; tone: "primary"
                            text: appController.collectionStarting ? "连接中…" : (!appController.collecting ? "开始" : (appController.paused ? "继续" : "暂停"))
                            enabled: !appController.busy || appController.collecting
                            toolTipText: appController.collecting ? (appController.paused ? "继续当前采集" : "暂停当前采集") : "开始采集所选设备和应用"
                            disabledReason: appController.busyReason
                            onClicked: appController.startOrPauseCollection()
                        }
                        CaptureButton {
                            objectName: "captureStopButton"
                            Layout.preferredWidth: 52; Layout.preferredHeight: 34
                            leftPadding: 8; rightPadding: 8; tone: "danger"
                            text: "停止"; enabled: appController.collecting
                            toolTipText: "停止采集并保存本次数据"
                            disabledReason: appController.collectionStarting ? "采集器正在连接，请等待连接完成。" : "当前没有进行中的采集，请先点击“开始”。"
                            onClicked: sceneMarks.requestStop(false)
                        }
                    }
                }

                SplitView {
                    id: chartLogSplit
                    objectName: "chartLogSplit"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    orientation: Qt.Vertical
                    handle: Rectangle {
                        id: splitHandle
                        objectName: "logResizeHandle"
                        implicitHeight: 10
                        color: SplitHandle.pressed ? Theme.selected : SplitHandle.hovered ? Theme.hover : Theme.content
                        readonly property bool active: SplitHandle.hovered || SplitHandle.pressed
                        Rectangle {
                            anchors.centerIn: parent; width: 42; height: 3; radius: 1.5
                            color: splitHandle.active ? Theme.blue : Theme.border
                        }
                        ToolTip.visible: SplitHandle.hovered && !SplitHandle.pressed
                        ToolTip.text: "上下拖动调整日志高度"
                        ToolTip.delay: 450
                    }
                ScrollView {
                    id: chartScroll
                    objectName: "chartScroll"
                    readonly property real viewportY: contentItem && isFinite(Number(contentItem.contentY))
                                                      ? Number(contentItem.contentY) : 0
                    function resetViewport() {
                        if (contentItem) contentItem.contentY = 0
                    }
                    function scrollBy(delta) {
                        if (!contentItem) return
                        var start = Number(contentItem.originY) || 0
                        var end = start + Math.max(0, contentItem.contentHeight - contentItem.height)
                        contentItem.cancelFlick()
                        contentItem.contentY = Math.max(start, Math.min(end, contentItem.contentY - delta))
                    }
                    SplitView.fillHeight: true; SplitView.minimumHeight: 140
                    clip: true
                    background: Rectangle { color: Theme.content }
                    Column {
                        id: chartBox
                        width: chartScroll.availableWidth
                        spacing: 12; topPadding: 12; bottomPadding: 12
                        Repeater {
                            // Keep chart delegates alive behind the history
                            // overlay. Recreating them used to be the accidental
                            // action that made first-run curves finally paint.
                            model: appController.chartCatalog
                            OriginalChart {
                                required property var modelData
                                x: 12; width: chartBox.width - 24
                                chartKey: modelData.key
                                chartTitle: modelData.title
                                unitOverride: modelData.axisUnit
                                seriesNames: modelData.seriesNames
                                seriesColors: modelData.seriesColors
                                seriesEnabled: modelData.seriesEnabled
                                seriesSelectable: modelData.seriesSelectable
                                seriesStatus: modelData.seriesStatus
                                seriesReasons: modelData.seriesReasons
                                metricKeys: modelData.metricKeys
                                canMoveUp: modelData.canMoveUp
                                canMoveDown: modelData.canMoveDown
                                renderActive: root.profilingMode === 0
                                              && y + height >= chartScroll.viewportY - height
                                              && y <= chartScroll.viewportY + chartScroll.height + height
                                dataSource: appController.chartData
                                activeMarking: root.markingScene
                                markingStart: root.markingStart
                                onPageScrollRequested: function(delta) { chartScroll.scrollBy(delta) }
                            }
                        }
                    }
                }
                LogDrawer {
                    id: logDrawer
                    objectName: "logDrawer"
                    SplitView.minimumHeight: 128
                    SplitView.preferredHeight: 210
                    visible: root.logsVisible
                    onCloseRequested: root.logsVisible = false
                    onDetailsRequested: statusPanel.openDetails()
                }

                }
            }
            MetricsButton {
                id: metricPickerButton
                objectName: "metricPickerButton"
                z: 30; width: 36; height: 36
                visible: root.profilingMode === 0
                anchors.right: parent.right; anchors.rightMargin: 18
                anchors.bottom: parent.bottom; anchors.bottomMargin: (root.logsVisible ? logDrawer.height + 10 : 0) + 16
                expanded: metricPicker.visible
                onClicked: {
                    if (metricPicker.visible) metricPicker.close()
                    else root.openMetricPicker()
                }
            }

            RecordViewerPage {
                id: recordViewer; anchors.fill: parent; visible: root.profilingMode === 3; z: 50
                returnPageLabel: root.recordReturnPage === 4 ? "HTML 报告" : "采集记录"
                onBackRequested: { recordViewerController.closeSessionView(); root.profilingMode = root.recordReturnPage }
                onRecordsRequested: root.openHistoryPage()
            }

            HtmlReportPage {
                anchors.fill: parent
                visible: root.profilingMode === 4
                z: 50
                onSessionsRequested: root.openHistoryPage()
            }

            SessionHistoryPanel {
                id: historyPage
                anchors.fill: parent
                visible: root.profilingMode === 1
                z: 50
                uploader: root.uploader
                onUploadRequested: function(recordId) { uploadDialog.openFor(recordId) }
            }
        }
        SettingsPage { id: settingsPage; uploader: root.uploader; Layout.fillWidth: true; Layout.fillHeight: true; visible: root.profilingMode === 2 }
            }
        }
    }

    MetricPicker { id: metricPicker; parent: metricPickerButton }
    Connections {
        target: root
        function onWidthChanged() { if (metricPicker.visible) Qt.callLater(root.positionMetricPicker) }
        function onHeightChanged() { if (metricPicker.visible) Qt.callLater(root.positionMetricPicker) }
        function onProfilingModeChanged() { if (root.profilingMode !== 0) metricPicker.close() }
    }

    Connections {
        target: recordViewerController
        function onSessionOpened() {
            if (root.profilingMode !== 3)
                root.recordReturnPage = root.profilingMode === 4 ? 4 : 1
            root.profilingMode = 3
            recordViewer.resetViewport()
        }
        function onNotificationRequested(title,message,level) { root.showNotification(title,message,level) }
    }
    Connections {
        target: appController
        function onScreenshotsChanged() {
            if (appController.screenshots.length > 0) {
                screenshotPane.positionViewAtIndex(appController.selectedScreenshotIndex, ListView.Contain)
            }
        }
        function onTimeLockChanged() {
            if (appController.selectedScreenshotIndex >= 0)
                screenshotPane.positionViewAtIndex(appController.selectedScreenshotIndex, ListView.Center)
        }
        function onSessionOpened() {
            root.profilingMode = 0
            chartScroll.resetViewport()
        }
        function onCaptureConfigurationApplied() {
            root.profilingMode = 0
            chartScroll.resetViewport()
        }
        function onSceneMarkStopRequested() { sceneMarks.requestStop(false) }
        function onSessionSaved(result) {
            if (root.exitAfterSave) {
                root.exitAfterSave = false
                if (result && result.ok) {
                    root.allowClose = true
                    root.close()
                }
            }
        }
        function onSessionNamingRequested(sessionId, sessionName) {
            if (!root.exitAfterSave) {
                root.stoppedSessionId = sessionId || ""
                root.stoppedSessionName = sessionName || ""
                stoppedSessionNameField.text = root.stoppedSessionName
                stopNamingDialog.open()
            }
        }
        function onNotificationRequested(title, message, level) {
            root.showNotification(title,message,level)
        }
    }

    AppDialog {
        id: stopNamingDialog
        objectName: "stopNamingDialog"
        modal: true; anchors.centerIn: parent; width: 490; height: 280
        title: "保存本次采集"; standardButtons: Dialog.NoButton
        closePolicy: Popup.CloseOnEscape
        onOpened: { stoppedSessionNameField.forceActiveFocus(); stoppedSessionNameField.selectAll() }
        
        
        contentItem: ColumnLayout {
            spacing: 10
            Text { Layout.fillWidth: true; text: "该名称会显示在采集记录中，并作为生成报告的文件名和报告标题。"; color: Theme.color("#9da9c5"); font.pixelSize: 12; wrapMode: Text.WordWrap }
            Text { text: "采集名称"; color: Theme.color("#c8d1e5"); font.pixelSize: 12 }
            CaptureTextField {
                id: stoppedSessionNameField; Layout.fillWidth: true; Layout.preferredHeight: 34
                horizontalAlignment: Text.AlignLeft; maximumLength: 80
                placeholderText: "例如：8月24日城市场景回归"
                onAccepted: saveStoppedSessionNameButton.clicked()
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                CaptureButton { Layout.preferredWidth: 72; text: "稍后修改"; onClicked: stopNamingDialog.close() }
                Item { Layout.fillWidth: true }
                CaptureButton {
                    id: saveStoppedSessionNameButton; Layout.preferredWidth: 82; text: "保存名称"
                    onClicked: {
                        if (appController.renameSession(root.stoppedSessionId, stoppedSessionNameField.text))
                            stopNamingDialog.close()
                    }
                }
                CaptureButton { tone: "primary";
                    Layout.preferredWidth: 132
                    text: appController.reportGenerating ? "生成中…" : "保存并生成报告"
                    enabled: !appController.reportGenerating
                    disabledReason: "正在生成报告，请等待生成完成。"
                    accentColor: Theme.primaryFill
                    onClicked: {
                        if (appController.renameSessionAndGenerateReport(root.stoppedSessionId, stoppedSessionNameField.text))
                            stopNamingDialog.close()
                    }
                }
            }
        }
    }

    AppDialog {
        id: exitConfirmDialog
        objectName: "exitConfirmDialog"
        modal: true; anchors.centerIn: parent; width: 440; height: appController.collecting ? 330 : 180
        title: "确认退出"; standardButtons: Dialog.NoButton
        
        
        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: appController.collecting
                      ? "当前正在采集数据，退出前是否保存本次采集会话？"
                      : "确定要退出 PerfZY 吗？"
                color: Theme.color("#aeb8d0"); font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            Rectangle {
                visible: appController.collecting
                Layout.fillWidth: true; Layout.preferredHeight: 110
                color: Theme.color("#191e2f"); border.color: Theme.color("#3a4258"); radius: 3
                GridLayout {
                    anchors.fill: parent; anchors.margins: 10; columns: 2; rowSpacing: 2
                    Text { text: "设备"; color: Theme.color("#77839e"); font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; text: deviceBox.currentIndex >= 0 ? appController.devices[deviceBox.currentIndex].name : "—"; color: Theme.color("#e8edfb"); font.pixelSize: 11; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { text: "应用"; color: Theme.color("#77839e"); font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; text: applicationBox.currentIndex >= 0 ? appController.targets[applicationBox.currentIndex].name : "—"; color: Theme.color("#e8edfb"); font.pixelSize: 11; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { text: "已采集时长"; color: Theme.color("#77839e"); font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; text: root.formatDuration(appController.duration); color: Theme.color("#e8edfb"); font.pixelSize: 11; font.weight: Font.DemiBold }
                    Text { text: "截图数量"; color: Theme.color("#77839e"); font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; text: appController.screenshots.length + " 张"; color: Theme.color("#e8edfb"); font.pixelSize: 11; font.weight: Font.DemiBold }
                }
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton {
                    visible: appController.collecting; Layout.preferredWidth: 94; tone: "danger"; text: "不保存退出"
                    onClicked: {
                        exitConfirmDialog.close()
                        appController.discardCollection()
                        root.allowClose = true
                        root.close()
                    }
                }
                CaptureButton { Layout.preferredWidth: 72; text: "取消"; onClicked: exitConfirmDialog.close() }
                CaptureButton { tone: "primary";
                    Layout.preferredWidth: 88
                    text: appController.collecting ? "保存退出" : "确认退出"; accentColor: Theme.primaryFill
                    onClicked: {
                        exitConfirmDialog.close()
                        if (appController.collecting) {
                            sceneMarks.requestStop(true)
                        } else {
                            root.allowClose = true
                            root.close()
                        }
                    }
                }
            }
        }
    }

    AppDialog {
        id: notificationDialog; objectName: "notificationDialog"
        property string noticeTitle: "提示"
        property string noticeMessage: ""
        property string noticeLevel: "error"
        modal: true; anchors.centerIn: parent; width: 480; height: Math.min(root.height - 80,Math.max(180,noticeText.contentHeight+138))
        title: noticeTitle; standardButtons: Dialog.NoButton
        
        
        contentItem: ColumnLayout {
            spacing: 18
            ScrollView {
                id: noticeScroll
                objectName: "notificationMessageScroll"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                TextArea {
                    id: noticeText; objectName: "notificationMessage"
                    width: noticeScroll.availableWidth
                    text: notificationDialog.noticeMessage; textFormat: TextEdit.PlainText
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere
                    color: Theme.muted; font.pixelSize: 12; padding: 0
                    selectionColor: Theme.primaryFill; selectedTextColor: "white"
                    background: Item {}
                }
            }
            CaptureButton {
                objectName: "notificationConfirmButton"
                Layout.alignment: Qt.AlignRight; Layout.preferredWidth: 80; Layout.preferredHeight: 34
                text: "知道了"; tone: "primary"; onClicked: notificationDialog.close()
            }
        }
    }

    AppDialog {
        id: sceneDialog; objectName: "sceneDialog"; modal: true; anchors.centerIn: parent; width: 440; height: 350
        property string validationError: ""
        onOpened: validationError = ""
        title: root.editingSceneIndex < 0 ? "添加场景时间段" : "编辑场景时间段"
        standardButtons: Dialog.NoButton
        
        
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout { Text { text: "场景名称"; color: Theme.color("#bcc6e2"); Layout.preferredWidth: 70 } CaptureTextField { id: sceneName; Layout.fillWidth: true; horizontalAlignment: Text.AlignLeft } }
            RowLayout { Text { text: "开始时间"; color: Theme.color("#bcc6e2"); Layout.preferredWidth: 70 } CaptureTextField { id: sceneStart; Layout.fillWidth: true } Text { text: "秒"; color: Theme.color("#8892ad") } }
            RowLayout { Text { text: "结束时间"; color: Theme.color("#bcc6e2"); Layout.preferredWidth: 70 } CaptureTextField { id: sceneEnd; Layout.fillWidth: true } Text { text: "秒"; color: Theme.color("#8892ad") } }
            RowLayout {
                Text { text: "标记颜色"; color: Theme.color("#bcc6e2"); Layout.preferredWidth: 70 }
                OriginalComboBox {
                    id: sceneColor; Layout.fillWidth: true
                    model: ["蓝色", "紫色", "青色", "橙色", "红色"]
                    currentIndex: 0
                }
                Rectangle {
                    Layout.preferredWidth: 22; Layout.preferredHeight: 22; radius: 3
                    color: [Theme.color("#4269d4"), Theme.color("#a45ac7"), Theme.color("#2e9d86"), Theme.color("#c98535"), Theme.color("#b74f66")][sceneColor.currentIndex]
                    border.color: Theme.color("#8792ac")
                }
            }
            Text { Layout.fillWidth: true; text: sceneDialog.validationError; visible: text.length > 0; wrapMode: Text.WordWrap; color: Theme.chartWarning; font.pixelSize: 12 }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                CaptureButton { Layout.preferredWidth: 72; text: "取消"; onClicked: sceneDialog.close() }
                CaptureButton {
                    Layout.preferredWidth: 72; text: "保存"
                    onClicked: {
                        var start = Number(sceneStart.text), end = Number(sceneEnd.text)
                        if (sceneStart.text.trim() === "" || sceneEnd.text.trim() === "" || !isFinite(start) || !isFinite(end) || start < 0 || end <= start || end > appController.duration + 0.01) {
                            sceneDialog.validationError = "请输入有效秒数：0 ≤ 开始 < 结束 ≤ 采集时长（" + appController.duration.toFixed(2) + " 秒）。"
                        } else {
                            if (root.editingSceneIndex < 0) appController.addScene(sceneName.text, start, end)
                            else appController.updateSceneDetails(root.editingSceneIndex, sceneName.text, start, end,
                                ["#4269d4", "#a45ac7", "#2e9d86", "#c98535", "#b74f66"][sceneColor.currentIndex])
                            appController.rememberScenePreset(sceneName.text)
                            sceneDialog.close()
                        }
                    }
                }
            }
        }
    }
}
