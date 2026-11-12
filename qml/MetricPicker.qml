import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    property var controller: appController
    property bool viewOnly: false
    objectName: "metricPicker"
    property alias showAdvanced: selection.showAdvanced
    property alias showSystem: selection.showSystem
    property alias expandedChart: selection.expandedChart
    width: 360; height: 620
    padding: 12
    modal: false; focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
    onAboutToHide: selection.rememberChartListPosition()
    onOpened: selection.queuePositionRestore()
    background: Rectangle { radius: 16; color: Theme.content; border.color: Theme.border; antialiasing: true }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            IconGlyph { Layout.preferredWidth: 20; Layout.maximumWidth: 20; Layout.preferredHeight: 20; name: "sliders"; tint: Theme.blue }
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
                Text { Layout.fillWidth: true; text: "性能指标"; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                Text { Layout.fillWidth: true; text: popup.viewOnly ? "记录指标 · 图表显示 · 排序" : "采集指标 · 图表显示 · 排序"; color: Theme.muted; font.pixelSize: 10 }
            }
            NavButton { objectName: "closeMetricPickerButton"; Layout.preferredWidth: 28; Layout.maximumWidth: 28; Layout.preferredHeight: 28; glyph: "close"; tip: "收起性能指标"; onClicked: popup.close() }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
        MetricSelectionPanel { id: selection; controller: popup.controller; viewOnly: popup.viewOnly; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true }
    }
}
