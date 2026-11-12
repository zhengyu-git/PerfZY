import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ListView {
    id: view
    property var rows: []
    property var formatRow: function(row) { return JSON.stringify(row) }
    property string actionText: ""
    property string secondaryActionText: ""
    property string emptyText: "暂无结果；请按上方操作步骤选择记录并开始分析。"
    signal activated(int rowIndex, var row)
    signal secondaryActivated(int rowIndex, var row)
    model: rows
    clip: true
    spacing: 5
    ScrollBar.vertical: ScrollBar {}
    delegate: Rectangle {
        required property var modelData
        required property int index
        width: view.width-12
        height: Math.max(62, rowText.implicitHeight+18)
        radius: 4; color: index%2 ? Theme.color("#293347") : Theme.color("#242e42")
        RowLayout {
            anchors.fill: parent; anchors.margins: 8
            Text {
                id: rowText; Layout.fillWidth: true
                text: view.formatRow(modelData); textFormat: Text.PlainText
                color: Theme.color("#cfdaef"); font.pixelSize: 11
                wrapMode: Text.Wrap; maximumLineCount: 5; elide: Text.ElideRight
            }
            Button { visible: view.actionText !== ""; text: view.actionText; onClicked: view.activated(index,modelData) }
            Button { visible: view.secondaryActionText !== ""; text: view.secondaryActionText; onClicked: view.secondaryActivated(index,modelData) }
        }
    }
    Text { anchors.centerIn: parent; width: Math.max(0,parent.width-24); visible: view.count===0; text: view.emptyText; color: Theme.color("#a7bad8"); font.pixelSize: 11; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; textFormat: Text.PlainText }
}
