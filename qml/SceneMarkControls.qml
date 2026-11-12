import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: control
    objectName: "sceneMarkControls"
    property var hostWindow
    property bool modalBlocked: false
    property bool pendingExit: false
    signal stopApproved(bool exiting)
    width: 0; height: 0

    function requestStop(exiting) {
        if (!appController.collecting) return
        pendingExit = exiting
        if (appController.markingScene) pendingDialog.open()
        else stopApproved(exiting)
    }
    function mark() {
        return appController.toggleSceneMark("")
    }
    function focusSafe() {
        var item = hostWindow ? hostWindow.activeFocusItem : null
        while (item) {
            // Text fields and popup/overlay focus must never mark a scene.
            if (typeof item.cursorPosition !== "undefined") return false
            if (item === hostWindow.contentItem) return true
            item = item.parent
        }
        return false
    }
    Shortcut {
        objectName: "sceneMarkShortcut"
        sequence: "Ctrl+Alt+M"
        context: Qt.WindowShortcut
        enabled: control.hostWindow && control.hostWindow.active && appController.collecting
                 && !appController.paused && !control.modalBlocked && !pendingDialog.visible && control.focusSafe()
        onActivated: control.mark()
    }
    Connections {
        target: appController
        function onStateChanged() { if (!appController.collecting) pendingDialog.close() }
    }
    AppDialog {
        id: pendingDialog
        objectName: "pendingSceneStopDialog"
        property string errorText: ""
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(480, parent ? parent.width - 32 : 480); height: 245
        modal: true; title: "场景标记尚未结束"; standardButtons: Dialog.NoButton
        palette.text: Theme.color("#dce6fa"); palette.buttonText: Theme.color("#dce6fa")
        
        closePolicy: Popup.CloseOnEscape
        onOpened: errorText = ""
        
        contentItem: ColumnLayout {
            spacing: 12
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "“" + appController.markingSceneName + "”仍在标记。可补齐到当前采集时间后保存，或只舍弃这段未结束标记。"
                color: Theme.color("#dce6fa"); font.pixelSize: 12
            }
            Text { Layout.fillWidth: true; text: pendingDialog.errorText; visible: text.length > 0; color: Theme.chartWarning; wrapMode: Text.WordWrap; font.pixelSize: 12 }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                CaptureButton { objectName: "pendingSceneCancelButton"; text: "取消"; Layout.preferredWidth: 60; onClicked: pendingDialog.close() }
                Item { Layout.fillWidth: true }
                CaptureButton {
                    objectName: "pendingSceneDiscardButton"
                    text: "舍弃标记并停止"; Layout.preferredWidth: 120
                    onClicked: {
                        appController.discardSceneMark()
                        pendingDialog.close()
                        control.stopApproved(control.pendingExit)
                    }
                }
                CaptureButton { tone: "primary";
                    objectName: "pendingSceneSaveButton"
                    text: "补齐标记并停止"; Layout.preferredWidth: 120; accentColor: Theme.primaryFill
                    onClicked: {
                        if (!appController.finishSceneMark()) {
                            pendingDialog.errorText = "这段标记尚无有效时长，请稍后重试，或选择舍弃标记。"
                            return
                        }
                        pendingDialog.close()
                        control.stopApproved(control.pendingExit)
                    }
                }
            }
        }
    }
}
