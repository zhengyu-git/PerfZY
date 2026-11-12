import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AppDialog {
    id: help
    objectName: "studioHelp"
    property var guides: []
    width: Math.min(820,parent ? parent.width-48 : 820)
    height: Math.min(650,parent ? parent.height-48 : 650)
    anchors.centerIn: parent
    modal: true; focus: true; padding: 16
    title: "六项工具 · 从哪里开始、怎么操作"
    onOpened: scroll.contentItem.contentY = 0
    palette.window: Theme.color("#222838"); palette.base: Theme.color("#182032"); palette.text: Theme.color("#dce6fa")
    palette.buttonText: Theme.color("#dce6fa"); palette.button: Theme.color("#35415a")
    palette.windowText: Theme.color("#dce6fa")
    
    
    contentItem: ScrollView {
        id: scroll
        objectName: "studioHelpScroll"
        clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: scroll.availableWidth; spacing: 14
            Label {
                Layout.fillWidth: true; color: Theme.color("#e8c58c"); wrapMode: Text.Wrap
                text: "第一次使用：先关闭工作台 → 主界面选择设备和应用 → 勾选内存/CPU等指标 → 开始采集 → 停止并保存 → 再打开工作台，勾选左侧记录。任务编排可直接发起新采集，不必先选历史记录。"
            }
            Repeater {
                model: help.guides
                delegate: Frame {
                    required property var modelData
                    Layout.fillWidth: true; padding: 12
                    background: Rectangle { color: Theme.color("#293448"); radius: 5 }
                    ColumnLayout {
                        anchors.fill: parent; spacing: 8
                        Label { Layout.fillWidth: true; text: modelData.title; color: Theme.color("#e3edff"); font.bold: true; wrapMode: Text.Wrap }
                        Label { Layout.fillWidth: true; text: "准备："+modelData.prepare; color: Theme.color("#e8c58c"); wrapMode: Text.Wrap }
                        Label { Layout.fillWidth: true; text: modelData.steps; color: Theme.color("#ccdcf4"); wrapMode: Text.Wrap }
                        Label { Layout.fillWidth: true; text: "结果："+modelData.result; color: Theme.color("#99c6b0"); wrapMode: Text.Wrap }
                    }
                }
            }
            Label { Layout.fillWidth: true; text: "所有秒数都是相对本条采集开始的时间，不是电脑时钟。没有 FPS 时，可先用内存、CPU做分析和对比；卡顿详情必须等真实帧源恢复，旧记录不能补出逐帧。数据只保存在本副本。"; color: Theme.color("#e8c58c"); wrapMode: Text.Wrap }
        }
    }
    footer: DialogButtonBox {
        CaptureButton { text: "明白了，返回操作"; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        onAccepted: help.close()
    }
}
