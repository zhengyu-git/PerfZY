import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "sessionHistoryPanel"
    color: Theme.content
    property var uploader: null
    signal uploadRequested(string recordId)
    function refreshFromTop() {
        recordList.scrollToTop()
        appController.refreshSessionRecords()
    }
    property var filteredRecords: appController.sessionRecords.filter(function(record) {
        var query = searchField.text.trim().toLowerCase()
        return !query || [record.name, record.application, record.device, record.deviceModel,
            record.deviceOS, record.startedAt, record.recordedAt].join(" ").toLowerCase().indexOf(query) >= 0
    })
    property string renamingSessionId: ""
    property string baselineSessionId: ""
    property string baselineSessionName: ""
    property string deletingSessionId: ""
    property string deletingSessionName: ""
    property var mergeSelection: []
    property var contextRecord: null
    readonly property string operationBlockedReason: appController.collecting ? "正在采集，请先停止采集并等待数据保存完成。" : appController.busyReason
    readonly property string mergeDisabledReason: operationBlockedReason || (mergeSelection.length < 2 ? "请先勾选至少两条同平台的采集记录。" : "")
    readonly property bool canMerge: mergeDisabledReason.length === 0
    readonly property real deviceColumnWidth: width >= 1100 ? 190 : 160
    readonly property real timeColumnWidth: 156
    readonly property real durationColumnWidth: 64
    readonly property real actionColumnWidth: 96
    readonly property string selectionSummary: [mergeSelection.length ? "已选 " + mergeSelection.length + " 条，按勾选顺序合并" : "",
        baselineSessionId ? "对比基准：" + baselineSessionName : ""].filter(function(value) { return value.length > 0 }).join("  ·  ")
    function recordName(record) { return String(record.name || record.application || "未命名记录") }
    function selectionIndex(identifier) {
        for (var i = 0; i < mergeSelection.length; ++i)
            if (mergeSelection[i].id === identifier) return i
        return -1
    }
    function toggleMergeRecord(record) {
        var selection = mergeSelection.slice()
        var index = selectionIndex(record.id)
        if (index >= 0) selection.splice(index, 1)
        else selection.push(record)
        mergeSelection = selection
    }
    function moveMergeRecord(index, step) {
        var selection = mergeSelection.slice()
        var record = selection.splice(index, 1)[0]
        selection.splice(index + step, 0, record)
        mergeSelection = selection
    }
    function beginMerge() {
        mergeNameField.text = "合并 " + mergeSelection.length + " 条 · " + recordName(mergeSelection[0])
        mergeDialog.open()
    }
    function clearBaseline() { baselineSessionId = ""; baselineSessionName = "" }
    function primary(text) {
        var parts = String(text || "").split(/\s+[·/]\s+/)
        return parts.length ? parts[0] : "—"
    }
    function secondary(text) {
        var parts = String(text || "").split(/\s+[·/]\s+/)
        return parts.length > 1 ? parts.slice(1).join(" · ") : ""
    }
    function openRecordMenu(record, button) {
        contextRecord = record
        var point = button.mapToItem(root, 0, 0)
        function positionMenu() {
            var menuHeight = Math.max(220, recordMenu.height)
            recordMenu.x = Math.max(8, Math.min(point.x + button.width - recordMenu.width, root.width - recordMenu.width - 8))
            recordMenu.y = point.y + button.height + menuHeight + 8 <= root.height
                ? point.y + button.height + 4 : Math.max(8, point.y - menuHeight - 4)
        }
        positionMenu()
        recordMenu.open()
        Qt.callLater(function() { if (recordMenu.visible) positionMenu() })
    }
    Connections {
        target: appController
        function onSessionRecordsChanged() {
            var records = appController.sessionRecords, byId = Object.create(null)
            for (var i = 0; i < records.length; ++i) byId[records[i].id] = records[i]
            root.mergeSelection = root.mergeSelection.map(function(record) { return byId[record.id] }).filter(function(record) { return !!record })
            if (root.baselineSessionId) {
                if (byId[root.baselineSessionId]) root.baselineSessionName = root.recordName(byId[root.baselineSessionId])
                else root.clearBaseline()
            }
        }
        function onSessionsMerged(identifier) {
            mergeDialog.close()
            root.mergeSelection = []
            searchField.text = ""
            Qt.callLater(function() {
                for (var i = 0; i < root.filteredRecords.length; ++i)
                    if (root.filteredRecords[i].id === identifier) {
                        recordList.positionViewAtIndex(i, ListView.Center)
                        break
                    }
            })
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 20; spacing: 14
        ColumnLayout {
            Layout.fillWidth: true; spacing: 12
            RowLayout {
                Layout.fillWidth: true; Layout.preferredHeight: 28; spacing: 12
                Text { text: "采集记录"; color: Theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
                Text {
                    objectName: "historyRecordCount"
                    text: searchField.text.trim().length ? root.filteredRecords.length + " / " + appController.sessionRecords.length + " 条" : appController.sessionRecords.length + " 条记录"
                    color: Theme.muted; font.pixelSize: 11
                }
                Item { Layout.fillWidth: true }
                ElidedLabel {
                    Layout.maximumWidth: 430; Layout.fillWidth: true
                    fullText: appController.busyReason; color: Theme.muted; font.pixelSize: 11
                }
            }
            RowLayout {
                objectName: "sessionHistoryToolbar"
                Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 8
                CaptureTextField {
                    id: searchField; objectName: "sessionSearchField"
                    Layout.fillWidth: true; Layout.preferredWidth: 360; Layout.maximumWidth: 360; Layout.minimumWidth: 220
                    Layout.preferredHeight: 34
                    leftPadding: 34; rightPadding: text.length ? 34 : 12
                    placeholderText: "搜索名称、应用、设备或机型"
                    IconGlyph { width: 16; height: 16; x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; tint: Theme.muted }
                    HistoryActionButton {
                        objectName: "clearSessionSearchButton"
                        visible: searchField.text.length > 0
                        anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter
                        width: 26; height: 26; glyph: "close"
                        toolTipText: "清空搜索"
                        onClicked: { searchField.clear(); searchField.forceActiveFocus() }
                    }
                }
                HistoryActionButton {
                    id: refreshRecordsButton
                    objectName: "refreshSessionRecordsButton"
                    Layout.preferredWidth: 34; Layout.preferredHeight: 34; glyph: "refresh"
                    padding: 0; leftPadding: 0; rightPadding: 0
                    Accessible.name: "刷新采集记录"
                    contentItem: Item {
                        implicitWidth: 16; implicitHeight: 16
                        IconGlyph { anchors.centerIn: parent; width: 16; height: 16; name: "refresh"; tint: Theme.muted }
                    }
                    background: Rectangle {
                        radius: 8; antialiasing: true
                        color: refreshRecordsButton.down ? Theme.selected : refreshRecordsButton.hovered ? Theme.hover : Theme.control
                        border.color: refreshRecordsButton.activeFocus ? Theme.blue : Theme.border
                    }
                    toolTipText: "刷新并回到顶部"; onClicked: root.refreshFromTop()
                }
                CaptureButton {
                    objectName: "mergeRecordsButton"
                    Layout.preferredWidth: 112; Layout.preferredHeight: 34
                    text: appController.sessionsMerging ? "正在合并…" : "合并记录" + (root.mergeSelection.length ? " (" + root.mergeSelection.length + ")" : "")
                    tone: root.canMerge ? "primary" : "neutral"
                    enabled: root.canMerge
                    toolTipText: "将所选记录按顺序拼接为新记录，可在合并窗口调整顺序"
                    disabledReason: root.mergeDisabledReason
                    onClicked: root.beginMerge()
                }
                Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
                CaptureButton {
                    id: directoryButton; objectName: "historyDirectoriesButton"
                    Layout.preferredWidth: 96; Layout.preferredHeight: 34
                    leftPadding: 10; rightPadding: 10
                    onClicked: directoryMenu.open()
                    contentItem: RowLayout {
                        spacing: 6
                        Text { text: "本地目录"; color: Theme.text; font.pixelSize: 12 }
                        IconGlyph { Layout.preferredWidth: 12; Layout.preferredHeight: 12; name: "chevronDown"; tint: Theme.muted }
                    }
                    HistoryMenu {
                        id: directoryMenu; objectName: "historyDirectoryMenu"
                        y: directoryButton.height + 4
                        HistoryMenuItem { objectName: "openLocalSessionsButton"; text: "采集记录目录"; glyph: "history"; onTriggered: appController.openLocalSessionsFolder() }
                        HistoryMenuItem { objectName: "openReportsFolderButton"; text: "HTML 报告目录"; glyph: "report"; onTriggered: appController.openReportsFolder() }
                    }
                }
                HistoryActionButton {
                    objectName: "recordUploadOpenButton"
                    Layout.preferredWidth: 106; Layout.preferredHeight: 34
                    text: root.uploader && root.uploader.busy ? "上传中…" : "上传记录"
                    glyph: "upload"; enabled: !!root.uploader
                    onClicked: root.uploadRequested(root.mergeSelection.length === 1 ? root.mergeSelection[0].id : "")
                }
            }
            Rectangle {
                id: selectionContext
                objectName: "historySelectionContext"
                visible: root.mergeSelection.length > 0 || root.baselineSessionId.length > 0
                Layout.fillWidth: true; Layout.preferredHeight: 34
                radius: 8; color: Qt.rgba(Theme.blue.r, Theme.blue.g, Theme.blue.b, .07)
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 5; spacing: 8
                    ElidedLabel { Layout.fillWidth: true; Layout.minimumWidth: 0; fullText: root.selectionSummary; color: Theme.text; font.pixelSize: 12 }
                    HistoryActionButton {
                        objectName: "clearMergeSelectionButton"
                        visible: root.mergeSelection.length > 0
                        Layout.preferredWidth: 76; Layout.preferredHeight: 26
                        text: "清空选择"; enabled: !appController.sessionsMerging
                        disabledReason: appController.busyReason
                        onClicked: root.mergeSelection = []
                    }
                    HistoryActionButton {
                        objectName: "clearComparisonBaselineButton"
                        visible: root.baselineSessionId.length > 0
                        Layout.preferredWidth: 76; Layout.preferredHeight: 26
                        text: "取消基准"; onClicked: root.clearBaseline()
                    }
                }
                ControlHint { targetControl: selectionContext; text: root.selectionSummary }
            }
        }
        Rectangle {
            id: tableCard
            Layout.fillWidth: true; Layout.fillHeight: true
            color: Theme.chartBackground; border.color: Theme.border; radius: 10
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 1; spacing: 0
                Item {
                    Layout.fillWidth: true; Layout.preferredHeight: 34
                    Rectangle { anchors.fill: parent; radius: 9; color: Theme.sidebar }
                    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 10; color: Theme.sidebar }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                        Text { Layout.preferredWidth: 28; text: "选择"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.fillWidth: true; Layout.minimumWidth: 120; text: "名称 / 应用"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.preferredWidth: root.deviceColumnWidth; text: "设备 / 机型"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.preferredWidth: root.timeColumnWidth; text: "记录时间"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.preferredWidth: root.durationColumnWidth; text: "时长"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.preferredWidth: root.actionColumnWidth; text: "操作"; color: Theme.muted; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                    }
                }
                StableRecordList {
                    id: recordList; objectName: "historyRecordList"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; records: root.filteredRecords; scopeKey: searchField.text.trim().toLowerCase(); boundsBehavior: Flickable.StopAtBounds
                    WheelHandler {
                        target: null
                        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                        onWheel: function(event) {
                            var delta = event.pixelDelta.y || event.angleDelta.y / 120 * 48
                            if (!isFinite(delta) || delta === 0) { event.accepted = false; return }
                            var first = recordList.originY
                            var last = first + Math.max(0, recordList.contentHeight - recordList.height)
                            recordList.contentY = Math.max(first, Math.min(last, recordList.contentY - delta))
                            event.accepted = true
                        }
                    }
                    ScrollBar.vertical: ScrollBar {
                        parent: recordList; x: recordList.width-width; y: 0; width: 6; height: recordList.height
                        visible: size < 1; active: size < 1
                        contentItem: Rectangle { implicitWidth: 6; implicitHeight: 18; radius: 3; color: Theme.chartAxis }
                    }
                    delegate: Item {
                        id: recordRow
                        required property var modelData
                        required property int index
                        objectName: "historyRow:" + modelData.id
                        width: recordList.width; height: 56
                        Rectangle {
                            anchors.fill: parent; anchors.margins: 3; radius: 7
                            color: rowMouse.containsMouse ? Theme.hover : root.selectionIndex(recordRow.modelData.id) >= 0
                                ? Qt.rgba(Theme.blue.r, Theme.blue.g, Theme.blue.b, .08) : "transparent"
                        }
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.leftMargin: 14; anchors.rightMargin: 14; height: 1; color: Theme.border; opacity: .38; visible: recordRow.index+1 < root.filteredRecords.length }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                            CheckBox {
                                id: mergeSelectBox
                                objectName: "mergeSelect:" + recordRow.modelData.id
                                Layout.preferredWidth: 28; Layout.preferredHeight: 28
                                checked: root.selectionIndex(recordRow.modelData.id) >= 0
                                enabled: !appController.sessionsMerging
                                onClicked: root.toggleMergeRecord(recordRow.modelData)
                                ControlHint { targetControl: mergeSelectBox; text: !mergeSelectBox.enabled ? appController.busyReason : mergeSelectBox.checked ? "合并顺序：第 " + (root.selectionIndex(recordRow.modelData.id) + 1) + " 条" : "加入合并列表" }
                                indicator: Rectangle {
                                    x: (mergeSelectBox.width-width)/2; y: (mergeSelectBox.height-height)/2
                                    width: 18; height: 18; radius: 4
                                    color: mergeSelectBox.checked ? Theme.primaryFill : Theme.control
                                    border.color: mergeSelectBox.checked ? Theme.primaryFill : Theme.border
                                    Text { anchors.centerIn: parent; text: root.selectionIndex(recordRow.modelData.id) >= 0 ? root.selectionIndex(recordRow.modelData.id)+1 : ""; color: "white"; font.pixelSize: 10 }
                                }
                            }
                            ColumnLayout {
                                id: nameColumn
                                Layout.fillWidth: true; Layout.minimumWidth: 120; spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 7
                                    ElidedLabel { Layout.fillWidth: true; Layout.minimumWidth: 0; fullText: root.recordName(recordRow.modelData); color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
                                    Rectangle {
                                        visible: root.baselineSessionId === recordRow.modelData.id
                                        Layout.preferredWidth: 34; Layout.preferredHeight: 18
                                        radius: 5; color: Theme.selected
                                        Text { anchors.centerIn: parent; text: "基准"; color: Theme.blue; font.pixelSize: 10 }
                                    }
                                }
                                ElidedLabel { Layout.fillWidth: true; Layout.minimumWidth: 0; fullText: (recordRow.modelData.mergeCount ? "合并 " + recordRow.modelData.mergeCount + " 条 · " : "") + recordRow.modelData.application; color: Theme.muted; font.pixelSize: 11 }
                                ControlHint { targetControl: nameColumn; text: root.recordName(recordRow.modelData) + "\n" + recordRow.modelData.application }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: root.deviceColumnWidth; Layout.maximumWidth: root.deviceColumnWidth; spacing: 3
                                ElidedLabel { Layout.fillWidth: true; Layout.minimumWidth: 0; fullText: recordRow.modelData.deviceName || root.primary(recordRow.modelData.device); color: Theme.text; font.pixelSize: 12 }
                                ElidedLabel { Layout.fillWidth: true; Layout.minimumWidth: 0; fullText: recordRow.modelData.deviceModel || root.secondary(recordRow.modelData.device) || "型号未记录"; color: Theme.muted; font.pixelSize: 11 }
                            }
                            ElidedLabel { Layout.preferredWidth: root.timeColumnWidth; Layout.maximumWidth: root.timeColumnWidth; fullText: recordRow.modelData.recordedAt || recordRow.modelData.startedAt || "时间未记录"; color: Theme.text; font.pixelSize: 12; font.family: Theme.numberFont }
                            Text { Layout.preferredWidth: root.durationColumnWidth; text: recordRow.modelData.duration; color: Theme.text; font.pixelSize: 12; font.family: Theme.numberFont }
                            RowLayout {
                                Layout.preferredWidth: root.actionColumnWidth; spacing: 8
                                HistoryActionButton {
                                    objectName: "viewSessionButton:" + recordRow.modelData.id
                                    Layout.preferredWidth: 54; Layout.preferredHeight: 30
                                    text: "查看"; primary: true
                                    toolTipText: "查看曲线与截图；也可双击记录"
                                    onClicked: appController.openSession(recordRow.modelData.id)
                                }
                                HistoryActionButton {
                                    id: moreButton
                                    objectName: "sessionMoreButton:" + recordRow.modelData.id
                                    Layout.preferredWidth: 30; Layout.preferredHeight: 30
                                    glyph: "more"; toolTipText: "重命名、报告、对比与其他操作"
                                    onClicked: root.openRecordMenu(recordRow.modelData, moreButton)
                                }
                            }
                        }
                        MouseArea {
                            id: rowMouse
                            objectName: "historyRowBody:" + recordRow.modelData.id
                            anchors.fill: parent; anchors.leftMargin: 54; anchors.rightMargin: root.actionColumnWidth+26
                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onWheel: function(wheel) { wheel.accepted = false }
                            onDoubleClicked: appController.openSession(recordRow.modelData.id)
                        }
                    }
                    Column {
                        objectName: "historyEmptyState"
                        parent: recordList
                        anchors.centerIn: parent; spacing: 10
                        visible: root.filteredRecords.length === 0
                        IconGlyph { anchors.horizontalCenter: parent.horizontalCenter; width: 32; height: 32; name: "history"; tint: Theme.muted; opacity: .6 }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: searchField.text.length ? "没有匹配的采集记录" : "暂无采集记录"; color: Theme.text; font.pixelSize: 14; font.weight: Font.Medium }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: searchField.text.length ? "换个关键词试试，或清空搜索" : "完成采集并保存后，会显示在这里"; color: Theme.muted; font.pixelSize: 12 }
                    }
                }
            }
        }
    }

    HistoryMenu {
        id: recordMenu; objectName: "sessionRecordMenu"
        parent: root
        HistoryMenuItem {
            objectName: "renameSessionMenuItem"
            text: "重命名"; glyph: "rename"
            onTriggered: {
                root.renamingSessionId = root.contextRecord.id
                renameField.text = root.recordName(root.contextRecord)
                renameDialog.open()
            }
        }
        HistoryMenuItem {
            objectName: "reuseCaptureConfigurationMenuItem"
            text: "沿用本次采集配置"; glyph: "settings"
            enabled: !!root.contextRecord && !root.operationBlockedReason
            disabledReason: root.operationBlockedReason
            onTriggered: appController.reuseCaptureConfiguration(root.contextRecord.id)
        }
        HistoryMenuItem {
            objectName: "reportSessionMenuItem"
            text: "生成报告"; glyph: "report"
            enabled: !root.operationBlockedReason
            disabledReason: root.operationBlockedReason
            onTriggered: appController.generateReportForSession(root.contextRecord.id)
        }
        HistoryMenuItem {
            objectName: "uploadSessionMenuItem"; text: "上传记录"; glyph: "upload"
            enabled: !!root.contextRecord && !!root.uploader
            onTriggered: root.uploadRequested(root.contextRecord.id)
        }
        HistoryMenuItem {
            objectName: "comparisonBaselineButton:" + (root.contextRecord ? root.contextRecord.id : "")
            text: marked ? "取消对比基准" : "设为对比基准"; glyph: "bookmark"
            marked: !!root.contextRecord && root.baselineSessionId === root.contextRecord.id
            onTriggered: {
                if (root.baselineSessionId === root.contextRecord.id) root.clearBaseline()
                else { root.baselineSessionId = root.contextRecord.id; root.baselineSessionName = root.recordName(root.contextRecord) }
            }
        }
        HistoryMenuItem {
            objectName: "comparisonReportButton:" + (root.contextRecord ? root.contextRecord.id : "")
            text: "与基准对比"; glyph: "compare"
            enabled: !!root.contextRecord && root.baselineSessionId.length > 0 && root.baselineSessionId !== root.contextRecord.id && !root.operationBlockedReason
            disabledReason: root.operationBlockedReason || (!root.baselineSessionId ? "请先将一条记录设为对比基准。" : "当前记录已是基准，请选择另一条记录。")
            onTriggered: appController.generateComparisonReport(root.baselineSessionId, root.contextRecord.id)
        }
        HistoryMenuItem { objectName: "openSessionFolderMenuItem"; text: "打开会话目录"; glyph: "history"; onTriggered: appController.openSessionFolder(root.contextRecord.id) }
        MenuSeparator { padding: 5; contentItem: Rectangle { implicitWidth: 198; implicitHeight: 1; color: Theme.border } }
        HistoryMenuItem {
            objectName: "deleteSessionMenuItem"
            text: "删除记录"; glyph: "trash"; destructive: true
            enabled: !root.operationBlockedReason; disabledReason: root.operationBlockedReason
            onTriggered: {
                root.deletingSessionId = root.contextRecord.id
                root.deletingSessionName = root.recordName(root.contextRecord)
                deleteDialog.open()
            }
        }
    }

    AppDialog {
        id: mergeDialog; dismissible: !appController.sessionsMerging; objectName: "mergeRecordsDialog"
        modal: true; anchors.centerIn: parent
        width: Math.min(660, root.width - 24); height: Math.min(500, root.height - 32, 296 + root.mergeSelection.length * 56)
        title: "按顺序合并采集记录"; standardButtons: Dialog.NoButton
        closePolicy: appController.sessionsMerging ? Popup.NoAutoClose : Popup.CloseOnEscape
        
        
        contentItem: ColumnLayout {
            spacing: 10
            Text { text: "合并后的记录名称"; color: Theme.text; font.pixelSize: 12 }
            CaptureTextField {
                id: mergeNameField; objectName: "mergeNameField"
                Layout.fillWidth: true; Layout.preferredHeight: 32
                horizontalAlignment: Text.AlignLeft; maximumLength: 80
                enabled: !appController.sessionsMerging
                disabledReason: appController.busyReason
            }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                text: "按下方顺序拼接，使用 ↑ ↓ 调整。指标、截图和场景时间依次累加，记录之间的实际等待时间不计入。"
            }
            ListView {
                id: mergeOrderList; objectName: "mergeOrderList"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                model: root.mergeSelection; spacing: 4
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Rectangle {
                    required property var modelData; required property int index
                    width: mergeOrderList.width; height: 52; color: Theme.content; radius: 8
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 6; spacing: 7
                        Text { Layout.preferredWidth: 24; text: index + 1; color: Theme.blue; font.pixelSize: 12 }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 1
                            Text { Layout.fillWidth: true; text: modelData.name || modelData.application; color: Theme.text; font.pixelSize: 12; elide: Text.ElideRight }
                            Text { Layout.fillWidth: true; text: (modelData.recordedAt || modelData.startedAt) + " · " + modelData.duration + " · " + modelData.device; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
                        }
                        CaptureButton { leftPadding: 7; rightPadding: 7; objectName: "mergeUp:" + index; Layout.preferredWidth: 32; text: "↑"; enabled: index > 0 && !appController.sessionsMerging; toolTipText: "提前拼接这条记录"; disabledReason: appController.sessionsMerging ? appController.busyReason : "这条记录已经排在第一位。"; onClicked: root.moveMergeRecord(index, -1) }
                        CaptureButton { leftPadding: 7; rightPadding: 7; objectName: "mergeDown:" + index; Layout.preferredWidth: 32; text: "↓"; enabled: index + 1 < root.mergeSelection.length && !appController.sessionsMerging; toolTipText: "延后拼接这条记录"; disabledReason: appController.sessionsMerging ? appController.busyReason : "这条记录已经排在最后一位。"; onClicked: root.moveMergeRecord(index, 1) }
                        CaptureButton { Layout.preferredWidth: 56; text: "移除"; enabled: !appController.sessionsMerging; disabledReason: appController.busyReason; onClicked: root.toggleMergeRecord(modelData) }
                    }
                }
            }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                text: "请选择同一平台的记录。设备和应用信息采用第一条，保存各段来源；合并生成新记录，原记录保留。"
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { objectName: "cancelMergeButton"; Layout.preferredWidth: 64; text: "取消"; enabled: !appController.sessionsMerging; disabledReason: appController.busyReason; onClicked: mergeDialog.close() }
                CaptureButton { tone: "primary";
                    objectName: "confirmMergeButton"; Layout.preferredWidth: 100
                    text: appController.sessionsMerging ? "正在合并…" : "合并并保存"; accentColor: Theme.primaryFill
                    enabled: root.canMerge && mergeNameField.text.trim().length > 0
                    disabledReason: root.mergeDisabledReason || "请填写合并后的记录名称。"
                    onClicked: appController.mergeSessions(root.mergeSelection.map(function(record) { return record.id }), mergeNameField.text)
                }
            }
        }
    }

    AppDialog {
        id: renameDialog; objectName: "renameSessionDialog"
        modal: true; anchors.centerIn: parent; width: 420; height: 228
        title: "重命名采集记录"; standardButtons: Dialog.NoButton
        onOpened: { renameField.forceActiveFocus(); renameField.selectAll() }
        
        
        contentItem: ColumnLayout {
            spacing: 10
            Text { text: "新名称"; color: Theme.text; font.pixelSize: 12 }
            CaptureTextField {
                id: renameField; objectName: "renameSessionNameField"; Layout.fillWidth: true; Layout.preferredHeight: 34
                horizontalAlignment: Text.AlignLeft; maximumLength: 80
                onAccepted: renameSaveButton.clicked()
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { objectName: "cancelRenameButton"; Layout.preferredWidth: 64; text: "取消"; onClicked: renameDialog.close() }
                CaptureButton { tone: "primary";
                    id: renameSaveButton; objectName: "confirmRenameSessionButton"; Layout.preferredWidth: 76; text: "保存"; accentColor: Theme.primaryFill
                    onClicked: {
                        if (appController.renameSession(root.renamingSessionId, renameField.text))
                            renameDialog.close()
                    }
                }
            }
        }
    }

    AppDialog {
        id: deleteDialog; objectName: "deleteSessionDialog"
        modal: true; anchors.centerIn: parent; width: 440; height: 246
        title: "删除采集记录"; standardButtons: Dialog.NoButton
        
        
        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: "确定删除“" + root.deletingSessionName + "”吗？\n对应的采集数据和全部截图都会被永久删除。"
                color: Theme.text; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { objectName: "cancelDeleteButton"; Layout.preferredWidth: 64; text: "取消"; onClicked: deleteDialog.close() }
                CaptureButton { tone: "danger"; objectName: "confirmDeleteSessionButton";
                    Layout.preferredWidth: 90; text: "确认删除"; accentColor: Theme.red
                    onClicked: {
                        if (appController.deleteSession(root.deletingSessionId))
                            deleteDialog.close()
                    }
                }
            }
        }
    }
}
