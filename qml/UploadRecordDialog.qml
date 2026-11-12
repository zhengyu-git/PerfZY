import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AppDialog {
    id: dialog
    objectName: "recordUploadDialog"
    property var bridge: null
    property string otherDestinationLabel: ""
    property string otherDestinationActivity: ""
    readonly property bool busy: bridge ? bridge.busy : false
    readonly property bool custom: !otherDestinationLabel || destination.currentIndex === 0
    property string preferredRecordId: ""
    readonly property var recordOptions: appController.sessionRecords.map(function(record) {
        return {id: record.id, label: [record.name || record.application || record.id, record.duration]
            .filter(Boolean).join("  ·  ")}
    })
    readonly property string selectedId: records.currentIndex >= 0 ? preferredRecordId : ""
    readonly property bool currentResult: !!bridge && selectedId.length > 0 && bridge.recordId === selectedId
    signal settingsRequested()
    signal otherDestinationRequested(string recordId)

    title: "上传记录"
    width: Math.min(520, parent.width - 40)

    function openFor(recordId) {
        if (!bridge) return
        preferredRecordId = busy ? bridge.recordId : recordId || preferredRecordId || bridge.recordId
        destination.currentIndex = 0
        includeScreenshots.checked = false
        open()
    }

    function openSettings() {
        close()
        settingsRequested()
    }

    contentItem: ColumnLayout {
        spacing: 12
        ColumnLayout {
            visible: dialog.otherDestinationLabel.length > 0
            Layout.fillWidth: true; spacing: 6
            SidebarSectionHeading { text: "上传目标" }
            CaptureComboBox {
                id: destination
                objectName: "recordUploadDestination"
                Layout.fillWidth: true; Layout.preferredHeight: 34
                model: dialog.otherDestinationLabel ? ["自定义服务", dialog.otherDestinationLabel] : ["自定义服务"]
                enabled: !dialog.busy
                Accessible.name: "上传目标"
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 6
            SidebarSectionHeading { text: "采集记录" }
            CaptureComboBox {
                id: records
                objectName: "recordUploadRecord"
                Layout.fillWidth: true; Layout.preferredHeight: 34
                model: dialog.recordOptions
                textRole: "label"; valueRole: "id"
                currentIndex: dialog.recordOptions.findIndex(function(row) { return row.id === dialog.preferredRecordId })
                onActivated: function(index) { dialog.preferredRecordId = dialog.recordOptions[index].id }
                placeholderText: count > 0 ? "请选择一条记录" : "暂无采集记录"
                enabled: !dialog.busy && count > 0
                Accessible.name: "采集记录"
            }
        }
        ColumnLayout {
            visible: dialog.custom
            Layout.fillWidth: true; spacing: 8
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                ElidedLabel {
                    id: endpointLabel
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    fullText: dialog.bridge && dialog.bridge.endpoint
                        ? dialog.bridge.endpoint : "请先设置自定义服务的接收地址"
                    color: Theme.muted; font.pixelSize: 12
                }
                ControlHint { targetControl: endpointLabel; text: endpointLabel.contentElided ? endpointLabel.fullText : "" }
                HistoryActionButton {
                    objectName: "recordUploadOpenSettings"
                    Layout.preferredWidth: 94; Layout.preferredHeight: 32
                    text: "上传设置"; glyph: "settings"; enabled: !dialog.busy
                    onClicked: dialog.openSettings()
                }
            }
            CheckBox {
                id: includeScreenshots
                objectName: "recordUploadIncludeScreenshots"
                text: "同时上传截图"; enabled: !dialog.busy
                font.pixelSize: 12
            }
            Text {
                Layout.fillWidth: true
                text: "上传内容包括设备、应用、指标时间序列和场景。"
                color: Theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap
            }
        }
        Text {
            Layout.fillWidth: true
            visible: !dialog.custom && dialog.otherDestinationActivity.length > 0
            text: dialog.otherDestinationLabel + " · " + dialog.otherDestinationActivity
            color: Theme.muted; font.pixelSize: 12; textFormat: Text.PlainText
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: status.implicitHeight + 20
            visible: dialog.custom && dialog.currentResult && !!dialog.bridge.message
            radius: 8; color: Theme.paneAlt
            ColumnLayout {
                id: status
                anchors.fill: parent; anchors.margins: 10; spacing: 4
                Text {
                    objectName: "recordUploadStatus"
                    Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    text: dialog.bridge ? dialog.bridge.message : ""
                    color: dialog.bridge && dialog.bridge.failed ? Theme.red : Theme.text
                    font.pixelSize: 12
                }
                Text {
                    objectName: "recordUploadReceiptId"
                    Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    visible: !!dialog.bridge && !!dialog.bridge.result.record_id
                    text: visible ? "服务端记录  " + dialog.bridge.result.record_id : ""
                    color: Theme.muted; font.pixelSize: 11
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.topMargin: 4; spacing: 8
            Item { Layout.fillWidth: true }
            CaptureButton { text: "关闭"; onClicked: dialog.close() }
            CaptureButton {
                objectName: "recordUploadOpenReport"
                visible: dialog.custom && dialog.currentResult && !!dialog.bridge.result.report_url
                text: "打开报告"; onClicked: dialog.bridge.openReport()
            }
            CaptureButton {
                objectName: "recordUploadSubmit"
                tone: "primary"
                text: dialog.busy ? "上传中…" : !dialog.custom ? "打开 " + dialog.otherDestinationLabel
                    : dialog.currentResult && dialog.bridge.result.record_id ? "再次上传" : "确认上传"
                enabled: dialog.selectedId.length > 0 && !dialog.busy
                    && (!dialog.custom || (dialog.bridge && dialog.bridge.endpoint.length > 0))
                disabledReason: dialog.selectedId.length === 0 ? "请先选择一条采集记录。" : dialog.busy
                    ? "正在上传，请等待服务响应。" : "请先填写自定义服务的接收地址。"
                onClicked: {
                    if (dialog.custom) {
                        dialog.bridge.startUpload(dialog.selectedId, includeScreenshots.checked)
                    } else {
                        dialog.close()
                        dialog.otherDestinationRequested(dialog.selectedId)
                    }
                }
            }
        }
    }
}
