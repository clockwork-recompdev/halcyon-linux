import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import org.kde.kirigami as Kirigami

// Halcyon AutoPlay chooser. Options come from HALCYON_AP_* environment
// variables set by halcyon-autoplay-dialog. The choice is returned as the exit
// code (10 + option index, +100 for "always"); 0 means closed/cancelled.
ApplicationWindow {
    id: win
    title: "AutoPlay"
    width: 460
    height: col.implicitHeight + 40
    visible: true
    color: "transparent"
    flags: Qt.Dialog | Qt.WindowStaysOnTopHint

    readonly property string discTitle: env("HALCYON_AP_TITLE")
    readonly property string what: env("HALCYON_AP_WHAT")
    readonly property string iconSource: env("HALCYON_AP_ICON")

    function env(name) {
        // QML has no getenv; read this process's environment from /proc.
        var xhr = new XMLHttpRequest()
        xhr.open("GET", "file:///proc/self/environ", false)
        xhr.send()
        var vars = xhr.responseText.split("\0")
        for (var i = 0; i < vars.length; i++)
            if (vars[i].indexOf(name + "=") === 0) return vars[i].substring(name.length + 1)
        return ""
    }

    function choose(index) {
        Qt.exit((always.checked ? 110 : 10) + index)
    }

    ListModel { id: options }
    Component.onCompleted: {
        env("HALCYON_AP_OPTIONS").split("\n").forEach(function (line) {
            var f = line.split("\t")
            if (f.length >= 3) options.append({ oicon: f[1], olabel: f[2], odesc: f[3] || "" })
        })
    }

    Shortcut { sequence: "Escape"; onActivated: Qt.quit() }

    Rectangle {
        anchors.fill: parent
        radius: 10
        border.color: "#cc0b3f66"
        gradient: Gradient {
            GradientStop { position: 0.0;  color: "#f4fbff" }
            GradientStop { position: 0.12; color: "#cdeeff" }
            GradientStop { position: 0.13; color: "#9fd8f5" }
            GradientStop { position: 0.2;  color: "#e8f6fd" }
            GradientStop { position: 1.0;  color: "#f6fbfe" }
        }

        ColumnLayout {
            id: col
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20 }
            spacing: 12

            RowLayout {
                spacing: 14
                Kirigami.Icon {
                    source: win.iconSource
                    Layout.preferredWidth: 56; Layout.preferredHeight: 56
                    Layout.minimumWidth: 56; Layout.maximumWidth: 56; Layout.maximumHeight: 56
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text { text: win.discTitle; font.pixelSize: 19; font.bold: true; color: "#0c2e4a"; elide: Text.ElideRight; Layout.fillWidth: true }
                    Text { text: "What do you want Halcyon to do?"; font.pixelSize: 13; color: "#2a5878" }
                }
            }

            Repeater {
                model: options
                delegate: Rectangle {
                    required property int index
                    required property string oicon
                    required property string olabel
                    required property string odesc
                    Layout.fillWidth: true
                    implicitHeight: 52
                    radius: 6
                    border.color: hover.hovered ? "#661e9be0" : "transparent"
                    gradient: Gradient {
                        GradientStop { position: 0.0;  color: hover.hovered ? "#f0faff" : "transparent" }
                        GradientStop { position: 0.48; color: hover.hovered ? "#cdeeff" : "transparent" }
                        GradientStop { position: 0.52; color: hover.hovered ? "#a8e0fa" : "transparent" }
                        GradientStop { position: 1.0;  color: hover.hovered ? "#d8f4ff" : "transparent" }
                    }
                    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: win.choose(index) }
                    Kirigami.Icon {
                        id: optIcon
                        source: oicon
                        width: 32; height: 32
                        anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                    }
                    Column {
                        anchors { left: optIcon.right; leftMargin: 12; right: parent.right; verticalCenter: parent.verticalCenter }
                        Text { text: olabel; font.pixelSize: 14; color: "#0a5fa8"; font.bold: true }
                        Text { text: odesc; font.pixelSize: 12; color: "#4a6a80"; visible: odesc !== "" }
                    }
                }
            }

            CheckBox {
                id: always
                text: "Always do this for " + win.what
            }
        }
    }
}
