import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    objectName: "recordUploadSettings"
    property var bridge: null
    property string feedback: ""
    property bool saved: false
    color: Theme.content; border.color: Theme.border; radius: 12
    implicitHeight: fields.implicitHeight + 40

    onVisibleChanged: if (visible && bridge) {
        endpointField.text = bridge.endpoint
        tokenField.clear()
        feedback = ""
    }

    ColumnLayout {
        id: fields
        anchors.fill: parent; anchors.margins: 20; spacing: 9
        SidebarSectionHeading { text: "接收地址" }
        CaptureTextField {
            id: endpointField
            objectName: "recordUploadEndpoint"
            Layout.fillWidth: true
            placeholderText: "https://example.com/api/v1/records"
            Accessible.name: "接收地址"
            inputMethodHints: Qt.ImhUrlCharactersOnly
            selectByMouse: true; maximumLength: 4096
            onTextEdited: panel.feedback = ""
        }
        SidebarSectionHeading { text: "Token（可选）"; Layout.topMargin: 5 }
        CaptureTextField {
            id: tokenField
            objectName: "recordUploadToken"
            Layout.fillWidth: true
            placeholderText: panel.bridge && panel.bridge.hasToken ? "本次运行已设置" : "输入服务的访问 Token"
            Accessible.name: "Token"
            echoMode: TextInput.Password
            inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
            maximumLength: 4096
            onTextEdited: panel.feedback = ""
        }
        Text {
            Layout.fillWidth: true; wrapMode: Text.Wrap
            text: "地址会保存。Token 在本次运行内有效，修改地址时请填写对应服务的 Token。"
            color: Theme.muted; font.pixelSize: 12
        }
        RowLayout {
            Layout.fillWidth: true; Layout.topMargin: 4; spacing: 8
            Text {
                objectName: "recordUploadSettingsStatus"
                Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.Wrap
                text: panel.feedback; textFormat: Text.PlainText
                color: panel.saved ? Theme.green : Theme.red; font.pixelSize: 12
            }
            CaptureButton {
                text: "清除 Token"
                enabled: !!panel.bridge && !panel.bridge.busy
                visible: !!panel.bridge && panel.bridge.hasToken
                onClicked: {
                    panel.bridge.clearToken()
                    tokenField.clear()
                    panel.saved = true
                    panel.feedback = "Token 已清除。"
                }
            }
            CaptureButton {
                objectName: "recordUploadSaveSettings"
                text: "保存"; tone: "primary"
                enabled: !!panel.bridge && !panel.bridge.busy
                onClicked: {
                    panel.saved = panel.bridge.saveSettings(endpointField.text, tokenField.text)
                    panel.feedback = panel.saved ? "上传设置已保存。" : panel.bridge.settingsError
                    if (panel.saved) {
                        endpointField.text = panel.bridge.endpoint
                        tokenField.clear()
                    }
                }
            }
        }
    }
}
