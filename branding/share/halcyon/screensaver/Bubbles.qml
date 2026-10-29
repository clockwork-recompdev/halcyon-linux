import QtQuick

// "Bubbles": Halcyon's built-in screensaver scene. Deep aqua water, slow
// light rays from the surface, glossy bubbles rising at three depths, and the
// Halcyon logo drifting in the middle. Everything is drawn in code.
Rectangle {
    id: scene
    gradient: Gradient {
        GradientStop { position: 0.0; color: "#3fb6e8" }
        GradientStop { position: 0.35; color: "#0f6fae" }
        GradientStop { position: 1.0; color: "#021a33" }
    }
    clip: true
    property url logo: "file:///usr/share/pixmaps/halcyon-logo.png"
    property real t: 0
    NumberAnimation on t { from: 0; to: 1000; duration: 1000000; loops: Animation.Infinite }

    // Light rays fanning down from the surface, slowly swaying.
    Repeater {
        model: 7
        Rectangle {
            required property int index
            readonly property real phase: index * 1.7
            width: scene.width * (0.06 + (index % 3) * 0.03)
            height: scene.height * 1.5
            x: scene.width * (0.08 + index * 0.13) + Math.sin(scene.t * 0.25 + phase) * scene.width * 0.04
            y: -scene.height * 0.15
            rotation: 12 + Math.sin(scene.t * 0.15 + phase) * 6
            transformOrigin: Item.Top
            opacity: 0.10 + 0.07 * Math.sin(scene.t * 0.4 + phase)
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#ffffffff" }
                GradientStop { position: 0.7; color: "#40d8f4ff" }
                GradientStop { position: 1.0; color: "#00ffffff" }
            }
        }
    }

    // A glossy bubble: rim, inner tint, big highlight, small back-reflection.
    component Bubble: Item {
        id: bub
        property real depth: 1            // 0.35 far .. 1 near
        property real size: 40
        property real speed: 30           // px per second
        property real wobble: 20
        property real seed: Math.random() * 100
        width: size; height: size
        opacity: 0.35 + depth * 0.55
        function reset(initial) {
            size = (12 + Math.random() * 70) * depth * Math.max(0.3, scene.height / 1080)
            speed = (20 + Math.random() * 35) * (0.5 + depth) * Math.max(0.3, scene.height / 1080)
            wobble = 8 + Math.random() * 30
            x = Math.random() * scene.width
            y = initial ? Math.random() * scene.height : scene.height + size
        }
        Component.onCompleted: reset(true)
        Rectangle {
            anchors.fill: parent; radius: width / 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#2affffff" }
                GradientStop { position: 0.7; color: "#10a8e8ff" }
                GradientStop { position: 1.0; color: "#40c8f4ff" }
            }
            border.width: Math.max(1, parent.width * 0.04)
            border.color: "#90e8faff"
        }
        Rectangle {   // main highlight
            x: parent.width * 0.2; y: parent.height * 0.1
            width: parent.width * 0.42; height: parent.height * 0.3
            radius: height / 2; rotation: -25
            gradient: Gradient {
                GradientStop { position: 0; color: "#e0ffffff" }
                GradientStop { position: 1; color: "#00ffffff" }
            }
        }
        Rectangle {   // little reflection, lower right
            x: parent.width * 0.62; y: parent.height * 0.68
            width: parent.width * 0.14; height: width; radius: width / 2
            color: "#70ffffff"
        }
        FrameAnimation {
            running: true
            onTriggered: {
                bub.y -= bub.speed * frameTime
                bub.x += Math.sin((bub.y + bub.seed * 37) / 60) * bub.wobble * frameTime
                if (bub.y < -bub.height) bub.reset(false)
            }
        }
    }

    Repeater { model: 26; Bubble { depth: 0.35 } }   // far
    Repeater { model: 14; Bubble { depth: 0.65 } }   // middle

    Image {   // logo drifting in the middle of the water
        source: scene.logo
        width: Math.min(scene.width, scene.height) * 0.2; height: width
        sourceSize.width: 512; sourceSize.height: 512
        fillMode: Image.PreserveAspectFit
        x: (scene.width - width) / 2 + Math.sin(scene.t * 0.11) * scene.width * 0.12
        y: (scene.height - height) / 2 + Math.sin(scene.t * 0.17) * scene.height * 0.08
        rotation: Math.sin(scene.t * 0.2) * 6
        opacity: 0.85
    }

    Repeater { model: 8; Bubble { depth: 1 } }       // near, in front of the logo
}
