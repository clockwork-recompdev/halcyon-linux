import QtQuick
import QtQuick.Controls

// Halcyon login, laid out like the Windows 7 / Vista logon screen: a glass-framed
// user tile in the middle, password box with a round arrow button, the shut-down
// button bottom right and the Halcyon mark bottom centre. Background: the user's
// own wallpaper (background.png from the branding package).
Item {
    id: root
    width: 1920; height: 1080

    readonly property real u: Math.min(width / 1920, height / 1080)   // scale for small screens
    property int picked: userModel.count > 1 ? -1 : 0                 // -1: show all accounts
    property string fontName: "Noto Sans"

    Image {
        anchors.fill: parent
        source: config.background
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
    }
    // Darker edges, like the logon screen's vignette, so the tile and text read well.
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#55001020" }
            GradientStop { position: 0.35; color: "#10001020" }
            GradientStop { position: 0.7; color: "#10001020" }
            GradientStop { position: 1.0; color: "#70001020" }
        }
    }

    // Soft dark glow behind the sign-in area so white text reads on any wallpaper.
    Canvas {
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -10 * root.u
        width: 900 * root.u; height: 620 * root.u
        onPaint: {
            var c = getContext("2d")
            c.reset()
            var g = c.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, width / 2)
            g.addColorStop(0, "rgba(0, 16, 40, 0.42)")
            g.addColorStop(0.55, "rgba(0, 16, 40, 0.22)")
            g.addColorStop(1, "rgba(0, 16, 40, 0)")
            c.save()
            c.scale(1, height / width)
            c.fillStyle = g
            c.fillRect(0, 0, width, width)
            c.restore()
        }
    }

    // ---- a user tile: glass frame with the picture inside -------------------------------
    component Tile: Item {
        id: tile
        property string name
        property string iconPath
        property real size: 190 * root.u
        property bool hot: false
        width: size; height: size
        // Soft glow behind the frame.
        Rectangle {
            anchors.centerIn: parent
            width: parent.width + 18 * root.u; height: width; radius: 16 * root.u
            color: "transparent"
            border.width: 9 * root.u
            border.color: tile.hot ? "#3090d8ff" : "#18ffffff"
        }
        // Glass frame.
        Rectangle {
            anchors.fill: parent
            radius: 12 * root.u
            border.color: "#c8ffffff"
            border.width: 1
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#d8ffffff" }
                GradientStop { position: 0.48; color: "#90dceeff" }
                GradientStop { position: 0.52; color: "#70b8d8f0" }
                GradientStop { position: 1.0; color: "#a0e8f4ff" }
            }
        }
        // Picture.
        Rectangle {
            id: well
            anchors.fill: parent; anchors.margins: parent.width * 0.085
            radius: 4 * root.u
            clip: true
            color: "#1f5e96"
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#5aa8de" }
                GradientStop { position: 1.0; color: "#164f86" }
            }
            Image {
                id: face
                anchors.fill: parent
                source: tile.iconPath
                fillMode: Image.PreserveAspectCrop
                visible: status === Image.Ready
                asynchronous: true
            }
            // No picture: a drawn silhouette.
            Item {
                anchors.fill: parent
                visible: !face.visible
                Rectangle {
                    width: parent.width * 0.34; height: width; radius: width / 2
                    x: (parent.width - width) / 2; y: parent.height * 0.18
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: "#f2fbff" }
                        GradientStop { position: 1.0; color: "#b7d6ec" }
                    }
                }
                Rectangle {
                    width: parent.width * 0.66; height: parent.height * 0.5; radius: width * 0.42
                    x: (parent.width - width) / 2; y: parent.height * 0.56
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: "#e8f6ff" }
                        GradientStop { position: 1.0; color: "#9cc4e2" }
                    }
                }
            }
            // Glass shine across the top half of the picture.
            Rectangle {
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: parent.height * 0.48
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "#50ffffff" }
                    GradientStop { position: 1.0; color: "#08ffffff" }
                }
            }
        }
    }

    component ShadowText: Text {
        color: "white"
        font.family: root.fontName
        style: Text.Raised
        styleColor: "#a0001830"
    }

    // ---- choose an account (several users) ------------------------------------------
    Row {
        id: chooser
        visible: root.picked < 0
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -40 * root.u
        spacing: 42 * root.u
        Repeater {
            model: userModel
            delegate: Column {
                spacing: 12 * root.u
                Tile {
                    size: 128 * root.u
                    name: model.name
                    iconPath: model.icon
                    hot: area.containsMouse
                    MouseArea {
                        id: area
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: { root.picked = index; password.forceActiveFocus() }
                    }
                }
                ShadowText {
                    width: 128 * root.u
                    horizontalAlignment: Text.AlignHCenter
                    text: model.realName || model.name
                    font.pixelSize: 20 * root.u
                    elide: Text.ElideRight
                }
            }
        }
    }

    // ---- the selected account -------------------------------------------------------
    Column {
        id: signIn
        visible: root.picked >= 0
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        anchors.verticalCenterOffset: -30 * root.u
        spacing: 14 * root.u

        Tile {
            anchors.horizontalCenter: parent.horizontalCenter
            name: root.userName()
            iconPath: root.userIcon()
            hot: true
        }
        ShadowText {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.userRealName()
            font.pixelSize: 30 * root.u
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 8 * root.u
            // Keep the box centred: the arrow button sits to its right.
            leftPadding: arrow.width + spacing

            TextField {
                id: password
                width: 226 * root.u; height: 30 * root.u
                echoMode: TextInput.Password
                placeholderText: "Password"
                font.family: root.fontName
                font.pixelSize: 14 * root.u
                focus: true
                leftPadding: 8 * root.u
                color: "#111"
                placeholderTextColor: "#888"
                background: Rectangle {
                    radius: 3 * root.u
                    color: "white"
                    border.color: password.activeFocus ? "#3c7fb1" : "#8aa2b8"
                    Rectangle {   // inner top shade, like the Windows 7 edit box
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
                        height: parent.height * 0.35; radius: parent.radius
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: "#14000000" }
                            GradientStop { position: 1.0; color: "#00000000" }
                        }
                    }
                }
                onAccepted: root.doLogin()
            }

            // Round glass arrow button.
            Item {
                id: arrow
                width: 30 * root.u; height: width
                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    border.color: "#0b3f6e"
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: arrowArea.pressed ? "#3d86c4" : "#9fd3fb" }
                        GradientStop { position: 0.48; color: arrowArea.pressed ? "#1b5f9e" : "#3b97de" }
                        GradientStop { position: 0.52; color: arrowArea.pressed ? "#0f4a82" : "#1f73bf" }
                        GradientStop { position: 1.0; color: arrowArea.pressed ? "#2a6fae" : (arrowArea.containsMouse ? "#5fc3ff" : "#3d9be2") }
                    }
                }
                Canvas {
                    anchors.fill: parent
                    onPaint: {
                        var c = getContext("2d"), w = width, h = height
                        c.reset()
                        c.strokeStyle = "white"; c.fillStyle = "white"
                        c.lineWidth = Math.max(2, w * 0.1); c.lineCap = "round"
                        c.beginPath(); c.moveTo(w * 0.28, h * 0.5); c.lineTo(w * 0.62, h * 0.5); c.stroke()
                        c.beginPath(); c.moveTo(w * 0.74, h * 0.5); c.lineTo(w * 0.52, h * 0.3); c.lineTo(w * 0.52, h * 0.7)
                        c.closePath(); c.fill()
                    }
                }
                MouseArea { id: arrowArea; anchors.fill: parent; hoverEnabled: true; onClicked: root.doLogin() }
            }
        }

        ShadowText {
            id: message
            anchors.horizontalCenter: parent.horizontalCenter
            font.pixelSize: 15 * root.u
            text: ""
        }

        // "Switch User", only with several accounts.
        Button {
            id: switchUser
            anchors.horizontalCenter: parent.horizontalCenter
            visible: userModel.count > 1
            text: "Switch User"
            font.family: root.fontName
            font.pixelSize: 14 * root.u
            onClicked: { root.picked = -1; password.text = ""; message.text = "" }
            contentItem: ShadowText {
                text: switchUser.text; font: switchUser.font
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                implicitWidth: 120 * root.u; implicitHeight: 30 * root.u; radius: 3 * root.u
                border.color: "#90ffffff"
                gradient: Gradient {
                    GradientStop { position: 0.0; color: switchUser.hovered ? "#90406080" : "#70203a58" }
                    GradientStop { position: 0.5; color: switchUser.hovered ? "#80203a58" : "#60102844" }
                    GradientStop { position: 1.0; color: switchUser.hovered ? "#90305070" : "#70183450" }
                }
            }
        }
    }

    // ---- Halcyon mark, bottom centre ------------------------------------------------
    Row {
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 46 * root.u }
        spacing: 12 * root.u
        Image {
            source: "logo.png"
            sourceSize.width: 54 * root.u; sourceSize.height: 54 * root.u
            anchors.verticalCenter: parent.verticalCenter
        }
        ShadowText {
            text: "Halcyon"
            font.pixelSize: 40 * root.u
            font.weight: Font.Light
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    // ---- session choice, bottom left (small and quiet) -------------------------------
    ComboBox {
        id: session
        anchors { left: parent.left; bottom: parent.bottom; margins: 28 * root.u }
        width: 220 * root.u
        model: sessionModel
        textRole: "name"
        currentIndex: sessionModel.lastIndex
        font.family: root.fontName
        font.pixelSize: 13 * root.u
        contentItem: ShadowText { text: session.displayText; font: session.font; leftPadding: 8; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle {
            implicitHeight: 28 * root.u; radius: 3 * root.u
            color: session.hovered ? "#30ffffff" : "#18ffffff"
            border.color: "#60ffffff"
        }
    }

    // ---- shut down, bottom right: the red glass button plus a menu arrow ---------------
    Row {
        anchors { right: parent.right; bottom: parent.bottom; margins: 28 * root.u }
        spacing: 2
        Item {
            id: power
            width: 42 * root.u; height: 30 * root.u
            visible: sddm.canPowerOff
            Rectangle {
                anchors.fill: parent; radius: 3 * root.u
                border.color: "#5c0b06"
                gradient: Gradient {
                    GradientStop { position: 0.0; color: powerArea.containsMouse ? "#ffb3a1" : "#f19b8a" }
                    GradientStop { position: 0.46; color: powerArea.containsMouse ? "#f1563c" : "#d6452e" }
                    GradientStop { position: 0.54; color: powerArea.containsMouse ? "#c62a12" : "#a92612" }
                    GradientStop { position: 1.0; color: powerArea.containsMouse ? "#f25a3c" : "#cf3a20" }
                }
            }
            Canvas {   // power symbol
                anchors.fill: parent
                onPaint: {
                    var c = getContext("2d"), w = width, h = height, r = h * 0.26
                    c.reset()
                    c.strokeStyle = "white"; c.lineWidth = Math.max(2, h * 0.08); c.lineCap = "round"
                    c.beginPath(); c.arc(w / 2, h / 2 + h * 0.03, r, -Math.PI * 0.32, Math.PI * 1.32, false); c.stroke()
                    c.beginPath(); c.moveTo(w / 2, h * 0.18); c.lineTo(w / 2, h * 0.5); c.stroke()
                }
            }
            MouseArea { id: powerArea; anchors.fill: parent; hoverEnabled: true; onClicked: sddm.powerOff() }
            ToolTip.visible: powerArea.containsMouse
            ToolTip.text: "Shut down"
        }
        Item {
            width: 18 * root.u; height: 30 * root.u
            visible: sddm.canReboot || sddm.canSuspend
            Rectangle {
                anchors.fill: parent; radius: 3 * root.u
                border.color: "#5c0b06"
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "#f19b8a" }
                    GradientStop { position: 0.5; color: "#b32c17" }
                    GradientStop { position: 1.0; color: "#cf3a20" }
                }
            }
            Text { anchors.centerIn: parent; text: "▸"; color: "white"; font.pixelSize: 12 * root.u }
            MouseArea { anchors.fill: parent; onClicked: powerMenu.popup() }
            Menu {
                id: powerMenu
                MenuItem { text: "Restart"; visible: sddm.canReboot; onTriggered: sddm.reboot() }
                MenuItem { text: "Sleep"; visible: sddm.canSuspend; onTriggered: sddm.suspend() }
                MenuItem { text: "Shut down"; visible: sddm.canPowerOff; onTriggered: sddm.powerOff() }
            }
        }
    }

    // ---- logic --------------------------------------------------------------------
    function row(role) {
        var i = root.picked >= 0 ? root.picked : Math.max(0, userModel.lastIndex)
        return userModel.data(userModel.index(i, 0), role)
    }
    // SDDM's user model roles: Qt.UserRole + 1 name, + 2 realName, + 4 icon.
    function userName() { return row(Qt.UserRole + 1) || userModel.lastUser || "" }
    function userRealName() { return row(Qt.UserRole + 2) || userName() }
    function userIcon() { return row(Qt.UserRole + 4) || "" }

    function doLogin() {
        message.text = ""
        sddm.login(userName(), password.text, session.currentIndex)
    }

    Component.onCompleted: {
        if (userModel.count === 1 || userModel.lastIndex >= 0 && userModel.count > 1 && userModel.lastUser !== "")
            root.picked = Math.max(0, userModel.lastIndex)
        password.forceActiveFocus()
    }

    Connections {
        target: sddm
        function onLoginFailed() {
            message.text = "The user name or password is incorrect."
            password.text = ""
            password.forceActiveFocus()
        }
    }
}
