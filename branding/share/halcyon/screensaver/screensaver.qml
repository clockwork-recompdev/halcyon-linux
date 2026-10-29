import QtQuick
import QtQuick.Window

// Fullscreen window for the Bubbles saver. Any key, click or real mouse
// movement ends it.
Window {
    id: win
    visible: true
    visibility: Window.FullScreen
    color: "black"
    title: "Halcyon Screensaver"
    property point start: Qt.point(-1, -1)

    Bubbles { anchors.fill: parent }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.BlankCursor
        onPositionChanged: (m) => {
            if (win.start.x < 0) { win.start = Qt.point(m.x, m.y); return }
            if (Math.abs(m.x - win.start.x) + Math.abs(m.y - win.start.y) > 12) Qt.quit()
        }
        onPressed: Qt.quit()
        onWheel: Qt.quit()
    }
    Item { focus: true; Keys.onPressed: Qt.quit() }
}
