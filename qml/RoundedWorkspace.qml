import QtQuick

Item {
    id: frame
    objectName: "roundedWorkspace"
    default property alias content: body.data
    property real radius: 20
    property color exteriorColor: Theme.header

    Item {
        id: body
        anchors.fill: parent
        clip: true
    }
    // Four small corner covers avoid putting the live chart workspace in an
    // offscreen layer just to round its outline. The center stays unobscured.
    Repeater {
        model: 4
        delegate: Canvas {
            id: corner
            required property int index
            property color maskColor: frame.exteriorColor
            width: frame.radius; height: frame.radius
            x: index === 1 || index === 2 ? frame.width - width : 0
            y: index >= 2 ? frame.height - height : 0
            rotation: index * 90
            z: 100
            antialiasing: true
            onMaskColorChanged: requestPaint()
            onWidthChanged: requestPaint()
            onPaint: {
                var c = getContext("2d"), r = width
                c.reset()
                c.beginPath()
                c.moveTo(0, 0)
                c.lineTo(r, 0)
                c.arc(r, r, r, -Math.PI / 2, -Math.PI, true)
                c.closePath()
                c.fillStyle = String(maskColor)
                c.fill()
            }
        }
    }
}
