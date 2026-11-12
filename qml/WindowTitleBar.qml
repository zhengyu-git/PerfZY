import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Rectangle {
    id: bar
    required property var hostWindow
    signal captureRequested()
    implicitHeight: 40
    color: Theme.header
    function toggleMaximized() {
        if (hostWindow.visibility === Window.Maximized) hostWindow.showNormal()
        else hostWindow.showMaximized()
    }
    Item {
        anchors.left: parent.left; anchors.right: windowButtons.left
        anchors.top: parent.top; anchors.bottom: parent.bottom
        Text {
            anchors.left: parent.left; anchors.leftMargin: 19; anchors.verticalCenter: parent.verticalCenter
            text: "PerfZY"; color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold
        }
        TapHandler { onDoubleTapped: bar.toggleMaximized() }
        DragHandler { target: null; onActiveChanged: if (active) bar.hostWindow.startSystemMove() }
    }
    CaptureActivityBadge {
        anchors.right: windowButtons.left; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
        onClicked: bar.captureRequested()
    }
    Row {
        id: windowButtons
        anchors.right: parent.right; height: parent.height
        Repeater {
            model: ["minimize", "maximize", "close"]
            delegate: ToolButton {
                id: windowButton
                required property string modelData
                objectName: "windowButton:" + modelData
                width: 46; height: bar.height
                Accessible.name: modelData === "close" ? "关闭" : modelData === "minimize" ? "最小化" : "最大化 / 还原"
                contentItem: Item {
                    IconGlyph {
                        anchors.centerIn: parent; width: 16; height: 16
                        name: windowButton.modelData === "maximize" && bar.hostWindow.visibility === Window.Maximized ? "restore" : windowButton.modelData
                        tint: windowButton.modelData === "close" && windowButton.hovered ? "white" : Theme.text
                    }
                }
                background: Rectangle { color: windowButton.hovered ? windowButton.modelData === "close" ? "#c42b36" : Theme.hover : "transparent" }
                onClicked: {
                    if (modelData === "close") bar.hostWindow.close()
                    else if (modelData === "minimize") bar.hostWindow.showMinimized()
                    else bar.toggleMaximized()
                }
            }
        }
    }
}
