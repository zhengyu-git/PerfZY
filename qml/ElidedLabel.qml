import QtQuick

Item {
    id: label
    property string fullText: ""
    property alias font: painted.font
    property alias color: painted.color
    readonly property bool truncated: painted.truncated
    readonly property real usableWidth: Math.max(0, width - 1)
    readonly property bool contentElided: fullMetrics.advanceWidth > usableWidth
    implicitWidth: fullMetrics.advanceWidth
    implicitHeight: painted.implicitHeight
    Accessible.role: Accessible.StaticText
    Accessible.name: fullText
    readonly property string text: {
        if (!contentElided) return fullText
        if (usableWidth < dotsMetrics.advanceWidth) return ""
        var value = shortened.elidedText
        return value.endsWith("\u2026") ? value.slice(0, -1) + "..." : value
    }
    Text {
        id: painted
        anchors.fill: parent
        text: label.text
        textFormat: Text.PlainText; wrapMode: Text.NoWrap; maximumLineCount: 1
        verticalAlignment: Text.AlignVCenter
        Accessible.ignored: true
    }
    TextMetrics { id: fullMetrics; font: label.font; text: label.fullText }
    TextMetrics { id: dotsMetrics; font: label.font; text: "..." }
    TextMetrics { id: ellipsisMetrics; font: label.font; text: "\u2026" }
    TextMetrics {
        id: shortened
        font: label.font; text: label.fullText; elide: Qt.ElideRight
        elideWidth: Math.max(0, label.usableWidth - Math.max(0, dotsMetrics.advanceWidth - ellipsisMetrics.advanceWidth))
    }
}
