import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: page
    objectName: "settingsPage"
    property string section: "appearance"
    property var uploader: null
    readonly property bool connectionDialogVisible: settingsPanel.connectionDialogVisible
    readonly property var sections: [
        {key: "appearance", title: "外观", icon: "sun", description: "选择界面的显示模式。"},
        {key: "capture", title: "采集与截图", icon: "capture", description: "管理采样间隔、截图和设备相关的采集选项。"},
        {key: "report", title: "报告导出", icon: "report", description: "设置本地报告的导出选项。"},
        {key: "upload", title: "上传设置", icon: "upload", description: "配置上传记录时使用的自定义服务。"},
        {key: "diagnostic", title: "连接诊断", icon: "connection", description: "检查设备连接、采集组件和权限。"}
    ]
    readonly property var currentSection: sections.filter(function(item) { return item.key === page.section })[0] || sections[0]
    color: Theme.window
    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.preferredWidth: 218; Layout.fillHeight: true; color: Theme.sidebar
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; spacing: 5
                Text { text: "设置"; color: Theme.text; font.pixelSize: 17; font.weight: Font.DemiBold; Layout.margins: 10; Layout.bottomMargin: 24 }
                Repeater {
                    model: page.sections
                    delegate: Button {
                        id: category
                        required property var modelData
                        objectName: "settingsSection:" + modelData.key
                        Layout.fillWidth: true; Layout.preferredHeight: 40
                        onClicked: page.section = modelData.key
                        background: Rectangle { radius: 8; color: page.section === category.modelData.key ? Theme.hover : category.hovered ? Theme.paneAlt : "transparent" }
                        contentItem: RowLayout {
                            spacing: 12
                            IconGlyph { Layout.leftMargin: 10; width: 18; height: 18; name: category.modelData.icon; tint: Theme.text }
                            Text { Layout.fillWidth: true; text: category.modelData.title; color: Theme.text; font.pixelSize: 13 }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
                Text { Layout.margins: 10; visible: page.section !== "upload"; text: "设置会自动保存"; color: Theme.muted; font.pixelSize: 11 }
            }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.margins: 34; spacing: 14
            Text { text: page.currentSection.title; color: Theme.text; font.pixelSize: 25; font.weight: Font.DemiBold; Layout.topMargin: 8 }
            Text { Layout.fillWidth: true; text: page.currentSection.description; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.bottomMargin: 14 }
            Rectangle {
                Layout.fillWidth: true; Layout.maximumWidth: 820
                Layout.preferredHeight: 212; visible: page.section === "appearance"
                radius: 12; color: Theme.content; border.color: Theme.border
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 16
                    Text { text: "显示模式"; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    RowLayout {
                        spacing: 16
                        Repeater {
                            model: [{key: "light", title: "浅色", glyph: "sun"}, {key: "dark", title: "深色", glyph: "moon"}]
                            delegate: Button {
                                id: themeButton
                                required property var modelData
                                objectName: "themeButton:" + modelData.key
                                Layout.preferredWidth: 170; Layout.preferredHeight: 106
                                onClicked: Theme.setMode(modelData.key)
                                background: Rectangle { radius: 9; color: Theme.window; border.color: Theme.mode === themeButton.modelData.key ? Theme.blue : Theme.border; border.width: Theme.mode === themeButton.modelData.key ? 2 : 1 }
                                IconGlyph { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; width: 15; height: 15; name: "check"; tint: Theme.blue; visible: Theme.mode === themeButton.modelData.key; z: 2 }
                                contentItem: ColumnLayout {
                                    spacing: 10
                                    IconGlyph { Layout.alignment: Qt.AlignHCenter; width: 28; height: 28; name: themeButton.modelData.glyph; tint: Theme.mode === themeButton.modelData.key ? Theme.blue : Theme.muted }
                                    Text { Layout.alignment: Qt.AlignHCenter; text: themeButton.modelData.title; color: Theme.text; font.pixelSize: 13 }
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Text { text: "切换后立即生效，下次启动保留你的选择。"; color: Theme.muted; font.pixelSize: 11 }
                }
            }
            SettingsPanel {
                id: settingsPanel
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.maximumWidth: 820
                visible: ["capture", "report", "diagnostic"].indexOf(page.section) >= 0
                section: page.section
            }
            UploadSettingsPanel {
                Layout.fillWidth: true; Layout.maximumWidth: 820
                bridge: page.uploader
                visible: page.section === "upload"
            }
            Item { Layout.fillHeight: true; visible: page.section === "appearance" || page.section === "upload" }
        }
    }
}
