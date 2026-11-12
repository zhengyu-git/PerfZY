import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Pane {
    id: root
    property var controller: appController
    property bool viewOnly: false
    property real savedChartListY: 0
    property string savedChartKey: ""
    property real savedChartOffset: 0
    property bool hasSavedPosition: false
    property bool restoringPosition: false
    property bool positionRestorePending: false
    property bool showAdvanced: false
    property bool showSystem: false
    property string expandedChart: ""
    readonly property string frameCapabilityNote: root.controller.chartManagerCatalog
        .filter(function(row) { return !!row.capabilityNote })
        .map(function(row) { return row.capabilityNote }).join("\n")

    function chartRows() {
        return root.controller.chartManagerCatalog.filter(function(row) {
            return row.tier === "basic" || (row.tier === "advanced" && root.showAdvanced)
                    || (row.tier === "system" && root.showSystem)
        })
    }

    function rememberChartListPosition() {
        if (restoringPosition || positionRestorePending || chartList.height <= 0 || !chartList.visible) return
        savedChartListY = chartList.contentY - chartList.originY
        savedChartKey = ""
        // Anchor to the first visible group. Heights can change when a group
        // expands, and the catalog is replaced when a metric is selected.
        for (var y = 0; y < chartList.height; y += 4) {
            var index = chartList.indexAt(1, chartList.contentY + y)
            if (index < 0) continue
            var item = chartList.itemAtIndex(index)
            if (item) {
                savedChartKey = item.modelData.key
                savedChartOffset = item.y - chartList.contentY
            }
            break
        }
        hasSavedPosition = true
    }

    function queuePositionRestore() {
        if (!hasSavedPosition) return
        positionRestorePending = true
        Qt.callLater(restoreChartListPosition)
    }

    function restoreChartListPosition() {
        if (!hasSavedPosition || chartList.height <= 0 || !chartList.visible) {
            positionRestorePending = false
            return
        }
        restoringPosition = true
        chartList.cancelFlick()
        chartList.forceLayout()
        var targetY = chartList.originY + savedChartListY
        var rows = chartRows()
        for (var i = 0; i < rows.length; ++i) {
            if (rows[i].key !== savedChartKey) continue
            chartList.positionViewAtIndex(i, ListView.Beginning)
            var item = chartList.itemAtIndex(i)
            if (item) targetY = item.y - savedChartOffset
            break
        }
        var maximumY = chartList.originY + Math.max(0, chartList.contentHeight - chartList.height)
        chartList.contentY = Math.max(chartList.originY, Math.min(targetY, maximumY))
        restoringPosition = false
        positionRestorePending = false
    }

    function toggleChart(key, visible) {
        rememberChartListPosition()
        root.controller.setChartVisible(key, visible)
        queuePositionRestore()
    }

    function toggleExpandedChart(key) {
        rememberChartListPosition()
        root.expandedChart = root.expandedChart === key ? "" : key
        queuePositionRestore()
    }

    function moveChart(key, direction) {
        rememberChartListPosition()
        root.controller.moveChart(key, direction)
        queuePositionRestore()
    }

    function setMetricEnabled(key, selected, groupKey) {
        rememberChartListPosition()
        root.controller.setMetricEnabled(key, selected)
        if (selected) root.controller.setChartVisible(groupKey, true)
        queuePositionRestore()
    }

    function setTierVisible(tier, shown) {
        rememberChartListPosition()
        if (tier === "advanced") showAdvanced = shown
        else showSystem = shown
        queuePositionRestore()
    }

    objectName: "metricSelectionPanel"
    implicitWidth: 390; implicitHeight: 620
    padding: 0
    background: Item {}
    contentItem: ColumnLayout {
        spacing: 0
        Rectangle {
            visible: !root.viewOnly
            Layout.fillWidth: true; Layout.preferredHeight: 54; Layout.margins: 8
            color: screenshotMouse.containsMouse ? Theme.hover : Theme.paneAlt
            border.color: Theme.border; radius: 10
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 9
                Rectangle {
                    Layout.preferredWidth: 28; Layout.maximumWidth: 28; Layout.preferredHeight: 28; radius: 8; color: Theme.color("#242a38")
                    Rectangle {
                        anchors.centerIn: parent; width: 16; height: 11; radius: 2
                        color: "transparent"; border.width: 1; border.color: root.controller.screenshotEnabled ? Theme.color("#71a5ff") : Theme.color("#747b8d")
                        Rectangle { anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: "transparent"; border.width: 1; border.color: parent.border.color }
                        Rectangle { x: 3; y: -3; width: 6; height: 3; radius: 1; color: parent.border.color }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 1
                    Text { Layout.fillWidth: true; text: "截图"; color: root.controller.screenshotEnabled ? Theme.color("#e0e8fb") : Theme.color("#747b8d"); font.pixelSize: 11; font.weight: Font.Medium }
                    Text { Layout.fillWidth: true; text: "采集并显示时间轴截图"; color: Theme.color("#8792ac"); font.pixelSize: 8 }
                }
                Rectangle {
                    Layout.preferredWidth: 34; Layout.maximumWidth: 34; Layout.preferredHeight: 18; radius: 9
                    color: root.controller.screenshotEnabled ? Theme.color("#4169e1") : Theme.color("#1f2330"); border.color: root.controller.screenshotEnabled ? Theme.color("#7895ff") : Theme.color("#596178")
                    Rectangle { width: 14; height: 14; radius: 7; y: 2; x: root.controller.screenshotEnabled ? 18 : 2; color: root.controller.screenshotEnabled ? "white" : Theme.color("#747b8d") }
                }
            }
            MouseArea { id: screenshotMouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.controller.setScreenshotEnabled(!root.controller.screenshotEnabled) }
        }

        Text { Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12; Layout.bottomMargin: 6; text: "组开关控制采集和显示 · 点击名称可选单项指标"; color: Theme.color("#8f9ab4"); font.pixelSize: 10; wrapMode: Text.Wrap }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8
            MetricCheckBox { objectName: "advancedMetricsToggle"; text: "高级明细"; palette.windowText: Theme.color("#d2daee"); checked: root.showAdvanced; onToggled: root.setTierVisible("advanced", checked) }
            MetricCheckBox { objectName: "systemMetricsToggle"; text: "系统环境"; palette.windowText: Theme.color("#d2daee"); checked: root.showSystem; onToggled: root.setTierVisible("system", checked) }
        }

        Label {
            objectName: "frameCapabilityNote"
            Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 12
            Layout.topMargin: visible ? 5 : 0; Layout.bottomMargin: visible ? 8 : 0
            visible: root.frameCapabilityNote.length > 0
            text: root.frameCapabilityNote; textFormat: Text.PlainText
            color: Theme.color("#c5ae7a"); font.pixelSize: 10; wrapMode: Text.Wrap
        }

        ListView {
            id: chartList
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: 8; Layout.rightMargin: 5; Layout.bottomMargin: 8
            objectName: "metricChartList"
            clip: true; spacing: 4; model: root.chartRows()
            boundsBehavior: Flickable.StopAtBounds
            onModelChanged: root.queuePositionRestore()
            onHeightChanged: root.queuePositionRestore()
            onMovementEnded: root.rememberChartListPosition()
            onContentYChanged: {
                if (!root.restoringPosition && !root.positionRestorePending) {
                    if (moving) root.rememberChartListPosition()
                    else Qt.callLater(root.rememberChartListPosition)
                }
            }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Rectangle {
                id: chartRow
                required property var modelData
                required property int index
                property bool displayed: modelData.visible
                objectName: "metricGroup:" + modelData.key
                width: chartList.width - 13; height: 50 + (root.expandedChart === modelData.key ? metricDetails.implicitHeight + 8 : 0); radius: 10
                color: chartMouse.containsMouse ? Theme.hover : Theme.control
                border.color: Theme.border
                RowLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    height: 50; anchors.leftMargin: 10; anchors.rightMargin: 7; spacing: 7
                    IconGlyph { Layout.preferredWidth: 12; Layout.preferredHeight: 12; name: root.expandedChart === chartRow.modelData.key ? "chevronUp" : "chevronDown"; tint: Theme.muted }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 1
                        Text { Layout.fillWidth: true; text: modelData.title; color: chartRow.displayed ? Theme.color("#e0e8fb") : Theme.color("#747b8d"); font.pixelSize: 11; font.weight: Font.Medium; elide: Text.ElideRight }
                        Text { Layout.fillWidth: true; text: modelData.seriesNames.join(" · "); color: chartRow.displayed ? Theme.color("#aeb9d2") : Theme.color("#626a7d"); font.pixelSize: 11; font.weight: Font.Medium; elide: Text.ElideRight }
                    }
                    ToolButton {
                        id: moveUpButton
                        Layout.preferredWidth: 26; Layout.preferredHeight: 28; enabled: modelData.canMoveUp
                        ControlHint { targetControl: moveUpButton; text: moveUpButton.enabled ? "将此图表向上移动" : "此图表已经排在最上方。" }
                        text: "↑"; onClicked: root.moveChart(modelData.key, -1)
                        background: Rectangle { color: moveUpButton.hovered ? Theme.color("#454d62") : Theme.color("#272c3a"); border.color: Theme.color("#4d566d"); radius: 2 }
                        contentItem: Text { text: moveUpButton.text; color: moveUpButton.enabled ? Theme.color("#cbd5ee") : Theme.color("#555d70"); horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    ToolButton {
                        id: moveDownButton
                        Layout.preferredWidth: 26; Layout.preferredHeight: 28; enabled: modelData.canMoveDown
                        ControlHint { targetControl: moveDownButton; text: moveDownButton.enabled ? "将此图表向下移动" : "此图表已经排在最下方。" }
                        text: "↓"; onClicked: root.moveChart(modelData.key, 1)
                        background: Rectangle { color: moveDownButton.hovered ? Theme.color("#454d62") : Theme.color("#272c3a"); border.color: Theme.color("#4d566d"); radius: 2 }
                        contentItem: Text { text: moveDownButton.text; color: moveDownButton.enabled ? Theme.color("#cbd5ee") : Theme.color("#555d70"); horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Rectangle {
                        Layout.preferredWidth: 34; Layout.maximumWidth: 34; Layout.preferredHeight: 18; radius: 9
                        color: chartRow.displayed ? Theme.color("#4169e1") : Theme.color("#1f2330"); border.color: chartRow.displayed ? Theme.color("#7895ff") : Theme.color("#596178")
                        Rectangle { width: 14; height: 14; radius: 7; y: 2; x: chartRow.displayed ? 18 : 2; color: chartRow.displayed ? "white" : Theme.color("#747b8d") }
                        TapHandler { cursorShape: Qt.PointingHandCursor; onTapped: { chartRow.displayed = !chartRow.displayed; root.toggleChart(chartRow.modelData.key, chartRow.displayed) } }
                    }
                }
                MouseArea {
                    id: chartMouse; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: 50
                    anchors.rightMargin: 105; hoverEnabled: true
                    onWheel: function(wheel) { wheel.accepted = false }
                    onClicked: root.toggleExpandedChart(chartRow.modelData.key)
                    ToolTip.visible: containsMouse
                    ToolTip.text: chartRow.modelData.title + "\n" + chartRow.modelData.seriesNames.join("\n")
                    ToolTip.delay: 500
                }
                Column {
                    id: metricDetails; x: 8; y: 50; width: parent.width - 16
                    visible: root.expandedChart === chartRow.modelData.key
                    Repeater {
                        model: chartRow.modelData.metrics
                        MetricCheckBox {
                            id: metricCheck
                            required property var modelData
                            objectName: "metricCheck:" + modelData.key
                            width: metricDetails.width; height: 40
                            checked: modelData.enabled
                            enabled: modelData.selectable || checked
                            text: modelData.title + " [" + modelData.unit + "]" + (modelData.status ? " · " + modelData.status : "")
                            font.pixelSize: 10
                            palette.windowText: Theme.color("#d2daee")
                            contentItem: Text {
                                text: metricCheck.text; font: metricCheck.font
                                color: metricCheck.enabled ? Theme.color("#d2daee") : Theme.color("#747b8d")
                                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                                leftPadding: metricCheck.indicator.width + metricCheck.spacing
                            }
                            ControlHint { targetControl: metricCheck; text: metricCheck.modelData.reason || (metricCheck.enabled ? "点击选择此指标" : "当前设备或采集来源不支持此指标。") }
                            onClicked: {
                                // Updating selection rebuilds this delegate. Capture
                                // values before the first call destroys its context.
                                var key = modelData.key
                                var groupKey = chartRow.modelData.key
                                var selected = checked
                                root.setMetricEnabled(key, selected, groupKey)
                            }
                        }
                    }
                }
            }
        }
    }
}
