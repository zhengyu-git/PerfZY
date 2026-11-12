import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "htmlReportPage"
    color: Theme.content
    signal sessionsRequested()
    property var contextReport: null
    property string selectedReportId: ""
    readonly property bool showDeviceColumn: width >= 920
    readonly property real deviceColumnWidth: width >= 1120 ? 200 : 160
    readonly property real timeColumnWidth: width >= 1000 ? 154 : 136
    readonly property real sizeColumnWidth: 72
    readonly property real actionColumnWidth: 110
    readonly property bool hasFilters: search.text.trim().length > 0 || typeFilter.currentIndex > 0
    readonly property string managementBlockedReason: appController.reportGenerating ? "报告正在生成，完成后可修改文件名或删除。" : ""
    property var filteredReports: {
        var query = search.text.trim().toLowerCase()
        var kind = ["", "capture", "comparison", "html"][typeFilter.currentIndex] || ""
        var rows = appController.htmlReports.filter(function(report) {
            return (!kind || report.kind === kind) && (!query || [report.title, report.filename,
                report.application, report.device, report.deviceModel].join(" ").toLowerCase().indexOf(query) >= 0)
        })
        rows.sort(function(a, b) {
            if (sortField.currentIndex === 2) return String(a.title).localeCompare(String(b.title))
            return sortField.currentIndex === 1 ? a.sortTimestamp-b.sortTimestamp : b.sortTimestamp-a.sortTimestamp
        })
        return rows
    }
    onVisibleChanged: if (visible) appController.refreshHtmlReports()
    Component.onCompleted: if (visible) appController.refreshHtmlReports()
    function openMenu(report, button) {
        contextReport = report
        selectedReportId = report.id
        var point = button.mapToItem(root, 0, 0)
        function place() {
            var h = Math.max(208, reportMenu.height)
            reportMenu.x = Math.max(8, Math.min(point.x + button.width-reportMenu.width, root.width-reportMenu.width-8))
            reportMenu.y = point.y+button.height+h+8 <= root.height ? point.y+button.height+4 : Math.max(8,point.y-h-4)
        }
        place(); reportMenu.open()
        Qt.callLater(function() { if (reportMenu.visible) place() })
    }
    function clearFilters() { search.clear(); typeFilter.currentIndex = 0 }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 16
        ColumnLayout {
            Layout.fillWidth: true; spacing: 6
            RowLayout {
                Layout.fillWidth: true; spacing: 10
                Text { text: "HTML 报告"; color: Theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
                Text { objectName: "htmlReportCount"; text: appController.htmlReports.length + " 份报告"; color: Theme.muted; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Text { visible: appController.htmlReportsLoading; text: "正在刷新…"; color: Theme.muted; font.pixelSize: 11 }
                CaptureButton {
                    objectName: "reportLibrarySessionsButton"
                    Layout.preferredWidth: 96; Layout.preferredHeight: 32
                    text: "采集记录"; onClicked: root.sessionsRequested()
                }
            }
            Text { text: "集中查看已生成的本地报告，也可以双击列表打开。"; color: Theme.muted; font.pixelSize: 12 }
        }
        RowLayout {
            objectName: "htmlReportToolbar"
            Layout.fillWidth: true; Layout.preferredHeight: 34; spacing: 8
            CaptureTextField {
                id: search; objectName: "htmlReportSearch"
                Layout.fillWidth: true; Layout.preferredWidth: 320; Layout.maximumWidth: 360; Layout.minimumWidth: 190; Layout.preferredHeight: 34
                placeholderText: "搜索报告、应用或设备"
                leftPadding: 34; rightPadding: text.length ? 33 : 12
                IconGlyph { width: 16; height: 16; x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; tint: Theme.muted }
                HistoryActionButton {
                    visible: search.text.length > 0
                    width: 26; height: 26; anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter
                    glyph: "close"; toolTipText: "清空搜索"
                    onClicked: { search.clear(); search.forceActiveFocus() }
                }
            }
            CaptureComboBox {
                id: typeFilter; objectName: "htmlReportTypeFilter"
                Layout.preferredWidth: 126; Layout.preferredHeight: 34
                model: ["全部类型", "采集报告", "对比报告", "其他 HTML"]
            }
            CaptureComboBox {
                id: sortField; objectName: "htmlReportSort"
                Layout.preferredWidth: 126; Layout.preferredHeight: 34
                model: ["最近更新", "最早更新", "名称升序"]
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
            HistoryActionButton {
                objectName: "refreshHtmlReportsButton"
                Layout.preferredWidth: 34; Layout.preferredHeight: 34
                glyph: "refresh"; toolTipText: "刷新本地 HTML 报告"
                onClicked: appController.refreshHtmlReports()
            }
            CaptureButton {
                objectName: "htmlReportsFolderButton"
                Layout.preferredWidth: 96; Layout.preferredHeight: 34
                text: "报告目录"; onClicked: appController.openReportsFolder()
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            color: Theme.chartBackground; border.color: Theme.border; radius: 12
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 1; spacing: 0
                Item {
                    Layout.fillWidth: true; Layout.preferredHeight: 36
                    Rectangle { anchors.fill: parent; radius: 11; color: Theme.sidebar }
                    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 10; color: Theme.sidebar }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16; spacing: 14
                        Text { Layout.fillWidth: true; Layout.minimumWidth: 150; text: "报告 / 应用"; color: Theme.muted; font.pixelSize: 11 }
                        Text { visible: root.showDeviceColumn; Layout.preferredWidth: root.deviceColumnWidth; text: "设备"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.preferredWidth: root.timeColumnWidth; text: "更新时间"; color: Theme.muted; font.pixelSize: 11 }
                        Text { Layout.preferredWidth: root.sizeColumnWidth; text: "大小"; color: Theme.muted; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                        Text { Layout.preferredWidth: root.actionColumnWidth; text: "操作"; color: Theme.muted; font.pixelSize: 11; horizontalAlignment: Text.AlignRight }
                    }
                }
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    StableRecordList {
                        id: reportList; objectName: "htmlReportList"
                        anchors.fill: parent; clip: true; boundsBehavior: Flickable.StopAtBounds
                        records: root.filteredReports
                        scopeKey: search.text.trim().toLowerCase() + ":" + typeFilter.currentIndex + ":" + sortField.currentIndex
                        ScrollBar.vertical: ScrollBar { width: 6; active: size < 1; visible: size < 1; contentItem: Rectangle { implicitWidth: 6; implicitHeight: 20; radius: 3; color: Theme.chartAxis } }
                        delegate: Item {
                            id: reportRow
                            required property var modelData
                            required property int index
                            objectName: "htmlReportRow:" + modelData.id
                            width: reportList.width; height: 72
                            Rectangle {
                                anchors.fill: parent; anchors.margins: 4; radius: 8
                                color: rowMouse.containsMouse ? Theme.hover : root.selectedReportId === reportRow.modelData.id
                                    ? Qt.rgba(Theme.blue.r,Theme.blue.g,Theme.blue.b,.07) : "transparent"
                            }
                            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.leftMargin: 16; anchors.rightMargin: 16; height: 1; color: Theme.border; opacity: .4; visible: reportRow.index+1 < root.filteredReports.length }
                            MouseArea {
                                id: rowMouse
                                anchors.fill: parent; anchors.rightMargin: root.actionColumnWidth + 28
                                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: root.selectedReportId = reportRow.modelData.id
                                onDoubleClicked: appController.openHtmlReport(reportRow.modelData.id)
                            }
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16; spacing: 14
                                RowLayout {
                                    id: reportName
                                    Layout.fillWidth: true; Layout.minimumWidth: 150; spacing: 12
                                    Rectangle {
                                        Layout.preferredWidth: 34; Layout.preferredHeight: 38; radius: 8; color: Theme.sidebar
                                        IconGlyph { anchors.centerIn: parent; width: 19; height: 19; name: "report"; tint: Theme.blue }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 5
                                        ElidedLabel { Layout.fillWidth: true; Layout.minimumWidth: 0; fullText: reportRow.modelData.title; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
                                        ElidedLabel {
                                            Layout.fillWidth: true; Layout.minimumWidth: 0
                                            fullText: [reportRow.modelData.sourceChanged ? "源记录已修改" : reportRow.modelData.kindLabel,
                                                reportRow.modelData.application, root.showDeviceColumn ? "" : reportRow.modelData.device].filter(function(s) { return s }).join(" · ")
                                            color: Theme.muted; font.pixelSize: 11
                                        }
                                    }
                                    ControlHint { targetControl: reportName; text: reportRow.modelData.filename + "\n" + reportRow.modelData.application }
                                }
                                ColumnLayout {
                                    visible: root.showDeviceColumn
                                    Layout.preferredWidth: root.deviceColumnWidth; Layout.maximumWidth: root.deviceColumnWidth; spacing: 4
                                    ElidedLabel { Layout.fillWidth: true; fullText: reportRow.modelData.device || "未记录设备"; color: Theme.text; font.pixelSize: 12 }
                                    ElidedLabel { Layout.fillWidth: true; fullText: [reportRow.modelData.deviceModel === reportRow.modelData.device ? "" : reportRow.modelData.deviceModel,reportRow.modelData.deviceOS].filter(function(s) { return s }).join(" · ") || "—"; color: Theme.muted; font.pixelSize: 11 }
                                }
                                ColumnLayout {
                                    Layout.preferredWidth: root.timeColumnWidth; spacing: 4
                                    Text { text: reportRow.modelData.updatedAt.split(" ")[0]; color: Theme.text; font.pixelSize: 12; font.family: Theme.numberFont }
                                    Text { text: reportRow.modelData.updatedAt.split(" ")[1] || ""; color: Theme.muted; font.pixelSize: 11; font.family: Theme.numberFont }
                                }
                                Text { Layout.preferredWidth: root.sizeColumnWidth; text: reportRow.modelData.size; color: Theme.muted; font.pixelSize: 12; horizontalAlignment: Text.AlignRight }
                                RowLayout {
                                    Layout.preferredWidth: root.actionColumnWidth; spacing: 8
                                    HistoryActionButton {
                                        objectName: "openHtmlReportButton:" + reportRow.modelData.id
                                        Layout.preferredWidth: 66; Layout.preferredHeight: 30
                                        text: "打开"; primary: true; toolTipText: "在浏览器中打开 HTML 报告"
                                        onClicked: appController.openHtmlReport(reportRow.modelData.id)
                                    }
                                    HistoryActionButton {
                                        id: moreButton
                                        objectName: "htmlReportMoreButton:" + reportRow.modelData.id
                                        Layout.preferredWidth: 30; Layout.preferredHeight: 30
                                        glyph: "more"; toolTipText: "查看源记录、文件名与其他操作"
                                        onClicked: root.openMenu(reportRow.modelData,moreButton)
                                    }
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        objectName: "htmlReportsEmptyState"
                        anchors.centerIn: parent; width: Math.min(400,parent.width-40); spacing: 12
                        visible: root.filteredReports.length === 0 && !appController.htmlReportsLoading
                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 48; Layout.preferredHeight: 48
                            radius: 13; color: Theme.sidebar
                            IconGlyph { anchors.centerIn: parent; width: 24; height: 24; name: root.hasFilters ? "search" : "report"; tint: Theme.muted }
                        }
                        Text { Layout.alignment: Qt.AlignHCenter; text: root.hasFilters ? "没有找到匹配的报告" : "还没有 HTML 报告"; color: Theme.text; font.pixelSize: 16; font.weight: Font.Medium }
                        Text { Layout.fillWidth: true; text: root.hasFilters ? "换个关键词，或清除筛选后再试。" : "在采集记录中生成报告后，会自动出现在这里。"; color: Theme.muted; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
                        CaptureButton {
                            objectName: "htmlReportsEmptyAction"
                            Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 132; Layout.preferredHeight: 34
                            text: root.hasFilters ? "清除筛选" : "前往采集记录"; tone: "primary"
                            onClicked: root.hasFilters ? root.clearFilters() : root.sessionsRequested()
                        }
                    }
                    BusyIndicator { anchors.centerIn: parent; running: appController.htmlReportsLoading && !appController.htmlReports.length; visible: running; width: 32; height: 32 }
                }
            }
        }
    }

    HistoryMenu {
        id: reportMenu; parent: root; objectName: "htmlReportMenu"
        HistoryMenuItem { text: "打开报告"; glyph: "report"; onTriggered: if (root.contextReport) appController.openHtmlReport(root.contextReport.id) }
        HistoryMenuItem {
            objectName: "htmlReportSourceAction"; text: "查看源采集记录"; glyph: "history"
            enabled: Boolean(root.contextReport && root.contextReport.sourceAvailable)
            disabledReason: "源采集记录已不存在或未关联；HTML 仍可独立查看。"
            onTriggered: appController.openSession(root.contextReport.sourceSessionId)
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        HistoryMenuItem {
            text: "修改文件名"; glyph: "rename"; enabled: !root.managementBlockedReason.length; disabledReason: root.managementBlockedReason
            onTriggered: { filenameField.text = root.contextReport.title; renameDialog.open() }
        }
        HistoryMenuItem { text: "打开报告目录"; glyph: "history"; onTriggered: appController.openReportsFolder() }
        HistoryMenuItem {
            objectName: "htmlReportDeleteAction"; text: "删除报告"; glyph: "trash"; destructive: true
            enabled: !root.managementBlockedReason.length; disabledReason: root.managementBlockedReason
            onTriggered: deleteDialog.open()
        }
    }
    AppDialog {
        id: renameDialog; objectName: "renameHtmlReportDialog"
        modal: true; anchors.centerIn: parent; width: 430; height: 242
        title: "修改报告文件名"; standardButtons: Dialog.NoButton
        onOpened: { filenameField.forceActiveFocus(); filenameField.selectAll() }
        contentItem: ColumnLayout {
            spacing: 10
            Text { Layout.fillWidth: true; text: "修改本地 HTML 文件名，报告内容保持原样。"; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
            CaptureTextField { id: filenameField; objectName: "htmlReportFilenameField"; Layout.fillWidth: true; Layout.preferredHeight: 34; maximumLength: 160; placeholderText: "文件名，无需输入 .html"; onAccepted: renameSave.clicked() }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { text: "取消"; Layout.preferredWidth: 64; onClicked: renameDialog.close() }
                CaptureButton {
                    id: renameSave; objectName: "confirmRenameHtmlReportButton"
                    text: "保存"; tone: "primary"; Layout.preferredWidth: 76
                    enabled: filenameField.text.trim().length > 0 && !root.managementBlockedReason.length
                    onClicked: {
                        var anchor = reportList.captureViewport()
                        var oldId = root.contextReport.id
                        var result = appController.renameHtmlReport(oldId,filenameField.text)
                        if (result) {
                            if (anchor && anchor.id === oldId) anchor.id = result
                            reportList.pendingAnchor = anchor
                            root.selectedReportId = result
                            renameDialog.close()
                        }
                    }
                }
            }
        }
    }
    AppDialog {
        id: deleteDialog; objectName: "deleteHtmlReportDialog"
        modal: true; anchors.centerIn: parent; width: 430; height: 244
        title: "删除 HTML 报告"; standardButtons: Dialog.NoButton
        contentItem: ColumnLayout {
            spacing: 10
            ElidedLabel { Layout.fillWidth: true; fullText: root.contextReport ? root.contextReport.filename : ""; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
            Text { Layout.fillWidth: true; text: "仅删除这个 HTML 文件，原采集记录和截图保留。"; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight; spacing: 8
                CaptureButton { text: "取消"; Layout.preferredWidth: 64; onClicked: deleteDialog.close() }
                CaptureButton {
                    objectName: "confirmDeleteHtmlReportButton"
                    text: "删除报告"; tone: "danger"; Layout.preferredWidth: 96
                    enabled: !root.managementBlockedReason.length
                    onClicked: if (root.contextReport && appController.deleteHtmlReport(root.contextReport.id)) {
                        root.selectedReportId = ""; deleteDialog.close()
                    }
                }
            }
        }
    }
}
