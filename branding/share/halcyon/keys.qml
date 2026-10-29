import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

// Halcyon keybinding cheat sheet, fed by keybindings.tsv (tools/gen_keys.py).
ApplicationWindow {
    id: win
    title: "Halcyon Keybindings"
    width: 760; height: 640
    visible: true
    color: "transparent"
    flags: Qt.Dialog

    ListModel { id: rows }

    function matches(r) {
        var q = filter.text.toLowerCase()
        return q === "" || (r.keys + " " + r.desc + " " + r.section).toLowerCase().indexOf(q) >= 0
    }

    Component.onCompleted: {
        var xhr = new XMLHttpRequest()
        xhr.open("GET", "file:///usr/share/halcyon/keybindings.tsv", false)
        xhr.send()
        xhr.responseText.split("\n").forEach(function (line) {
            var f = line.split("\t")
            if (f.length === 3) rows.append({ section: f[0], keys: f[1].replace(/Meta/g, "Super"), desc: f[2] })
        })
    }

    Shortcut { sequence: "Escape"; onActivated: Qt.quit() }

    Rectangle {
        anchors.fill: parent
        radius: 12
        border.color: "#992a6e9a"
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#f4ffffff" }
            GradientStop { position: 0.08; color: "#e6eaf7ff" }
            GradientStop { position: 0.1; color: "#e2dcefff" }
            GradientStop { position: 1.0; color: "#eaf2fbff" }
        }

        ColumnLayout {
            anchors { fill: parent; margins: 18 }
            spacing: 10

            TextField {
                id: filter
                Layout.fillWidth: true
                placeholderText: "Search keybindings…"
                focus: true
            }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: rows
                section.property: "section"
                section.delegate: Text {
                    text: section
                    font.bold: true; font.pixelSize: 15
                    color: "#0a5fa8"
                    topPadding: 10; bottomPadding: 4
                }
                delegate: RowLayout {
                    required property var model
                    readonly property var modelData: model
                    width: ListView.view.width
                    visible: win.matches(model)
                    height: visible ? implicitHeight : 0
                    Text {
                        text: modelData.keys
                        Layout.preferredWidth: 300
                        font.family: "Source Code Pro"; font.pixelSize: 13
                        color: "#0c2e4a"
                    }
                    Text {
                        text: modelData.desc
                        Layout.fillWidth: true
                        font.pixelSize: 13
                        color: "#0c2e4a"
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
        }
    }
}
