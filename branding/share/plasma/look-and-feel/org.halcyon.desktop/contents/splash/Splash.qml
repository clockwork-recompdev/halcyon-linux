import QtQuick

// Halcyon startup splash: wallpaper, logo, and a row of pulsing aqua orbs.
Rectangle {
    id: root
    color: "#0a5fa8"

    property int stage

    Image {
        anchors.fill: parent
        source: "images/background.png"
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
    }

    Item {
        id: content
        anchors.fill: parent
        opacity: 0
        Behavior on opacity { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }

        Image {
            id: logo
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -height * 0.15
            source: "images/logo.png"
            sourceSize.width: Math.min(root.width, root.height) * 0.22
            sourceSize.height: sourceSize.width
            asynchronous: true
        }

        Row {
            anchors.top: logo.bottom
            anchors.topMargin: logo.height * 0.25
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 14
            Repeater {
                model: 5
                Image {
                    source: "images/orb.png"
                    width: 18; height: 18
                    opacity: index < root.stage ? 1 : 0.25
                    Behavior on opacity { NumberAnimation { duration: 300 } }
                }
            }
        }
    }

    onStageChanged: if (stage >= 1) content.opacity = 1
}
