import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support

// Screensaver settings, laid out like the classic Windows "Screen Saver" tab:
// a little monitor with a live preview, the saver list, Settings / Preview,
// and the wait time. Values come in through HALCYON_SS_* environment vars.
ApplicationWindow {
    id: win
    title: "Screensaver"
    width: 460; height: 560
    visible: true
    color: "#eef6fb"

    function env(name) {
        const xhr = new XMLHttpRequest()
        xhr.open("GET", "file:///proc/self/environ", false)
        xhr.send()
        for (const kv of xhr.responseText.split("\0"))
            if (kv.indexOf(name + "=") === 0) return kv.substring(name.length + 1)
        return ""
    }
    function sh(s) { return "'" + s.replace(/'/g, "'\\''") + "'" }

    P5Support.DataSource {
        id: run
        engine: "executable"
        onNewData: (src) => disconnectSource(src)
    }
    function exec(cmd) { run.connectSource(cmd) }

    ListModel { id: savers }
    property string prefix: env("HALCYON_SS_PREFIX")
    Component.onCompleted: {
        const cur = env("HALCYON_SS_CURRENT")
        for (const line of env("HALCYON_SS_LIST").split("\n")) {
            const f = line.split("\t")
            if (f.length < 2) continue
            savers.append({ path: f[0], name: f[1] })
            if (f[0] === cur) list.currentIndex = savers.count - 1
        }
        if (list.currentIndex < 0) list.currentIndex = 0
        minutes.value = parseInt(env("HALCYON_SS_TIMEOUT")) || 3
        enabled.checked = env("HALCYON_SS_ENABLED") !== "false"
    }
    readonly property string chosen: list.currentIndex >= 0 ? savers.get(list.currentIndex).path : "bubbles"
    readonly property bool isBubbles: chosen === "bubbles"

    function save() {
        exec("kwriteconfig6 --file halcyonrc --group Screensaver --key Saver " + sh(chosen)
             + " && kwriteconfig6 --file halcyonrc --group Screensaver --key Timeout " + minutes.value
             + " && kwriteconfig6 --file halcyonrc --group Screensaver --key Enabled " + (enabled.checked ? "true" : "false")
             + " && halcyon-screensaver --restart-daemon")
    }

    ColumnLayout {
        anchors { fill: parent; margins: 16 }
        spacing: 12

        // ---- little monitor with a live preview ----
        Item {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 260; Layout.preferredHeight: 210
            Rectangle {   // bezel
                id: bezel
                width: 260; height: 180; radius: 10
                gradient: Gradient {
                    GradientStop { position: 0; color: "#e8eef2" }
                    GradientStop { position: 1; color: "#b8c4cc" }
                }
                border.color: "#7a8a96"
                Rectangle {   // screen
                    id: screen
                    anchors { fill: parent; margins: 14 }
                    color: "black"; clip: true
                    border.color: "#405060"
                    Loader {
                        anchors.fill: parent; anchors.margins: 1
                        active: win.isBubbles
                        source: "Bubbles.qml"
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: !win.isBubbles
                        horizontalAlignment: Text.AlignHCenter
                        color: "#9fd8f5"
                        text: list.currentIndex >= 0 ? savers.get(list.currentIndex).name + "\n\n(press Preview)" : ""
                    }
                }
            }
            Rectangle {   // stand
                anchors { horizontalCenter: bezel.horizontalCenter; top: bezel.bottom }
                width: 90; height: 26; radius: 4
                gradient: Gradient {
                    GradientStop { position: 0; color: "#c8d2d8" }
                    GradientStop { position: 1; color: "#8a98a2" }
                }
            }
        }

        Label { text: "Screensaver"; font.bold: true; color: "#0c2e4a" }
        Frame {
            Layout.fillWidth: true; Layout.fillHeight: true
            padding: 2
            ListView {
                id: list
                anchors.fill: parent
                clip: true
                currentIndex: -1
                model: savers
                delegate: ItemDelegate {
                    required property string name
                    required property int index
                    width: ListView.view.width
                    text: name
                    highlighted: ListView.isCurrentItem
                    onClicked: list.currentIndex = index
                    onDoubleClicked: win.exec("halcyon-screensaver --run " + win.sh(win.chosen))
                }
                ScrollBar.vertical: ScrollBar {}
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Button {
                text: "Settings…"
                enabled: !win.isBubbles
                onClicked: win.exec("WINEPREFIX=" + win.sh(win.prefix) + " wine " + win.sh(win.chosen) + " /c")
            }
            Button {
                text: "Preview"
                onClicked: win.exec("halcyon-screensaver --run " + win.sh(win.chosen))
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Add .scr…"
                onClicked: win.exec("halcyon-screensaver --add")
            }
        }

        RowLayout {
            CheckBox { id: enabled; text: "Start after" }
            SpinBox { id: minutes; from: 1; to: 120; enabled: enabled.checked }
            Label { text: "minutes of inactivity"; color: "#0c2e4a" }
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            Button { text: "OK"; highlighted: true; onClicked: { win.save(); quitTimer.start() } }
            Button { text: "Cancel"; onClicked: Qt.quit() }
            Button { text: "Apply"; onClicked: win.save() }
        }
    }
    // Let the save command start before the window goes away.
    Timer { id: quitTimer; interval: 400; onTriggered: Qt.quit() }
}
