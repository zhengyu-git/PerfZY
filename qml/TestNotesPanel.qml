import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: notes
    objectName: "testNotesPanel"
    property bool expanded: false
    readonly property string summary: [appController.testBuildLabel, appController.testConditions]
        .filter(function(value) { return String(value).trim().length > 0 }).join(" · ")
    spacing: 6
    Button {
        id: notesToggle
        objectName: "testNotesToggle"
        Layout.fillWidth: true; Layout.preferredHeight: 26
        leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
        Accessible.name: "测试备注（可选），" + (notes.expanded ? "收起" : "展开")
        onClicked: { forceActiveFocus(); notes.expanded = !notes.expanded }
        background: Rectangle { radius: 6; color: notesToggle.hovered ? Theme.hover : "transparent" }
        contentItem: RowLayout {
            spacing: 6
            SidebarSectionHeading { objectName: "testNotesHeading"; text: "测试备注" }
            Text { text: "可选"; color: Theme.muted; font.pixelSize: 10 }
            Item { Layout.fillWidth: true }
            IconGlyph { Layout.preferredWidth: 14; Layout.maximumWidth: 14; Layout.preferredHeight: 14; name: notes.expanded ? "chevronUp" : "chevronDown"; tint: Theme.muted }
        }
    }
    Text {
        objectName: "testNotesSummary"
        Layout.fillWidth: true
        visible: !notes.expanded
        text: notes.summary || "记录版本、画质与路线，便于对比"
        textFormat: Text.PlainText
        color: Theme.muted; font.pixelSize: 11; elide: Text.ElideRight
        HoverHandler { id: summaryHover }
        ToolTip.visible: summaryHover.hovered && notes.summary.length > 0
        ToolTip.text: notes.summary
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: notes.expanded
        spacing: 8
    CaptureTextField {
        objectName: "testBuildLabelField"
        Layout.fillWidth: true; Layout.preferredHeight: 34
        placeholderText: "测试版本，例如 1.2.0 / 优化前"; text: appController.testBuildLabel
        horizontalAlignment: Text.AlignLeft
        toolTipText: "可选：为下一次新采集记录注明游戏版本或优化阶段"
        disabledReason: "采集中测试备注已锁定，请停止采集后修改。"
        maximumLength: 120; enabled: !appController.collecting && !appController.collectionStarting
        onEditingFinished: appController.setTestContext(text, conditionsField.text)
    }
    CaptureTextField {
        id: conditionsField; objectName: "testConditionsField"
        Layout.fillWidth: true; Layout.preferredHeight: 34
        placeholderText: "画质 / 分辨率 / 帧率上限 / 路线"; text: appController.testConditions
        horizontalAlignment: Text.AlignLeft
        toolTipText: "可选：记录测试条件，便于前后对比；此处不会修改游戏设置"
        disabledReason: "采集中测试备注已锁定，请停止采集后修改。"
        maximumLength: 300; enabled: !appController.collecting && !appController.collectionStarting
        onEditingFinished: appController.setTestContext(appController.testBuildLabel, text)
    }
    Text {
        Layout.fillWidth: true
        text: "随下一次新采集记录保存，便于核对测试条件；不会修改游戏设置。"
        color: Theme.color("#a3b0c9"); font.pixelSize: 11; wrapMode: Text.WordWrap
    }
    }
}
