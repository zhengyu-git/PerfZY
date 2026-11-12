import QtQuick
import QtQuick.Layouts

Rectangle {
    id: badge
    property string connectionType: "unknown"
    readonly property string text: ({usb:"USB", network:"Wi-Fi", local:"Local", emulator:"Emulator", adb:"ADB"})[connectionType] || "已连接"
    readonly property color foreground: connectionType === "usb" ? Theme.blue
        : connectionType === "network" ? Theme.green : Theme.muted
    implicitWidth: Math.max(44, Math.ceil(label.implicitWidth) + 12)
    implicitHeight: 20
    Layout.fillWidth: false; Layout.fillHeight: false
    Layout.minimumHeight: implicitHeight; Layout.maximumHeight: implicitHeight
    radius: 6; color: Theme.paneAlt; border.color: Qt.rgba(foreground.r,foreground.g,foreground.b,0.35)
    Accessible.role: Accessible.StaticText
    Accessible.name: "连接方式：" + text
    Text { id: label; anchors.centerIn: parent; text: badge.text; color: badge.foreground; font.pixelSize: 10 }
}
