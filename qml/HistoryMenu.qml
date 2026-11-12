import QtQuick
import QtQuick.Controls

Menu {
    width: 220
    padding: 6
    palette.text: Theme.text
    palette.window: Theme.sidebar
    background: Rectangle {
        radius: 10; color: Theme.sidebar; border.color: Theme.border
    }
}
