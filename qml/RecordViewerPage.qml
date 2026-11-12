import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: page
    objectName: "recordViewerPage"
    property var controller: recordViewerController
    property string returnPageLabel: "采集记录"
    property bool sidebarExpanded: true
    readonly property bool showingRecord: visible && controller.viewingSession
    readonly property var info: controller.recordInfo
    readonly property real selectedStart: controller.hasSelection ? controller.selectionStartRatio * controller.duration : 0
    readonly property real selectedEnd: controller.hasSelection ? controller.selectionEndRatio * controller.duration : controller.duration
    signal backRequested()
    signal recordsRequested()
    color: Theme.content
    function resetViewport() { if (charts.contentItem) charts.contentItem.contentY = 0 }
    function scrollBy(delta) {
        var view = charts.contentItem
        if (!view) return
        var first = Number(view.originY) || 0
        view.cancelFlick()
        view.contentY = Math.max(first, Math.min(first + Math.max(0, view.contentHeight-view.height),view.contentY-delta))
    }
    function formatDuration(value) {
        var seconds = Math.max(0, Math.floor(value))
        return String(Math.floor(seconds/60)).padStart(2,"0") + ":" + String(seconds%60).padStart(2,"0")
    }
    function rangeLabel(first,last) { return formatDuration(first) + " — " + formatDuration(last) + "  ·  " + (last-first).toFixed(2) + " 秒" }
    function showSceneEditor(index) {
        var scene = index >= 0 ? controller.scenes[index] : null
        sceneEditor.sceneId = scene ? scene.id : null
        sceneName.text = scene ? scene.name : "场景 " + (controller.scenes.length+1)
        sceneStart.text = (scene ? scene.start : selectedStart).toFixed(2)
        sceneEnd.text = (scene ? scene.end : selectedEnd).toFixed(2)
        sceneEditor.chosenColor = scene ? scene.color : "#4269d4"
        sceneEditor.validationError = ""
        sceneEditor.open()
        sceneName.forceActiveFocus()
        sceneName.selectAll()
    }
    function openSelectionMenu(x,y) {
        selectionMenu.x = Math.max(4,Math.min(page.width-selectionMenu.width-4,x))
        selectionMenu.y = Math.max(4,Math.min(page.height-selectionMenu.implicitHeight-4,y))
        selectionMenu.open()
    }
    function confirmDelete(data, currentSceneOnly) {
        deleteDialog.compactOnly = false
        deleteDialog.createCopy = controller.editAsCopy
        deleteDialog.dataRemoval = data
        deleteDialog.first = selectedStart
        deleteDialog.last = selectedEnd
        var chosen = currentSceneOnly && scenes.currentIndex >= 0 ? [controller.scenes[scenes.currentIndex]] : controller.selectionScenes
        deleteDialog.sceneIds = chosen.map(function(scene){return scene.id})
        deleteDialog.sceneNames = chosen.map(function(scene){return scene.name}).join("、")
        deleteDialog.currentSceneOnly = !!currentSceneOnly
        deleteDialog.open()
    }
    function confirmCompact() {
        deleteDialog.createCopy = controller.editAsCopy
        deleteDialog.dataRemoval = true
        deleteDialog.compactOnly = true
        deleteDialog.currentSceneOnly = false
        deleteDialog.open()
    }
    onShowingRecordChanged: if (!showingRecord) {
        metricPicker.close()
        selectionMenu.close()
        sceneActions.close()
        sceneEditor.close()
        deleteDialog.close()
        undoDialog.close()
    }

    Item {
        objectName: "recordViewerEmptyState"
        anchors.fill: parent
        visible: !page.controller.viewingSession

        Text {
            anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 16
            text: "记录查看"; color: Theme.text
            font.pixelSize: 20; font.weight: Font.DemiBold
        }
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(400, parent.width - 32)
            spacing: 10
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 48; Layout.preferredHeight: 48
                radius: 13; color: Theme.sidebar
                IconGlyph { anchors.centerIn: parent; width: 24; height: 24; name: "view"; tint: Theme.muted }
            }
            Text {
                Layout.alignment: Qt.AlignHCenter
                text: "还没有选择采集记录"; color: Theme.text
                font.pixelSize: 16; font.weight: Font.Medium
            }
            Text {
                Layout.fillWidth: true
                text: "在「采集记录」中点「查看」，即可在这里回看曲线、截图和场景。"
                color: Theme.muted; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            }
            CaptureButton {
                objectName: "recordViewerEmptyAction"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 132; Layout.topMargin: 4
                text: "前往采集记录"; tone: "primary"
                onClicked: page.recordsRequested()
            }
        }
    }

    Rectangle {
        id: recordSidebar
        objectName: "recordViewerSidebar"
        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 260
        visible: page.showingRecord && page.sidebarExpanded
        color: Theme.sidebar

        ColumnLayout {
            anchors.fill: parent
            spacing: 0
            Text {
                Layout.fillWidth: true; Layout.margins: 14
                text: "记录查看"; color: Theme.text
                font.pixelSize: 16; font.weight: Font.DemiBold
                lineHeight: 24; lineHeightMode: Text.FixedHeight
            }
            ScrollView {
                id: recordDevicePanel
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: recordDevicePanel.availableWidth
                    spacing: 0
                    SidebarSectionHeading {
                        Layout.leftMargin: 14
                        text: "采集应用"
                    }
                    Rectangle {
                        id: applicationCard
                        Layout.fillWidth: true
                        Layout.leftMargin: 14; Layout.rightMargin: 14; Layout.topMargin: 8
                        Layout.preferredHeight: applicationLabels.implicitHeight + 18
                        radius: 8; color: Theme.control; border.color: Theme.border
                        ColumnLayout {
                            id: applicationLabels
                            anchors.fill: parent; anchors.margins: 9
                            spacing: 3
                            ElidedLabel {
                                id: applicationTitle
                                objectName: "recordViewerApplicationName"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                fullText: page.info.applicationName
                                color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium
                            }
                            ElidedLabel {
                                id: applicationIdentifier
                                objectName: "recordViewerApplicationIdentifier"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                fullText: page.info.applicationIdentifier
                                visible: fullText.length > 0
                                color: Theme.muted; font.pixelSize: 11
                            }
                        }
                        ControlHint {
                            targetControl: applicationCard
                            text: applicationTitle.contentElided || (applicationIdentifier.visible && applicationIdentifier.contentElided)
                                ? [applicationTitle.fullText, applicationIdentifier.fullText].filter(Boolean).join("\n") : ""
                        }
                    }
                    SidebarSectionHeading {
                        Layout.leftMargin: 14; Layout.topMargin: 20; Layout.bottomMargin: 8
                        text: "设备信息"
                    }
                    DeviceInfoTable {
                        id: recordDeviceInfo
                        objectName: "recordViewerDeviceInfo"
                        Layout.fillWidth: true; Layout.bottomMargin: 14
                        device: page.info.deviceDetails
                        visible: rows.length > 0
                    }
                    Text {
                        objectName: "recordViewerMissingDeviceInfo"
                        Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 14
                        visible: recordDeviceInfo.rows.length === 0
                        text: "这条记录没有保存设备信息"
                        color: Theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
                    }
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16
        anchors.leftMargin: (recordSidebar.visible ? recordSidebar.width : 0) + 16
        spacing: 12
        visible: page.showingRecord
        RowLayout {
            objectName: "recordViewerHeader"
            Layout.fillWidth: true; spacing: 12
            HistoryActionButton {
                objectName: "backToHistoryButton"
                Layout.preferredWidth: 94; Layout.preferredHeight: 34
                text: page.returnPageLabel; glyph: "collapse"; toolTipText: "返回" + page.returnPageLabel
                onClicked: page.backRequested()
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 5
                ElidedLabel {
                    objectName: "recordViewerTitle"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    fullText: page.info.name || "采集记录"; color: Theme.text
                    font.pixelSize: 18; font.weight: Font.DemiBold
                }
                ElidedLabel {
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    fullText: [recordSidebar.visible ? "" : page.info.device,
                        recordSidebar.visible ? "" : page.info.applicationName, page.info.startedAt,
                        page.formatDuration(page.controller.duration)].filter(Boolean).join("  ·  ")
                    color: Theme.muted; font.pixelSize: 12
                }
            }
            Rectangle {
                Layout.preferredWidth: 68; Layout.preferredHeight: 25
                radius: 6; color: Theme.paneAlt
                Text { anchors.centerIn: parent; text: page.info.edited ? "已剪辑" : "记录查看"; color: Theme.muted; font.pixelSize: 11 }
            }
            HistoryActionButton {
                objectName: "recordViewerUndoOverwriteButton"
                Layout.preferredWidth: 100; Layout.preferredHeight: 34
                visible: page.controller.hasUndoBackup
                enabled: !page.controller.editing && !appController.busy
                text: "撤销剪辑"; toolTipText: "撤销上次覆盖剪辑"
                onClicked: undoDialog.open()
            }
            CaptureButton {
                objectName: "recordViewerReportButton"
                Layout.preferredWidth: 88; Layout.preferredHeight: 34
                text: appController.reportGenerating ? "生成中…" : "生成报告"
                enabled: page.controller.viewingSession && !appController.collecting && !appController.busy
                disabledReason: appController.collecting ? "请停止采集并保存后再生成报告。" : appController.busyReason
                onClicked: page.controller.generateLocalReport()
            }
        }
        ScreenshotStrip {
            id: shots
            objectName: "recordViewerScreenshots"
            Layout.fillWidth: true
            Layout.preferredHeight: preferredStripHeight
            visible: preferredStripHeight > 0
            shots: page.controller.screenshots
            selectedIndex: page.controller.selectedScreenshotIndex
            noticeText: page.controller.screenshotStatusText
            showGap: page.controller.hasTimeLock && page.controller.selectedScreenshotIndex < 0
        }
        RowLayout {
            objectName: "recordViewerToolbar"
            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 8
            CaptureComboBox {
                id: scenes; objectName: "recordViewerSceneSelector"
                Layout.fillWidth: true; Layout.minimumWidth: 122; Layout.maximumWidth: 190
                Layout.preferredWidth: 190; Layout.preferredHeight: 34
                enabled: page.controller.scenes.length > 0
                model: page.controller.scenes; textRole: "title"; placeholderText: "尚未添加场景"
                displayText: currentIndex >= 0 && currentIndex < page.controller.scenes.length ? page.controller.scenes[currentIndex].name : ""
                popupWidth: 280
                onActivated: function(index) { page.controller.selectScene(index) }
            }
            CaptureButton {
                objectName: "recordViewerAddSceneButton"
                Layout.preferredWidth: 80; Layout.preferredHeight: 34
                text: "添加场景"; enabled: page.controller.viewingSession && !page.controller.editing
                onClicked: page.showSceneEditor(-1)
            }
            CaptureButton {
                objectName: "recordViewerEditSceneButton"
                Layout.preferredWidth: 52; Layout.preferredHeight: 34
                text: "修改"; enabled: scenes.currentIndex >= 0 && page.controller.scenes.length > 0 && !page.controller.editing
                onClicked: page.showSceneEditor(scenes.currentIndex)
            }
            HistoryActionButton {
                id: sceneMore; objectName: "recordViewerSceneMoreButton"
                Layout.preferredWidth: 30; Layout.preferredHeight: 34
                glyph: "more"; toolTipText: "场景与时间段操作"; enabled: !page.controller.editing
                onClicked: sceneActions.popup(sceneMore,0,sceneMore.height+5)
            }
            CaptureButton {
                objectName: "recordViewerCompactTimeButton"
                visible: page.controller.excludedIntervals.length > 0
                Layout.preferredWidth: 80; Layout.preferredHeight: 34
                text: "压缩空白"; enabled: !page.controller.editing
                toolTipText: "移除旧副本中的空白时间，前后拼接后另存为新记录。"
                onClicked: page.confirmCompact()
            }
            CaptureButton {
                objectName: "recordViewerClearLockButton"
                Layout.preferredWidth: 80; Layout.preferredHeight: 34
                text: "取消锁定"; enabled: page.controller.hasTimeLock
                onClicked: page.controller.clearTimeLock()
            }
            CaptureButton {
                objectName: "recordViewerClearSelectionButton"
                Layout.preferredWidth: 80; Layout.preferredHeight: 34
                text: "取消框选"; enabled: page.controller.hasSelection
                onClicked: page.controller.clearSelection()
            }
            ElidedLabel {
                Layout.fillWidth: true; Layout.minimumWidth: 0
                fullText: page.controller.editing ? page.controller.editNotice
                    : page.controller.hasSelection ? page.rangeLabel(page.selectedStart,page.selectedEnd) + " · 右键编辑"
                    : page.controller.editNotice || "框选曲线后右键编辑"
                color: Theme.muted; font.pixelSize: 11
            }
            CollectionHealthButton {
                objectName: "recordViewerHealthButton"
                Layout.preferredWidth: 28; Layout.preferredHeight: 30
                enabled: page.controller.viewingSession
                statuses: page.controller.collectionMetricStatus; details: page.controller.collectionMetricDetails
            }
            MetricsButton {
                id: metrics; objectName: "recordViewerMetricsButton"
                Layout.preferredWidth: 34; Layout.preferredHeight: 34
                expanded: metricPicker.visible
                onClicked: {
                    if (metricPicker.visible) metricPicker.close()
                    else {
                        var point = metrics.mapToItem(page,0,metrics.height)
                        metricPicker.height = Math.max(240,Math.min(600,page.height-point.y-18))
                        metricPicker.x = metrics.width-metricPicker.width
                        metricPicker.y = metrics.height+6
                        metricPicker.open()
                    }
                }
            }
        }
        ScrollView {
            id: charts; objectName: "recordViewerChartScroll"
            readonly property real viewportY: contentItem ? Number(contentItem.contentY) || 0 : 0
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            Column {
                id: chartColumn
                width: charts.availableWidth; spacing: 12; bottomPadding: 8
                Repeater {
                    model: page.controller.viewingSession ? page.controller.chartCatalog : []
                    OriginalChart {
                        required property var modelData
                        objectName: "recordChart:" + modelData.key
                        width: chartColumn.width
                        controller: page.controller
                        chartKey: modelData.key; chartTitle: modelData.title; unitOverride: modelData.axisUnit
                        seriesNames: modelData.seriesNames; seriesColors: modelData.seriesColors
                        seriesEnabled: modelData.seriesEnabled; seriesSelectable: modelData.seriesSelectable
                        seriesStatus: modelData.seriesStatus; seriesReasons: modelData.seriesReasons; metricKeys: modelData.metricKeys
                        canMoveUp: modelData.canMoveUp; canMoveDown: modelData.canMoveDown
                        dataSource: page.controller.chartData
                        selectionMenuEnabled: !page.controller.editing
                        onSelectionMenuRequested: function(x,y) {
                            var point = mapToItem(page,x,y)
                            page.openSelectionMenu(point.x,point.y)
                        }
                        renderActive: page.showingRecord && y+height >= charts.viewportY-height && y <= charts.viewportY+charts.height+height
                        onPageScrollRequested: function(delta) { page.scrollBy(delta) }
                    }
                }
            }
        }
    }
    MetricPicker { id: metricPicker; objectName: "recordViewerMetricPicker"; parent: metrics; controller: page.controller; viewOnly: true }
    HistoryMenu {
        id: selectionMenu; objectName: "recordViewerSelectionMenu"; parent: page; width: 246
        HistoryMenuItem { text: page.controller.hasSelection ? page.rangeLabel(page.selectedStart,page.selectedEnd) : "请先拖动框选时间段"; enabled: false }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        HistoryMenuItem {
            objectName: "recordViewerContextAddScene"; text: "添加场景…"; glyph: "bookmark"
            enabled: page.controller.hasSelection && !page.controller.editing
            disabledReason: "先在曲线上拖动框选一段时间。"
            onTriggered: page.showSceneEditor(-1)
        }
        HistoryMenuItem {
            objectName: "recordViewerContextEditScene"; text: "修改当前场景…"; glyph: "rename"
            enabled: scenes.currentIndex >= 0 && page.controller.scenes.length > 0 && !page.controller.editing
            onTriggered: page.showSceneEditor(scenes.currentIndex)
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        HistoryMenuItem {
            objectName: "recordViewerContextDeleteData"; text: "删除所选时间段数据…"; glyph: "trash"; destructive: true
            enabled: page.controller.hasSelection && !page.controller.editing
            disabledReason: "先框选需要移除的时间段。"
            toolTipText: "移除数据与截图，前后拼接后生成副本，保留原记录。"
            onTriggered: page.confirmDelete(true,false)
        }
        HistoryMenuItem {
            objectName: "recordViewerContextDeleteScenes"; text: "删除范围内场景标记…"; glyph: "bookmark"; destructive: true
            enabled: page.controller.selectionScenes.length > 0 && !page.controller.editing
            disabledReason: "框选范围内没有重叠的场景标记。"
            toolTipText: "仅删除与框选范围重叠的场景，保留数据和截图。"
            onTriggered: page.confirmDelete(false,false)
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        HistoryMenuItem { text: "取消框选"; enabled: page.controller.hasSelection; onTriggered: page.controller.clearSelection() }
    }
    HistoryMenu {
        id: sceneActions; objectName: "recordViewerSceneActions"; width: 234
        HistoryMenuItem {
            text: "删除当前场景标记…"; glyph: "trash"; destructive: true
            enabled: scenes.currentIndex >= 0 && page.controller.scenes.length > 0 && !page.controller.editing
            onTriggered: page.confirmDelete(false,true)
        }
        HistoryMenuItem {
            text: "删除所选时间段数据…"; glyph: "trash"; destructive: true
            enabled: page.controller.hasSelection && !page.controller.editing
            disabledReason: "先在曲线上框选时间段。"
            onTriggered: page.confirmDelete(true,false)
        }
    }
    Connections {
        target: page.controller
        function onTimeLockChanged() { if (page.controller.selectedScreenshotIndex >= 0) shots.positionViewAtIndex(page.controller.selectedScreenshotIndex,ListView.Center) }
        function onSessionOpened() { selectionMenu.close(); sceneActions.close(); sceneEditor.close(); deleteDialog.close(); undoDialog.close(); metricPicker.close() }
    }
    AppDialog {
        id: sceneEditor; objectName: "recordViewerSceneDialog"
        title: sceneId === null ? "添加场景" : "编辑场景"; width: 440; height: 356
        subtitle: "标记一段测试场景，方便回看和对比。"
        property var sceneId: null
        property string chosenColor: "#4269d4"
        property string validationError: ""
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: "场景名称"; color: Theme.muted; font.pixelSize: 12 }
            CaptureTextField { id: sceneName; objectName: "recordViewerSceneName"; Layout.fillWidth: true; horizontalAlignment: Text.AlignLeft; maximumLength: 80 }
            RowLayout {
                Layout.fillWidth: true; spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    Text { text: "开始时间（秒）"; color: Theme.muted; font.pixelSize: 12 }
                    CaptureTextField { id: sceneStart; objectName: "recordViewerSceneStart"; Layout.fillWidth: true }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Text { text: "结束时间（秒）"; color: Theme.muted; font.pixelSize: 12 }
                    CaptureTextField { id: sceneEnd; objectName: "recordViewerSceneEnd"; Layout.fillWidth: true }
                }
            }
            RowLayout {
                spacing: 10
                Text { text: "标记颜色"; color: Theme.muted; font.pixelSize: 12 }
                Repeater {
                    model: ["#4269d4","#a45ac7","#2e9d86","#c98535","#b74f66"]
                    Button {
                        required property string modelData
                        objectName: "recordViewerSceneColor:" + modelData
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                        Accessible.name: "场景颜色 " + modelData
                        background: Rectangle { radius: 13; color: "transparent"; border.color: sceneEditor.chosenColor === modelData ? Theme.blue : "transparent"; border.width: 2
                            Rectangle { anchors.centerIn: parent; width: 18; height: 18; radius: 9; color: modelData }
                        }
                        onClicked: sceneEditor.chosenColor = modelData
                    }
                }
            }
            Text { Layout.fillWidth: true; visible: text.length > 0; text: sceneEditor.validationError; color: Theme.chartWarning; wrapMode: Text.WordWrap; font.pixelSize: 12 }
            Item { Layout.fillHeight: true }
            Text { text: "保存到当前记录，已生成的 HTML 需重新生成。"; color: Theme.muted; font.pixelSize: 11 }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { implicitWidth: 72; text: "取消"; onClicked: sceneEditor.close() }
                CaptureButton {
                    objectName: "recordViewerSaveSceneButton"; implicitWidth: 72; text: "保存"; tone: "primary"
                    onClicked: {
                        var first=Number(sceneStart.text),last=Number(sceneEnd.text)
                        if (!sceneName.text.trim()) { sceneEditor.validationError = "请输入场景名称。"; return }
                        if (!sceneStart.text.trim() || !sceneEnd.text.trim() || !isFinite(first) || !isFinite(last) || first < 0 || last <= first || last > page.controller.duration+.01) {
                            sceneEditor.validationError = "请输入有效范围：开始时间小于结束时间，且不超过记录时长。"
                            return
                        }
                        var saved = false, savedIndex = -1
                        if (sceneEditor.sceneId === null) {
                            saved = page.controller.addSavedScene(sceneName.text,first,last,sceneEditor.chosenColor)
                            savedIndex = page.controller.scenes.length-1
                        }
                        else {
                            var index = page.controller.scenes.findIndex(function(scene){return scene.id === sceneEditor.sceneId})
                            if (index < 0) { sceneEditor.validationError = "场景已变化，请关闭窗口后重新选择。"; return }
                            saved = page.controller.updateSavedScene(index,sceneName.text,first,last,sceneEditor.chosenColor)
                            savedIndex = index
                        }
                        if (saved) {
                            scenes.currentIndex = savedIndex
                            page.controller.selectScene(scenes.currentIndex)
                            sceneEditor.close()
                        }
                    }
                }
            }
        }
    }
    AppDialog {
        id: undoDialog; objectName: "recordViewerUndoOverwriteDialog"
        title: "撤销上次覆盖剪辑"
        subtitle: "将当前记录恢复到最近一次覆盖剪辑前。"
        width: 424; height: 244
        contentItem: ColumnLayout {
            spacing: 12
            Text {
                Layout.fillWidth: true
                text: "曲线、截图、场景和记录名称会恢复到备份中的状态。恢复后，这份备份将用完。"
                color: Theme.text; font.pixelSize: 13; wrapMode: Text.WordWrap; lineHeight: 1.4
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { implicitWidth: 72; text: "取消"; onClicked: undoDialog.close() }
                CaptureButton {
                    objectName: "recordViewerConfirmUndoButton"
                    implicitWidth: 104; text: "恢复记录"; tone: "primary"
                    enabled: page.controller.hasUndoBackup && !page.controller.editing && !appController.busy
                    onClicked: { if (page.controller.undoLastOverwrite()) undoDialog.close() }
                }
            }
        }
    }
    AppDialog {
        id: deleteDialog; objectName: "recordViewerDeleteDialog"
        property bool dataRemoval: false
        property bool compactOnly: false
        property bool createCopy: true
        readonly property real blankSeconds: page.controller.excludedIntervals.reduce(function(total,span){return total+span.end-span.start},0)
        property bool currentSceneOnly: false
        property real first: 0
        property real last: 0
        property var sceneIds: []
        property string sceneNames: ""
        title: compactOnly ? "压缩空白时间" : dataRemoval ? "删除所选时间段数据" : "删除场景标记"
        subtitle: dataRemoval ? (createCopy ? "前后拼接后生成新副本，原采集记录完整保留。" : "前后拼接并覆盖当前记录，不新增记录。") : "仅删除标记，保留性能数据和截图。"
        width: 460; height: dataRemoval ? 390 : 352
        contentItem: ColumnLayout {
            spacing: 12
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 66
                radius: 9; color: Theme.paneAlt; border.color: Theme.border
                Column { anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    Text { text: deleteDialog.compactOnly ? "将移除的空白" : deleteDialog.currentSceneOnly ? "所选场景" : "框选范围"; color: Theme.muted; font.pixelSize: 11 }
                    ElidedLabel { width: parent.width; fullText: deleteDialog.compactOnly ? page.controller.excludedIntervals.length+" 段 · 共 "+deleteDialog.blankSeconds.toFixed(2)+" 秒" : deleteDialog.currentSceneOnly ? deleteDialog.sceneNames : page.rangeLabel(deleteDialog.first,deleteDialog.last); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                }
            }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.text; font.pixelSize: 12; lineHeight: 1.35
                text: deleteDialog.compactOnly
                    ? "移除已删除段留下的空白，曲线、截图和场景时间一起前移，总时长同步缩短。完全位于空白中的场景会移除，拼接处保留细线提示。"
                    : deleteDialog.dataRemoval
                    ? "移除所选性能数据、原始帧和截图，后续内容向前拼接，总时长同步缩短。场景边界一起调整，完全位于删除段内的场景会移除。"
                    : deleteDialog.currentSceneOnly ? "将从当前记录删除这个场景标记。"
                    : "将从当前记录删除与框选范围重叠的 " + deleteDialog.sceneIds.length + " 个场景标记，包括跨越范围边界的标记。"
            }
            Text { Layout.fillWidth: true; visible: deleteDialog.dataRemoval && !deleteDialog.compactOnly && page.controller.excludedIntervals.length > 0; text: "当前记录中已有的空白时间也会一并压缩。"; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap }
            RowLayout {
                visible: deleteDialog.dataRemoval
                Layout.fillWidth: true; spacing: 12
                Text { text: "保存方式"; color: Theme.muted; font.pixelSize: 12 }
                CaptureComboBox {
                    id: saveMode; objectName: "recordViewerEditSaveMode"
                    Layout.fillWidth: true; Layout.preferredHeight: 34
                    model: ["覆盖当前记录（不生成副本）","另存为剪辑副本"]
                    currentIndex: deleteDialog.createCopy ? 1 : 0
                    onActivated: function(index) { deleteDialog.createCopy = index === 1 }
                }
            }
            Text {
                visible: deleteDialog.dataRemoval; Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: (deleteDialog.createCopy ? "保留当前记录，新增一条剪辑记录。" : "保持当前记录的名称和编号，仅保留最近一次覆盖前的回退备份。") + "会记住此选择。"
                color: Theme.muted; font.pixelSize: 11; lineHeight: 1.3
            }
            ElidedLabel { Layout.fillWidth: true; visible: !deleteDialog.dataRemoval && !deleteDialog.currentSceneOnly; fullText: deleteDialog.sceneNames; color: Theme.muted; font.pixelSize: 12 }
            Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "已生成的 HTML 报告保持不变，需重新生成才能反映编辑。"; color: Theme.muted; font.pixelSize: 11; lineHeight: 1.3 }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { implicitWidth: 72; text: "取消"; onClicked: deleteDialog.close() }
                CaptureButton {
                    objectName: "recordViewerConfirmDeleteButton"; implicitWidth: deleteDialog.dataRemoval ? 112 : 84
                    text: deleteDialog.dataRemoval ? (deleteDialog.createCopy ? "生成剪辑副本" : "覆盖并保存") : "删除标记"; tone: deleteDialog.dataRemoval ? "primary" : "danger"
                    enabled: !page.controller.editing
                    onClicked: {
                        var saved = deleteDialog.compactOnly ? page.controller.compactRemovedTimeRanges(deleteDialog.createCopy)
                            : deleteDialog.dataRemoval ? page.controller.deleteSelectedTimeRange(deleteDialog.first,deleteDialog.last,deleteDialog.createCopy)
                            : page.controller.deleteSavedScenes(deleteDialog.sceneIds)
                        if (saved) deleteDialog.close()
                    }
                }
            }
        }
    }
}
