import QtQuick
import QtQuick.Layouts

Column {
    id: table
    required property var device
    readonly property var graphicsRows: {
        var description = String(device.gpu || "")
        var adapters = device.platform === "Windows 本机" ? description.split(" / ") : [description]
        return adapters.map(function(value, index) {
            return [index === 0 ? "图形处理器" : "图形处理器 " + (index + 1), value]
        })
    }
    property var candidates: [
        ["设备名称", device.name || ""], ["设备类型", device.platform || ""],
        ["设备型号", device.model || ""], ["操作系统", device.os || ""],
        ["处理器", device.cpu || ""]
    ].concat(graphicsRows, [
        ["屏幕分辨率", device.resolution || ""], ["设备内存", device.memory || ""]
    ])
    property var rows: candidates.filter(function(row) { return String(row[1]).trim().length > 0 })
    function valueParts(key, value) {
        var full = String(value)
        var match = key === "处理器" || key.indexOf("图形处理器") === 0
            ? /^(.*?)\s*[（(]([^()（）]+)[）)]\s*$/.exec(full) : null
        return match && match[1].trim().length
            ? {primary: match[1].trim(), secondary: match[2].trim()}
            : {primary: full, secondary: ""}
    }
    Repeater {
        model: table.rows
        Item {
            id: row
            required property var modelData
            required property int index
            objectName: "deviceInfoRow:" + modelData[0]
            readonly property string rawValue: String(modelData[1])
            readonly property var parts: table.valueParts(modelData[0], rawValue)
            width: table.width
            height: Math.max(32, valueColumn.implicitHeight + 14)
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14; anchors.rightMargin: 14
                anchors.topMargin: 7; anchors.bottomMargin: 7
                spacing: 10
                Text {
                    objectName: "deviceInfoKey:" + row.modelData[0]
                    Layout.preferredWidth: 72; Layout.alignment: Qt.AlignTop
                    text: row.modelData[0]; color: Theme.muted; font.pixelSize: 11
                    lineHeight: 18; lineHeightMode: Text.FixedHeight
                    verticalAlignment: Text.AlignVCenter
                }
                ColumnLayout {
                    id: valueColumn
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.alignment: Qt.AlignTop
                    spacing: 2
                    ElidedLabel {
                        id: primary
                        objectName: "deviceInfoValue:" + row.modelData[0]
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        Layout.minimumHeight: 18; Layout.maximumHeight: 18
                        fullText: row.parts.primary
                        color: Theme.text; font.pixelSize: 12
                    }
                    ElidedLabel {
                        objectName: "deviceInfoSecondary:" + row.modelData[0]
                        visible: row.parts.secondary.length > 0
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        Layout.minimumHeight: 16; Layout.maximumHeight: 16
                        fullText: row.parts.secondary
                        color: Theme.muted; font.pixelSize: 11
                    }
                }
            }
            ControlHint {
                targetControl: row
                text: primary.contentElided || row.parts.secondary.length > 0
                    ? row.modelData[0] + "：" + row.rawValue : ""
            }
        }
    }
}
