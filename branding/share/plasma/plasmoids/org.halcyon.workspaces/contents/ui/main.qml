import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.taskmanager as TaskManager

// Numbered round workspace buttons (Omarchy-style), in Halcyon glass.
PlasmoidItem {
    id: root
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property real orb: Math.round((vertical ? width : height) * 0.72)

    TaskManager.VirtualDesktopInfo { id: desktops }

    // Which desktops have windows, for the "occupied" look.
    TaskManager.TasksModel {
        id: tasks
        groupMode: TaskManager.TasksModel.GroupDisabled
        filterByVirtualDesktop: false
        filterByScreen: false
    }
    function occupied(id) {
        for (let i = 0; i < tasks.count; ++i) {
            const ds = tasks.data(tasks.index(i, 0), TaskManager.AbstractTasksModel.VirtualDesktops) || []
            if (ds.indexOf(id) >= 0) return true
        }
        return false
    }

    P5Support.DataSource {
        id: run
        engine: "executable"
        onNewData: (source) => disconnectSource(source)
    }
    function activate(id) {
        run.connectSource("dbus-send --session --dest=org.kde.KWin /VirtualDesktopManager "
            + "org.freedesktop.DBus.Properties.Set string:org.kde.KWin.VirtualDesktopManager "
            + "string:current variant:string:" + id)
    }
    function step(delta) {
        const ids = desktops.desktopIds
        const i = ids.indexOf(desktops.currentDesktop)
        activate(ids[(i + delta + ids.length) % ids.length])
    }

    Layout.preferredWidth: vertical ? -1 : grid.implicitWidth + orb * 0.4
    Layout.preferredHeight: vertical ? grid.implicitHeight + orb * 0.4 : -1

    WheelHandler {
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: (event) => root.step(event.angleDelta.y > 0 ? -1 : 1)
    }

    Grid {
        id: grid
        anchors.centerIn: parent
        flow: root.vertical ? Grid.TopToBottom : Grid.LeftToRight
        rows: root.vertical ? desktops.numberOfDesktops : 1
        spacing: Math.round(root.orb * 0.28)

        Repeater {
            model: desktops.desktopIds
            delegate: Item {
                required property var modelData
                required property int index
                readonly property bool current: modelData === desktops.currentDesktop
                readonly property bool busy: tasks.count >= 0 && root.occupied(modelData)
                width: root.orb; height: root.orb

                Rectangle {   // glossy orb
                    anchors.fill: parent
                    radius: width / 2
                    border.width: 1
                    border.color: current ? "#cc7fe0ff" : "#88000a14"
                    gradient: Gradient {
                        GradientStop { position: 0.0;  color: current ? "#d8f4ff" : (busy ? "#5a7fa0" : "#2a3e52") }
                        GradientStop { position: 0.48; color: current ? "#5cc4f0" : (busy ? "#33587a" : "#1a2a3a") }
                        GradientStop { position: 0.52; color: current ? "#1e8fd0" : (busy ? "#1c3a56" : "#0e1a26") }
                        GradientStop { position: 1.0;  color: current ? "#7ee0ff" : (busy ? "#2a5070" : "#16283a") }
                    }
                    opacity: hover.hovered || current || busy ? 1 : 0.7
                }
                Rectangle {   // top sheen
                    x: parent.width * 0.18; y: parent.height * 0.08
                    width: parent.width * 0.64; height: parent.height * 0.42
                    radius: height / 2
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#b0ffffff" }
                        GradientStop { position: 1; color: "#00ffffff" }
                    }
                }
                Text {
                    anchors.centerIn: parent
                    text: index + 1
                    color: current ? "#ffffff" : "#d8ecfa"
                    font.pixelSize: Math.round(parent.height * 0.52)
                    font.bold: current
                    style: Text.Raised; styleColor: "#80001020"
                }
                HoverHandler { id: hover }
                TapHandler { onTapped: root.activate(modelData) }
            }
        }
    }
}
