import QtQuick
import QtQuick.Effects
import qs.Commons
import qs.Ui

Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Color.foreground
  property bool unlocked: false

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  readonly property string svgSource: {
    if (unlocked) {
      return "file:///usr/share/icons/hicolor/symbolic/apps/org.cryptomator.Cryptomator.tray-unlocked-symbolic.svg"
    }
    return "file:///usr/share/icons/hicolor/symbolic/apps/org.cryptomator.Cryptomator.tray-symbolic.svg"
  }

  Image {
    id: svgImage
    anchors.fill: parent
    fillMode: Image.PreserveAspectFit
    sourceSize.width: Math.round(root.width * Screen.devicePixelRatio)
    sourceSize.height: Math.round(root.height * Screen.devicePixelRatio)
    source: root.svgSource
    visible: false
    layer.enabled: true
  }

  MultiEffect {
    id: effect
    anchors.fill: svgImage
    source: svgImage
    visible: svgImage.status === Image.Ready
    colorization: 1.0
    colorizationColor: root.color
  }

  OpticalGlyph {
    id: glyphFallback
    anchors.fill: parent
    visible: svgImage.status !== Image.Ready
    text: root.unlocked ? "󰌿" : "󰌾"
    fontSize: root.iconSize
    color: root.color
  }
}
