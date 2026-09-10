import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "pandac.cryptomator"
  ipcTarget: "pandac.cryptomator"
  manageIpc: false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  property string activePasswordVault: ""

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color barForeground: bar ? bar.barForeground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color barIconColor: cryptomator.unlockedCount > 0 ? Color.accent : barForeground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  Service {
    id: cryptomator
    settings: root.settings
  }

  IpcHandler {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { cryptomator.refresh(); return "ok" }
    function lockAll(): string { cryptomator.lockAll(); return "ok" }
    function launch(): string { cryptomator.launchApp(); return "ok" }
    function unlockWithPassword(vaultPath: string, pw: string): string {
      cryptomator.unlockVaultWithPassword(vaultPath, "", pw)
      return "started"
    }
    function status(): string {
      return Model.summaryText(cryptomator.unlockedCount, cryptomator.totalVaults, cryptomator.installed, cryptomator.running)
    }
    function dumpState(): string {
      return JSON.stringify({
        installed: cryptomator.installed,
        running: cryptomator.running,
        totalVaults: cryptomator.totalVaults,
        unlockedCount: cryptomator.unlockedCount,
        vaultsCount: cryptomator.vaults.length,
        lastError: cryptomator.lastError
      })
    }
    function dumpVaults(): string {
      return JSON.stringify(cryptomator.vaults)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: cryptomator.unlockedCount > 0 ? "󰌿" : "󰌾"
    active: cryptomator.unlockedCount > 0
    activeColor: Color.accent
    tooltipText: Model.summaryText(cryptomator.unlockedCount, cryptomator.totalVaults, cryptomator.installed, cryptomator.running)

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) {
        cryptomator.lockAll()
      } else if (buttonCode === Qt.MiddleButton) {
        cryptomator.launchApp()
      } else {
        root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.activePasswordVault !== ""
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") cryptomator.refresh()
        else if (t === "l" || t === "L") cryptomator.lockAll()
        else if (t === "o" || t === "O") cryptomator.launchApp()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // ---------------------------------------------------- Hero Header
          PanelHero {
            id: hero
            width: parent.width
            title: "Cryptomator"
            meta: cryptomator.unlocking ? "Unlocking vault..." : Model.summaryText(cryptomator.unlockedCount, cryptomator.totalVaults, cryptomator.installed, cryptomator.running)
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              CryptomatorIcon {
                iconSize: Style.font.display
                color: cryptomator.unlockedCount > 0 ? Color.accent : root.foreground
                unlocked: cryptomator.unlockedCount > 0
              }
            }

            trailingControl: Component {
              Row {
                spacing: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter

                Button {
                  visible: !cryptomator.installed
                  text: "Install"
                  iconText: "󰉍"
                  bordered: true
                  onClicked: cryptomator.installApp()
                }

                Button {
                  visible: cryptomator.installed && cryptomator.unlockedCount > 0
                  text: "Lock All"
                  iconText: "󰌾"
                  bordered: true
                  onClicked: cryptomator.lockAll()
                }

                Button {
                  visible: cryptomator.installed && cryptomator.unlockedCount === 0
                  text: "Open App"
                  iconText: "󰝰"
                  bordered: true
                  onClicked: cryptomator.launchApp()
                }
              }
            }
          }

          PanelSeparator {
            foreground: root.foreground
          }

          // ----------------------------------------- Not Installed Banner
          BorderSurface {
            visible: !cryptomator.installed
            width: parent.width
            radius: Style.cornerRadius
            color: Style.selectedFillFor(root.foreground, Color.accent)
            topPadding: Style.space(12)
            bottomPadding: Style.space(12)
            leftPadding: Style.space(14)
            rightPadding: Style.space(14)

            Column {
              width: parent.width
              spacing: Style.space(8)

              Text {
                textFormat: Text.PlainText
                text: "Cryptomator is not installed"
                font.bold: true
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.foreground
              }

              Text {
                textFormat: Text.PlainText
                text: "Transparent client-side encryption of your files in the cloud or on local disk."
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                color: root.dim
                wrapMode: Text.WordWrap
                width: parent.width
              }

              Button {
                text: "Install Cryptomator (AUR)"
                iconText: "󰉍"
                bordered: true
                onClicked: cryptomator.installApp()
              }
            }
          }

          // ----------------------------------------- Empty Vaults Banner
          BorderSurface {
            visible: cryptomator.installed && cryptomator.totalVaults === 0
            width: parent.width
            radius: Style.cornerRadius
            color: Style.selectedFillFor(root.foreground, Color.accent)
            topPadding: Style.space(12)
            bottomPadding: Style.space(12)
            leftPadding: Style.space(14)
            rightPadding: Style.space(14)

            Column {
              width: parent.width
              spacing: Style.space(8)

              Text {
                textFormat: Text.PlainText
                text: "No Vaults Configured"
                font.bold: true
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.foreground
              }

              Text {
                textFormat: Text.PlainText
                text: "Add an encrypted vault in Cryptomator to manage and monitor it here."
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                color: root.dim
                wrapMode: Text.WordWrap
                width: parent.width
              }

              Button {
                text: "Open Cryptomator"
                iconText: "󰝰"
                bordered: true
                onClicked: cryptomator.launchApp()
              }
            }
          }

          // ----------------------------------------- Vaults List
          Column {
            visible: cryptomator.installed && cryptomator.vaults.length > 0
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "VAULTS (" + cryptomator.vaults.length + ")"
            }

            Repeater {
              model: cryptomator.vaults

              delegate: BorderSurface {
                id: vaultCard
                required property var modelData
                required property int index

                readonly property bool isThisVaultPrompting: root.activePasswordVault === modelData.path
                readonly property bool isThisVaultUnlocking: cryptomator.unlocking && cryptomator.unlockingVaultPath === modelData.path
                property string errorMessage: ""

                width: column.width
                implicitHeight: cardContent.implicitHeight + Style.space(20)
                radius: Style.cornerRadius
                color: modelData.isMounted ? Style.selectedFillFor(root.foreground, Color.accent) : Style.hoverFillFor(root.foreground, Color.accent)

                Connections {
                  target: cryptomator
                  function onUnlockFinished(vaultPath, success, message) {
                    if (vaultPath === vaultCard.modelData.path) {
                      if (success) {
                        vaultCard.errorMessage = ""
                        root.activePasswordVault = ""
                        keyCatcher.forceActiveFocus()
                      } else {
                        vaultCard.errorMessage = message || "Incorrect password"
                        pwField.selectAll()
                        pwField.forceActiveFocus()
                      }
                    }
                  }
                }

                Column {
                  id: cardContent
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.top: parent.top
                  anchors.leftMargin: Style.space(12)
                  anchors.rightMargin: Style.space(12)
                  anchors.topMargin: Style.space(10)
                  spacing: Style.space(10)

                  // Main Row: Icon, Vault name/badge/path, and Action Buttons
                  Item {
                    width: parent.width
                    implicitHeight: Math.max(leftInfo.implicitHeight, actionButtons.implicitHeight)

                    Row {
                      id: leftInfo
                      anchors.left: parent.left
                      anchors.right: actionButtons.left
                      anchors.rightMargin: Style.space(8)
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(10)

                      Text {
                        textFormat: Text.PlainText
                        text: modelData.isMounted ? "󰌿" : "󰌾"
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.display
                        color: modelData.isMounted ? Color.accent : root.dim
                        anchors.verticalCenter: parent.verticalCenter
                      }

                      Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(10, leftInfo.width - Style.space(34))
                        spacing: Style.space(2)

                        Row {
                          spacing: Style.space(8)

                          Text {
                            textFormat: Text.PlainText
                            text: modelData.name || ""
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.body
                            font.bold: true
                            color: root.foreground
                            elide: Text.ElideRight
                          }

                          BorderSurface {
                            radius: 4
                            color: modelData.isMounted ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.2) : Qt.rgba(root.dim.r, root.dim.g, root.dim.b, 0.15)
                            implicitWidth: badgeText.implicitWidth + Style.space(10)
                            implicitHeight: badgeText.implicitHeight + Style.space(4)

                            Text {
                              id: badgeText
                              anchors.centerIn: parent
                              textFormat: Text.PlainText
                              text: modelData.isMounted ? "UNLOCKED" : "LOCKED"
                              font.family: root.fontFamily
                              font.pixelSize: Style.font.caption
                              font.bold: true
                              color: modelData.isMounted ? Color.accent : root.dim
                            }
                          }
                        }

                        Text {
                          textFormat: Text.PlainText
                          text: modelData.isMounted ? Model.shortPath(modelData.mountPoint) : Model.shortPath(modelData.path)
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          color: root.dim
                          elide: Text.ElideMiddle
                          width: parent.width
                        }
                      }
                    }

                    Row {
                      id: actionButtons
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(6)

                      Button {
                        visible: modelData.isMounted === true
                        text: "Browse"
                        iconText: "󰉋"
                        bordered: true
                        tooltipText: "Open in File Manager"
                        onClicked: cryptomator.revealVault(modelData.mountPoint)
                      }

                      Button {
                        visible: modelData.isMounted === true
                        text: "Lock"
                        iconText: "󰌾"
                        bordered: true
                        tooltipText: "Safely unmount & lock"
                        onClicked: cryptomator.lockVault(modelData.mountPoint)
                      }

                      Button {
                        visible: modelData.isMounted !== true && !vaultCard.isThisVaultPrompting
                        text: "Unlock"
                        iconText: "󰌿"
                        bordered: true
                        accent: Color.accent
                        tooltipText: "Enter password to unlock"
                        onClicked: {
                          vaultCard.errorMessage = ""
                          root.activePasswordVault = modelData.path
                        }
                      }

                      Button {
                        visible: modelData.isMounted !== true && !vaultCard.isThisVaultPrompting
                        iconText: "󰝰"
                        bordered: true
                        tooltipText: "Open in Cryptomator GUI"
                        onClicked: cryptomator.unlockVault(modelData.path)
                      }

                      Button {
                        visible: modelData.isMounted !== true && vaultCard.isThisVaultPrompting
                        iconText: "󰝰"
                        text: "GUI"
                        bordered: true
                        tooltipText: "Open in Cryptomator GUI instead"
                        onClicked: cryptomator.unlockVault(modelData.path)
                      }

                      Button {
                        visible: modelData.isMounted !== true && vaultCard.isThisVaultPrompting
                        iconText: "󰅖"
                        bordered: true
                        tooltipText: "Cancel inline unlock"
                        onClicked: {
                          vaultCard.errorMessage = ""
                          root.activePasswordVault = ""
                          keyCatcher.forceActiveFocus()
                        }
                      }
                    }
                  }

                  // Inline Password Prompt Area
                  Column {
                    visible: modelData.isMounted !== true && vaultCard.isThisVaultPrompting
                    width: parent.width
                    spacing: Style.space(6)

                    Row {
                      width: parent.width
                      spacing: Style.space(6)

                      TextField {
                        id: pwField
                        width: parent.width - submitBtn.implicitWidth - eyeBtn.implicitWidth - Style.space(12)
                        password: !eyeBtn.revealed
                        placeholderText: "Vault passphrase..."
                        foreground: root.foreground
                        font.family: root.fontFamily
                        enabled: !vaultCard.isThisVaultUnlocking

                        onAccepted: {
                          if (text.length > 0 && !vaultCard.isThisVaultUnlocking) {
                            vaultCard.errorMessage = ""
                            cryptomator.unlockVaultWithPassword(modelData.path, modelData.mountPoint, text)
                          }
                        }

                        Keys.onEscapePressed: {
                          vaultCard.errorMessage = ""
                          root.activePasswordVault = ""
                          keyCatcher.forceActiveFocus()
                        }

                        onVisibleChanged: {
                          if (visible) {
                            text = ""
                            Qt.callLater(function() { pwField.forceActiveFocus() })
                          }
                        }
                      }

                      Button {
                        id: eyeBtn
                        property bool revealed: false
                        iconText: revealed ? "󰈈" : "󰈉"
                        bordered: true
                        tooltipText: revealed ? "Hide passphrase" : "Show passphrase"
                        onClicked: revealed = !revealed
                      }

                      Button {
                        id: submitBtn
                        text: vaultCard.isThisVaultUnlocking ? "Unlocking..." : "Unlock"
                        iconText: vaultCard.isThisVaultUnlocking ? "󰑐" : "󰌿"
                        bordered: true
                        accent: Color.accent
                        enabled: !vaultCard.isThisVaultUnlocking && pwField.text.length > 0
                        onClicked: {
                          if (pwField.text.length > 0) {
                            vaultCard.errorMessage = ""
                            cryptomator.unlockVaultWithPassword(modelData.path, modelData.mountPoint, pwField.text)
                          }
                        }
                      }
                    }

                    // Error text if password attempt failed
                    Row {
                      visible: vaultCard.errorMessage !== ""
                      spacing: Style.space(6)

                      Text {
                        textFormat: Text.PlainText
                        text: "󰅚"
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        color: root.urgent
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: vaultCard.errorMessage
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        color: root.urgent
                        elide: Text.ElideRight
                        width: parent.width - Style.space(24)
                      }
                    }
                  }
                }
              }
            }
          }

          PanelSeparator {
            visible: cryptomator.installed
            foreground: root.foreground
          }

          // ---------------------------------------------------- Footer Bar
          RowLayout {
            width: parent.width

            Text {
              textFormat: Text.PlainText
              text: "Cryptomator: " + (cryptomator.running ? "Active" : "Idle")
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              color: root.dim
              Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            Row {
              spacing: Style.space(6)

              Button {
                iconText: "󰑐"
                text: "Refresh"
                fontSize: Style.font.bodySmall
                onClicked: cryptomator.refresh()
              }

              Button {
                iconText: "󰝰"
                text: "Open GUI"
                fontSize: Style.font.bodySmall
                onClicked: cryptomator.launchApp()
              }
            }
          }
        }
      }
    }
  }

  onOpenedChanged: if (opened) {
    cryptomator.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  } else {
    root.activePasswordVault = ""
  }
}
