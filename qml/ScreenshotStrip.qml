import QtQuick
import QtQuick.Controls

Rectangle {
    id: pane
    objectName: "captureScreenshotStrip"
    property var shots: []
    property int selectedIndex: -1
    property string noticeText: ""
    property bool showGap: false
    readonly property int preferredStripHeight: shots.length > 0 ? 132 : noticeText.length > 0 ? 44 : 0
    readonly property int noticeHeight: !showGap && shots.length > 0 && noticeText.length > 0 ? 24 : 0
    color: Theme.content
    clip: true

    Item {
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 5
        height: 18
        visible: pane.noticeHeight > 0
        IconGlyph { id: noticeIcon; width: 15; height: 15; anchors.verticalCenter: parent.verticalCenter; name: "capture"; tint: Theme.muted }
        Text {
            objectName: "screenshotCaptureNotice"
            anchors.left: noticeIcon.right; anchors.leftMargin: 6; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            text: pane.noticeText; color: Theme.muted; font.family: Theme.fontFamily; font.pixelSize: 11; elide: Text.ElideRight
        }
    }
    Row {
        objectName: "screenshotGapPlaceholder"
        anchors.centerIn: parent
        spacing: 8
        visible: pane.showGap || pane.shots.length === 0
        IconGlyph { width: 18; height: 18; name: "capture"; tint: Theme.muted }
        Text { text: pane.noticeText || "该时段暂无截图"; color: Theme.muted; font.family: Theme.fontFamily; font.pixelSize: 12 }
    }

    function positionViewAtIndex(index, mode) {
        if (index >= 0 && index < screenshotList.count)
            screenshotList.positionViewAtIndex(index, mode)
    }

    ListView {
        id: screenshotList
        objectName: "captureScreenshotList"
        anchors.fill: parent
        anchors.leftMargin: 8
        anchors.rightMargin: 8
        anchors.topMargin: 7 + pane.noticeHeight
        // Keep thumbnails and their labels above the scrollbar's own lane.
        anchors.bottomMargin: 20
        orientation: ListView.Horizontal
        spacing: 6
        clip: true
        visible: !pane.showGap && pane.shots.length > 0
        model: pane.shots
        currentIndex: pane.selectedIndex
        highlight: null
        delegate: Rectangle {
            id: thumbnail
            objectName: "captureScreenshotThumbnail"
            required property var modelData
            required property int index
            property real ratio: image.status === Image.Ready && image.sourceSize.height > 0
                                 ? image.sourceSize.width / image.sourceSize.height : 16 / 9
            height: screenshotList.height
            width: Math.max(60, Math.min(190, (height - 24) * ratio + 6))
            radius: 6
            color: Theme.chartBackground
            border.width: index === pane.selectedIndex ? 1.5 : 1
            border.color: index === pane.selectedIndex ? Theme.blue : Theme.border
            Image {
                id: image
                objectName: "captureScreenshotImage"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 3
                height: Math.max(0, parent.height - 25)
                source: thumbnail.modelData.image
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: true
            }
            Text {
                objectName: "captureScreenshotTime"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 3
                height: 17
                text: thumbnail.modelData.time
                color: Theme.muted
                font.pixelSize: 10
                font.family: Theme.fontFamily
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
        ScrollBar.horizontal: ScrollBar {
            id: scrollBar
            objectName: "captureScreenshotScrollBar"
            parent: pane
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 10
            anchors.rightMargin: 10
            anchors.bottomMargin: 6
            height: 8
            padding: 1
            policy: ScrollBar.AlwaysOn
            visible: screenshotList.visible
            contentItem: Rectangle {
                radius: height / 2
                color: scrollBar.pressed ? Theme.blue : Theme.muted
                opacity: scrollBar.hovered || scrollBar.pressed ? .75 : .4
            }
            background: Rectangle {
                x: scrollBar.leftPadding
                y: (scrollBar.height - height) / 2
                width: scrollBar.availableWidth
                height: 3
                radius: height / 2
                color: Theme.border
            }
        }
    }
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
    }
}
