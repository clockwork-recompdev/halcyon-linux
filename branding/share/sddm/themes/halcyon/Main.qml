import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Halcyon Aero greeter: a frosted-glass card centred over the wallpaper.
Item {
    id: root
    width: 1920; height: 1080

    readonly property color ink: "#0c2e4a"
    readonly property color aqua: "#1e9be0"

    Image {
        anchors.fill: parent
        source: config.background
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
    }

    Text {
        id: clock
        anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: parent.height * 0.08 }
        color: "white"
        font.pixelSize: 72; font.weight: Font.Light
        style: Text.Raised; styleColor: "#40002040"
        function update() { text = Qt.formatTime(new Date(), Qt.locale().timeFormat(Locale.ShortFormat)) }
        Timer { interval: 1000; running: true; repeat: true; triggeredOnStart: true; onTriggered: clock.update() }
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: 380; height: form.implicitHeight + 56
        radius: 14
        border.color: "#992a6e9a"
        gradient: Gradient {
            GradientStop { position: 0.0;  color: "#e6ffffff" }
            GradientStop { position: 0.46; color: "#c8eaf7ff" }
            GradientStop { position: 0.5;  color: "#b0cdeaf8" }
            GradientStop { position: 1.0;  color: "#d0e6f6ff" }
        }
        Rectangle {  // inner highlight
            anchors.fill: parent; anchors.margins: 1
            radius: parent.radius - 1; color: "transparent"; border.color: "#ccffffff"
        }

        ColumnLayout {
            id: form
            anchors { fill: parent; margins: 28 }
            spacing: 12

            Image {
                Layout.alignment: Qt.AlignHCenter
                source: "logo.png"
                sourceSize.width: 96; sourceSize.height: 96
            }

            ComboBox {
                id: user
                Layout.fillWidth: true
                model: userModel
                textRole: "name"
                currentIndex: userModel.lastIndex
                editable: true
                Component.onCompleted: if (userModel.lastUser !== "") editText = userModel.lastUser
            }

            TextField {
                id: password
                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: "Password"
                focus: true
                onAccepted: root.doLogin()
            }

            ComboBox {
                id: session
                Layout.fillWidth: true
                model: sessionModel
                textRole: "name"
                currentIndex: sessionModel.lastIndex
            }

            Button {
                id: loginButton
                Layout.fillWidth: true
                text: "Log in"
                onClicked: root.doLogin()
                contentItem: Text {
                    text: loginButton.text; color: "white"; font.bold: true
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitHeight: 34; radius: 17
                    border.color: "#063a5e"
                    gradient: Gradient {
                        GradientStop { position: 0.0;  color: loginButton.down ? "#4cb8e8" : "#bfe9ff" }
                        GradientStop { position: 0.48; color: loginButton.down ? "#1d86c8" : "#6cc8f2" }
                        GradientStop { position: 0.52; color: loginButton.down ? "#0b5e98" : "#1e9be0" }
                        GradientStop { position: 1.0;  color: loginButton.down ? "#2a8ec8" : "#7ee0ff" }
                    }
                }
            }

            Text {
                id: message
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                color: "#d33a1a"
                text: ""
            }
        }
    }

    Row {
        anchors { right: parent.right; bottom: parent.bottom; margins: 20 }
        spacing: 10
        Button { text: "Restart"; visible: sddm.canReboot; onClicked: sddm.reboot() }
        Button { text: "Shut down"; visible: sddm.canPowerOff; onClicked: sddm.powerOff() }
    }

    function doLogin() {
        message.text = ""
        var name = user.editText !== "" ? user.editText : user.currentText
        sddm.login(name, password.text, session.currentIndex)
    }

    Connections {
        target: sddm
        function onLoginFailed() {
            message.text = "Login failed. Please try again."
            password.text = ""
            password.forceActiveFocus()
        }
    }
}
