import QtQuick
import QtQuick.Controls

ToolButton {
    id: control
    objectName: "metricHealthButton"
    property var statuses: []
    property string details: ""
    readonly property bool dialogVisible: detailDialog.visible
    readonly property bool hasWarning: statuses.some(function(row) {
        return row.state === "missing" || row.state === "stale"
    })
    readonly property string summaryText: statuses.map(function(row) {
        return row.title + " · " + row.label
    }).join("\n")
    implicitWidth: 30; implicitHeight: 30
    padding: 7
    hoverEnabled: true
    onClicked: detailDialog.open()
    contentItem: IconGlyph {
        name: "info"
        tint: control.hasWarning && control.enabled ? Theme.chartWarning : Theme.muted
        opacity: control.enabled ? 1 : .5
    }
    background: Rectangle {
        radius: 8
        color: control.down ? Theme.selected : control.hovered ? Theme.hover : "transparent"
    }
    Rectangle {
        objectName: "metricHealthWarningDot"
        width: 5; height: 5; radius: 2.5
        x: control.width - 7; y: 4
        color: Theme.chartWarning
        visible: control.enabled && control.hasWarning
    }
    ControlHint {
        targetControl: control
        text: !control.enabled ? "采集开始或打开记录后，可查看指标数据状态。"
            : (control.hasWarning ? "部分指标缺失或已过期\n" : "指标数据状态\n")
              + control.summaryText + "\n点击查看来源与缺失说明"
    }
    AppDialog {
        id: detailDialog
        objectName: "metricHealthDetailDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(620, parent ? parent.width - 32 : 620)
        height: Math.min(480, parent ? parent.height - 32 : 480)
        modal: true; title: "指标数据状态"; standardButtons: Dialog.NoButton
        palette.text: Theme.text; palette.buttonText: Theme.text
        
        
        footer: DialogButtonBox {
            objectName: "metricHealthDialogFooter"
            padding: 12; spacing: 8
            onAccepted: detailDialog.accept()
            background: Rectangle { color: "transparent" }
            CaptureButton {
                objectName: "metricHealthConfirmButton"
                text: "确定"; implicitWidth: 76
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
        }
        contentItem: ScrollView {
            id: scroll; clip: true; contentWidth: availableWidth
            Label {
                width: scroll.availableWidth
                text: control.details || control.summaryText
                textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere
                color: Theme.text; font.pixelSize: 12
            }
        }
    }
}
