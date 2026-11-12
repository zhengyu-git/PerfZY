import QtQuick
import QtQuick.Window

Item {
    id: edges
    required property var hostWindow
    property real cornerGripSize: 14
    visible: hostWindow.visibility !== Window.Maximized && hostWindow.visibility !== Window.FullScreen
    Repeater {
        model: [Qt.TopEdge, Qt.BottomEdge, Qt.LeftEdge, Qt.RightEdge,
            Qt.TopEdge | Qt.LeftEdge, Qt.TopEdge | Qt.RightEdge,
            Qt.BottomEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge]
        delegate: MouseArea {
            required property int modelData
            objectName: "windowResizeEdge:" + modelData
            readonly property bool corner: !!(modelData & (Qt.LeftEdge | Qt.RightEdge)) && !!(modelData & (Qt.TopEdge | Qt.BottomEdge))
            x: modelData & Qt.LeftEdge ? 0 : modelData & Qt.RightEdge ? edges.width - width : 7
            y: modelData & Qt.TopEdge ? 0 : modelData & Qt.BottomEdge ? edges.height - height : 7
            width: modelData & (Qt.LeftEdge | Qt.RightEdge) ? (corner ? edges.cornerGripSize : 6) : edges.width - 14
            height: modelData & (Qt.TopEdge | Qt.BottomEdge) ? (corner ? edges.cornerGripSize : 6) : edges.height - 14
            cursorShape: corner ? (modelData === (Qt.TopEdge | Qt.LeftEdge) || modelData === (Qt.BottomEdge | Qt.RightEdge) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor) : modelData & (Qt.LeftEdge | Qt.RightEdge) ? Qt.SizeHorCursor : Qt.SizeVerCursor
            onPressed: edges.hostWindow.startSystemResize(modelData)
        }
    }
}
