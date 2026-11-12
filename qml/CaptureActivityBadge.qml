import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: badge
    objectName: "captureActivityBadge"
    readonly property bool paused: appController.collecting && appController.paused
    readonly property color statusColor: appController.collectionStarting || paused ? Theme.chartWarning : Theme.green
    readonly property string elapsed: {
        var seconds = Math.max(0,Math.floor(appController.duration))
        return String(Math.floor(seconds/60)).padStart(2,"0")+":"+String(seconds%60).padStart(2,"0")
    }
    visible: appController.collecting || appController.collectionStarting
    height: 27; implicitWidth: contentItem.implicitWidth+20
    leftPadding: 10; rightPadding: 10
    Accessible.name: (appController.collectionStarting ? "正在连接采集器" : paused ? "采集已暂停" : "正在采集") + " " + elapsed
    contentItem: RowLayout {
        spacing: 7
        Rectangle { Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3; color: badge.statusColor }
        Text { text: appController.collectionStarting ? "正在连接" : badge.paused ? "采集已暂停" : "正在采集"; color: badge.statusColor; font.pixelSize: 12 }
        Text { visible: !appController.collectionStarting; text: badge.elapsed; color: badge.statusColor; font.pixelSize: 12; font.family: Theme.numberFont }
    }
    background: Rectangle {
        radius: 8
        color: Qt.rgba(badge.statusColor.r,badge.statusColor.g,badge.statusColor.b,badge.hovered ? .16 : .09)
    }
    ControlHint { targetControl: badge; text: "点击返回常规测试" }
}
