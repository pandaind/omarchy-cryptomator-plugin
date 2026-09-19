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
  moduleName: "omarchy-cryptomator-plugin"
  ipcTarget: "omarchy-cryptomator-plugin"
  manageIpc: false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ─── State ────────────────────────────────────────────────────────────────
  property string activePasswordVault: ""
  property string formMode: ""           // "" | "add" | "create"
  readonly property bool formActive: formMode !== ""

  property string addVaultPath: ""
  property string addErrorMsg: ""

  property string createVaultPath: ""
  property string createVaultName: ""
  property string createVaultPw: ""
  property string createVaultPwConfirm: ""
  property bool   createShowPw: false
  property string createErrorMsg: ""

  // ─── Colors ───────────────────────────────────────────────────────────────
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent:     bar ? bar.urgent     : Color.urgent
  readonly property color accent:     Color.accent
  readonly property color dim:        Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // ─── Helpers ──────────────────────────────────────────────────────────────
  property string highlightVaultPath: ""
  Timer {
    id: clearHighlightTimer
    interval: 3500
    repeat: false
    onTriggered: root.highlightVaultPath = ""
  }

  function resetAddForm() {
    addVaultPath = ""
    addErrorMsg = ""
    if (typeof addPathField !== "undefined" && addPathField) addPathField.text = ""
  }
  function resetCreateForm() {
    createVaultPath = ""
    createVaultName = ""
    createVaultPw = ""
    createVaultPwConfirm = ""
    createErrorMsg = ""
    createShowPw = false
    if (typeof createPathField !== "undefined" && createPathField) createPathField.text = ""
    if (typeof createNameField !== "undefined" && createNameField) createNameField.text = ""
    if (typeof createPwField !== "undefined" && createPwField) createPwField.text = ""
    if (typeof createPwConfirmField !== "undefined" && createPwConfirmField) createPwConfirmField.text = ""
  }
  function closeForm()       { formMode = ""; resetAddForm(); resetCreateForm() }

  // ─── Service ──────────────────────────────────────────────────────────────
  Service { id: cryptomator; settings: root.settings }

  Connections {
    target: cryptomator
    function onAddVaultFinished(ok, msg) {
      if (ok) {
        root.highlightVaultPath = root.addVaultPath.trim()
        clearHighlightTimer.restart()
        root.closeForm()
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      } else {
        root.addErrorMsg = msg || "Failed to register vault"
      }
    }
    function onCreateVaultFinished(ok, msg) {
      if (ok) {
        root.highlightVaultPath = root.createVaultPath.trim()
        clearHighlightTimer.restart()
        root.closeForm()
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      } else {
        root.createErrorMsg = msg || "Failed to create vault"
      }
    }
    function onRemoveVaultFinished(ok, msg) {
      // Always restore focus so the panel doesn't close
      Qt.callLater(function() { keyCatcher.forceActiveFocus() })
    }
  }

  // ─── IPC ──────────────────────────────────────────────────────────────────
  IpcHandler {
    target: root.ipcTarget
    function open():   void   { root.open() }
    function close():  void   { root.close() }
    function show():   void   { root.open() }
    function hide():   void   { root.close() }
    function toggle(): void   { root.toggle() }
    function refresh(): string { cryptomator.refresh(); return "ok" }
    function lockAll(): string { cryptomator.lockAll(); return "ok" }
    function setupBundle(): string { cryptomator.setupBundle(); return "started" }
    function status(): string {
      return Model.summaryText(cryptomator.unlockedCount, cryptomator.totalVaults,
                               cryptomator.installed, cryptomator.running)
    }
    function dumpState(): string {
      return JSON.stringify({
        installed: cryptomator.installed, cliInstalled: cryptomator.cliInstalled,
        guiInstalled: cryptomator.guiInstalled, isBundled: cryptomator.isBundled,
        cliPath: cryptomator.cliPath, running: cryptomator.running,
        totalVaults: cryptomator.totalVaults, unlockedCount: cryptomator.unlockedCount,
        vaultsCount: cryptomator.totalVaults, lastError: cryptomator.lastError
      })
    }
    function dumpVaults(): string { return JSON.stringify(cryptomator.vaults) }
  }

  // ─── Bar button ───────────────────────────────────────────────────────────
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: cryptomator.unlockedCount > 0 ? "󰌿" : "󰌾"
    active: cryptomator.unlockedCount > 0
    activeColor: Color.accent
    tooltipText: Model.summaryText(cryptomator.unlockedCount, cryptomator.totalVaults,
                                   cryptomator.installed, cryptomator.running)
    onPressed: function(btn) {
      if (btn === Qt.RightButton) cryptomator.lockAll()
      else root.toggle()
    }
  }

  // ─── Panel ────────────────────────────────────────────────────────────────
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth:  panel.fittedContentWidth(Style.space(480))
    contentHeight: panel.fittedContentHeight(mainCol.implicitHeight, Style.space(680))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.activePasswordVault !== "" || root.formActive
      onCloseRequested: root.close()
      onTabRequested: function(d) { root.switchPanel(d) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") cryptomator.refresh()
        else if (t === "l" || t === "L") cryptomator.lockAll()
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainCol.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: mainCol
          width: parent.width
          spacing: Style.space(12)

          // ═══════════════════════════════════════════ HERO
          PanelHero {
            width: parent.width
            title: "Cryptomator"
            meta: {
              if (cryptomator.settingUpBundle)   return "Downloading CLI bundle..."
              if (cryptomator.creatingVaultProcess) return "Creating vault..."
              if (cryptomator.addingVaultProcess) return "Registering vault..."
              return Model.summaryText(cryptomator.unlockedCount, cryptomator.totalVaults,
                                       cryptomator.installed, cryptomator.running)
            }
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
                  visible: !cryptomator.isBundled
                  text: cryptomator.settingUpBundle ? "Downloading..." : "Setup CLI"
                  iconText: "󰉍"; bordered: true; enabled: !cryptomator.settingUpBundle
                  onClicked: cryptomator.setupBundle()
                }
                Button {
                  visible: cryptomator.installed && cryptomator.unlockedCount > 0
                  text: "Lock All"; iconText: "󰌾"; bordered: true
                  onClicked: cryptomator.lockAll()
                }
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ═══════════════════════════════════════════ VERIFIED BUNDLE NOT INSTALLED
          // Gated on isBundled, not installed: unlocking only ever trusts this
          // plugin's own verified bundle, so this must show even for a user who
          // already has the Cryptomator GUI or a system cryptomator-cli — those
          // are surfaced elsewhere (see the CLI status text) but can't unlock a
          // vault through this plugin on their own.
          Item {
            visible: !cryptomator.isBundled
            width: parent.width
            implicitHeight: notInstalledCard.implicitHeight

            BorderSurface {
              id: notInstalledCard
              width: parent.width
              implicitHeight: notInstalledInner.implicitHeight + Style.space(24)
              radius: Style.cornerRadius
              color: Style.hoverFillFor(root.foreground, root.urgent)
              topPadding: Style.space(12); bottomPadding: Style.space(12)
              leftPadding: Style.space(14); rightPadding: Style.space(14)

              Column {
                id: notInstalledInner
                anchors { left: parent.left; right: parent.right; top: parent.top }
                anchors { leftMargin: parent.leftPadding; rightMargin: parent.rightPadding; topMargin: parent.topPadding }
                spacing: Style.space(8)

                Text {
                  textFormat: Text.PlainText
                  text: cryptomator.installed ? "Secure CLI Bundle Required" : "Cryptomator CLI Not Installed"
                  font.bold: true; font.family: root.fontFamily; font.pixelSize: Style.font.body
                  color: root.foreground
                }
                Text {
                  textFormat: Text.PlainText; width: parent.width; wrapMode: Text.WordWrap
                  text: cryptomator.installed
                    ? "Vault unlocking only trusts this plugin's own verified CLI bundle, even though Cryptomator is already installed on your system. Use Setup CLI above to unlock vaults."
                    : "Download the official headless CLI bundle — no desktop app required. Use Setup CLI above."
                  font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.dim
                }
              }
            }
          }

          // ═══════════════════════════════════════════ VAULT LIST SECTION
          Column {
            visible: cryptomator.installed
            width: parent.width
            spacing: Style.space(8)

            // ─── Section header: "VAULTS (N)" + Add / New buttons ──────────
            RowLayout {
              width: parent.width

              PanelSectionHeader {
                text: cryptomator.totalVaults > 0
                  ? "VAULTS (" + cryptomator.totalVaults + ")"
                  : "VAULTS"
                Layout.alignment: Qt.AlignVCenter
              }
              Item { Layout.fillWidth: true }

              Row {
                spacing: Style.space(4)
                visible: !root.formActive && cryptomator.totalVaults > 0

                Button {
                  text: "Add Existing"; iconText: "󰙅"; bordered: true
                  fontSize: Style.font.bodySmall
                  tooltipText: "Register an existing Cryptomator vault directory"
                  onClicked: { root.resetAddForm(); root.formMode = "add" }
                }
                Button {
                  text: "New Vault"; iconText: "󰐕"; bordered: true; accent: Color.accent
                  fontSize: Style.font.bodySmall
                  tooltipText: "Create a brand-new encrypted vault"
                  onClicked: { root.resetCreateForm(); root.formMode = "create" }
                }
              }

              // Close-form button when a form is open
              Button {
                visible: root.formActive
                iconText: "󰅖"; bordered: true
                tooltipText: "Cancel"
                onClicked: { root.closeForm(); keyCatcher.forceActiveFocus() }
              }
            }

            // ─── ADD EXISTING form ──────────────────────────────────────────
            Item {
              visible: root.formMode === "add"
              width: parent.width
              implicitHeight: addFormCard.implicitHeight

              BorderSurface {
                id: addFormCard
                width: parent.width
                implicitHeight: addFormInner.implicitHeight + Style.space(24)
                radius: Style.cornerRadius
                color: Style.hoverFillFor(root.foreground, Color.accent)
                topPadding: Style.space(12); bottomPadding: Style.space(12)
                leftPadding: Style.space(14); rightPadding: Style.space(14)

                Column {
                  id: addFormInner
                  anchors { left: parent.left; right: parent.right; top: parent.top }
                  anchors { leftMargin: parent.leftPadding; rightMargin: parent.rightPadding; topMargin: parent.topPadding }
                  spacing: Style.space(8)

                  Text {
                    textFormat: Text.PlainText; text: "󰙅  Add Existing Vault"
                    font.bold: true; font.family: root.fontFamily; font.pixelSize: Style.font.body
                    color: Color.accent
                  }
                  Text {
                    textFormat: Text.PlainText; width: parent.width; wrapMode: Text.WordWrap
                    text: "Point to a directory that already contains a Cryptomator vault (masterkey.cryptomator)."
                    font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.dim
                  }

                  TextField {
                    id: addPathField
                    width: parent.width
                    placeholderText: "e.g. ~/Secure_Files/MyVault"
                    font.family: root.fontFamily; foreground: root.foreground
                    onTextChanged: { root.addVaultPath = text; root.addErrorMsg = "" }
                    onAccepted: {
                      if (text.trim().length > 0 && !cryptomator.addingVaultProcess)
                        cryptomator.addVault(text.trim(), "")
                    }
                    Keys.onEscapePressed: { root.closeForm(); keyCatcher.forceActiveFocus() }
                    onVisibleChanged: { if (visible) { text = ""; Qt.callLater(forceActiveFocus) } }
                  }

                  Row {
                    spacing: Style.space(6)
                    Button {
                      text: cryptomator.addingVaultProcess ? "Registering..." : "Register Vault"
                      iconText: cryptomator.addingVaultProcess ? "󰑐" : "󰙅"
                      bordered: true; accent: Color.accent
                      enabled: root.addVaultPath.trim().length > 0 && !cryptomator.addingVaultProcess
                      onClicked: { root.addErrorMsg = ""; cryptomator.addVault(root.addVaultPath.trim(), "") }
                    }
                    Button {
                      text: "Cancel"; bordered: true
                      onClicked: { root.closeForm(); keyCatcher.forceActiveFocus() }
                    }
                  }

                  // Error
                  RowLayout {
                    visible: root.addErrorMsg !== ""; width: parent.width; spacing: Style.space(6)
                    Text { textFormat: Text.PlainText; text: "󰅚"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent }
                    Text { textFormat: Text.PlainText; text: root.addErrorMsg; font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                  }
                }
              }
            }

            // ─── CREATE NEW VAULT form ──────────────────────────────────────
            Item {
              visible: root.formMode === "create"
              width: parent.width
              implicitHeight: createFormCard.implicitHeight

              BorderSurface {
                id: createFormCard
                width: parent.width
                implicitHeight: createFormInner.implicitHeight + Style.space(24)
                radius: Style.cornerRadius
                color: Style.hoverFillFor(root.foreground, Color.accent)
                topPadding: Style.space(12); bottomPadding: Style.space(12)
                leftPadding: Style.space(14); rightPadding: Style.space(14)

                Column {
                  id: createFormInner
                  anchors { left: parent.left; right: parent.right; top: parent.top }
                  anchors { leftMargin: parent.leftPadding; rightMargin: parent.rightPadding; topMargin: parent.topPadding }
                  spacing: Style.space(8)

                  Text {
                    textFormat: Text.PlainText; text: "󰐕  Create New Vault"
                    font.bold: true; font.family: root.fontFamily; font.pixelSize: Style.font.body
                    color: Color.accent
                  }
                  Text {
                    textFormat: Text.PlainText; width: parent.width; wrapMode: Text.WordWrap
                    text: "Choose an empty folder. It will be initialised as an encrypted Cryptomator vault and registered automatically."
                    font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.dim
                  }

                  // Vault directory
                  Text { textFormat: Text.PlainText; text: "Vault directory"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; color: root.foreground }
                  TextField {
                    id: createPathField
                    width: parent.width; placeholderText: "e.g. ~/Secure_Files/NewVault"
                    font.family: root.fontFamily; foreground: root.foreground
                    onTextChanged: { root.createVaultPath = text; root.createErrorMsg = "" }
                    Keys.onEscapePressed: { root.closeForm(); keyCatcher.forceActiveFocus() }
                    onVisibleChanged: { if (visible) { text = ""; Qt.callLater(forceActiveFocus) } }
                  }

                  // Vault name (optional)
                  Text { textFormat: Text.PlainText; text: "Display name (optional)"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; color: root.foreground }
                  TextField {
                    id: createNameField
                    width: parent.width; placeholderText: "Defaults to folder name"
                    font.family: root.fontFamily; foreground: root.foreground
                    onTextChanged: root.createVaultName = text
                    Keys.onEscapePressed: { root.closeForm(); keyCatcher.forceActiveFocus() }
                    onVisibleChanged: { if (visible) text = "" }
                  }

                  // Passphrase
                  Text { textFormat: Text.PlainText; text: "Passphrase"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; color: root.foreground }
                  RowLayout {
                    width: parent.width; spacing: Style.space(6)
                    TextField {
                      id: createPwField
                      Layout.fillWidth: true; placeholderText: "Choose a strong passphrase..."
                      password: !root.createShowPw; font.family: root.fontFamily; foreground: root.foreground
                      onTextChanged: { root.createVaultPw = text; root.createErrorMsg = "" }
                      Keys.onEscapePressed: { root.closeForm(); keyCatcher.forceActiveFocus() }
                      onVisibleChanged: text = ""
                    }
                    Button {
                      iconText: root.createShowPw ? "󰈈" : "󰈉"; bordered: true
                      tooltipText: root.createShowPw ? "Hide" : "Show"
                      onClicked: root.createShowPw = !root.createShowPw
                    }
                    // Strength dot
                    Rectangle {
                      width: Style.space(8); height: Style.space(8); radius: width / 2
                      Layout.alignment: Qt.AlignVCenter
                      color: {
                        var pw = root.createVaultPw
                        if (!pw) return root.dim
                        if (pw.length < 8)  return root.urgent
                        if (pw.length < 12) return Qt.rgba(1, 0.6, 0, 1)
                        return Color.accent
                      }
                    }
                  }

                  // Confirm passphrase
                  Text { textFormat: Text.PlainText; text: "Confirm passphrase"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; color: root.foreground }
                  TextField {
                    id: createPwConfirmField
                    width: parent.width; placeholderText: "Re-enter passphrase..."
                    password: !root.createShowPw; font.family: root.fontFamily; foreground: root.foreground
                    onTextChanged: { root.createVaultPwConfirm = text; root.createErrorMsg = "" }
                    Keys.onEscapePressed: { root.closeForm(); keyCatcher.forceActiveFocus() }
                    onAccepted: createVaultBtn.clicked()
                    onVisibleChanged: text = ""
                  }

                  // Mismatch warning
                  Text {
                    visible: root.createVaultPwConfirm.length > 0 && root.createVaultPw !== root.createVaultPwConfirm
                    textFormat: Text.PlainText; text: "󰅚  Passphrases do not match"
                    font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent
                  }

                  Row {
                    spacing: Style.space(6)
                    Button {
                      id: createVaultBtn
                      text: cryptomator.creatingVaultProcess ? "Creating vault..." : "Create Vault"
                      iconText: cryptomator.creatingVaultProcess ? "󰑐" : "󰐕"
                      bordered: true; accent: Color.accent
                      enabled: root.createVaultPath.trim().length > 0
                               && root.createVaultPw.length >= 1
                               && root.createVaultPw === root.createVaultPwConfirm
                               && !cryptomator.creatingVaultProcess
                      onClicked: {
                        root.createErrorMsg = ""
                        cryptomator.createVault(root.createVaultPath.trim(),
                                                root.createVaultPw,
                                                root.createVaultName.trim())
                      }
                    }
                    Button {
                      text: "Cancel"; bordered: true
                      onClicked: { root.closeForm(); keyCatcher.forceActiveFocus() }
                    }
                  }

                  // Error
                  RowLayout {
                    visible: root.createErrorMsg !== ""; width: parent.width; spacing: Style.space(6)
                    Text { textFormat: Text.PlainText; text: "󰅚"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent }
                    Text { textFormat: Text.PlainText; text: root.createErrorMsg; font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                  }
                }
              }
            }

            // ─── EMPTY STATE (no vaults, no form open) ─────────────────────
            Item {
              visible: cryptomator.totalVaults === 0 && !root.formActive && !cryptomator.addingVaultProcess && !cryptomator.creatingVaultProcess
              width: parent.width
              implicitHeight: emptyCard.implicitHeight

              BorderSurface {
                id: emptyCard
                width: parent.width
                implicitHeight: emptyInner.implicitHeight + Style.space(24)
                radius: Style.cornerRadius
                color: Style.hoverFillFor(root.foreground, Color.accent)
                topPadding: Style.space(16); bottomPadding: Style.space(16)
                leftPadding: Style.space(14); rightPadding: Style.space(14)

                Column {
                  id: emptyInner
                  anchors { left: parent.left; right: parent.right; top: parent.top }
                  anchors { leftMargin: parent.leftPadding; rightMargin: parent.rightPadding; topMargin: parent.topPadding }
                  spacing: Style.space(10)

                  Text {
                    textFormat: Text.PlainText; text: "No vaults yet"
                    font.bold: true; font.family: root.fontFamily; font.pixelSize: Style.font.body
                    color: root.foreground
                  }
                  Text {
                    textFormat: Text.PlainText; width: parent.width; wrapMode: Text.WordWrap
                    text: "Create a new encrypted vault or register an existing Cryptomator vault directory."
                    font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.dim
                  }
                  Row {
                    spacing: Style.space(8)
                    Button {
                      text: "Add Existing"; iconText: "󰙅"; bordered: true
                      onClicked: { root.resetAddForm(); root.formMode = "add" }
                    }
                    Button {
                      text: "Create New Vault"; iconText: "󰐕"; bordered: true; accent: Color.accent
                      onClicked: { root.resetCreateForm(); root.formMode = "create" }
                    }
                  }
                }
              }
            }

            // ─── VAULT CARDS ───────────────────────────────────────────────
            Repeater {
              model: cryptomator.vaults

              delegate: Item {
                id: vaultItem
                required property var modelData
                required property int index

                width: parent.width
                implicitHeight: vaultCard.implicitHeight

                BorderSurface {
                  id: vaultCard
                  width: parent.width
                  implicitHeight: cardCol.implicitHeight + Style.space(20)
                  radius: Style.cornerRadius
                  color: vaultItem.modelData.isMounted
                    ? (vaultCard.recentlyUnlocked
                        ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.28)
                        : Style.selectedFillFor(root.foreground, Color.accent))
                    : (vaultCard.isHighlighted
                        ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.2)
                        : Style.hoverFillFor(root.foreground, Color.accent))

                  Behavior on color {
                    ColorAnimation { duration: 250 }
                  }

                  readonly property bool isPrompting: root.activePasswordVault === vaultItem.modelData.path
                  readonly property bool isUnlocking: cryptomator.unlocking && cryptomator.unlockingVaultPath === vaultItem.modelData.path
                  readonly property bool isHighlighted: root.highlightVaultPath !== "" && (root.highlightVaultPath === vaultItem.modelData.path || root.highlightVaultPath === vaultItem.modelData.name)
                  property string unlockError: ""
                  property bool recentlyUnlocked: false

                  Timer {
                    id: unlockPulseTimer
                    interval: 1500
                    repeat: false
                    onTriggered: vaultCard.recentlyUnlocked = false
                  }

                  Connections {
                    target: cryptomator
                    function onUnlockFinished(vaultPath, ok, msg) {
                      if (vaultPath !== vaultItem.modelData.path) return
                      if (ok) {
                        vaultCard.unlockError = ""
                        vaultCard.recentlyUnlocked = true
                        unlockPulseTimer.restart()
                        root.activePasswordVault = ""
                        pwField.text = ""
                        keyCatcher.forceActiveFocus()
                      } else {
                        vaultCard.unlockError = msg || "Incorrect password"
                        pwField.selectAll(); pwField.forceActiveFocus()
                      }
                    }
                  }

                  Column {
                    id: cardCol
                    anchors { left: parent.left; right: parent.right; top: parent.top }
                    anchors { leftMargin: Style.space(12); rightMargin: Style.space(12); topMargin: Style.space(10) }
                    spacing: Style.space(10)

                    // Main info row
                    Item {
                      width: parent.width
                      implicitHeight: Math.max(vaultLeft.implicitHeight, vaultBtns.implicitHeight)

                      Row {
                        id: vaultLeft
                        anchors { left: parent.left; right: vaultBtns.left; rightMargin: Style.space(8) }
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(10)

                        Text {
                          id: statusIcon
                          textFormat: Text.PlainText
                          text: vaultCard.isUnlocking ? "󰑐" : (vaultItem.modelData.isMounted ? "󰌿" : "󰌾")
                          font.family: root.fontFamily; font.pixelSize: Style.font.display
                          color: vaultItem.modelData.isMounted ? Color.accent : (vaultCard.isUnlocking ? Color.accent : root.dim)
                          anchors.verticalCenter: parent.verticalCenter
                          scale: vaultCard.recentlyUnlocked ? 1.25 : 1.0
                          Behavior on scale {
                            NumberAnimation { duration: 250; easing.type: Easing.OutBack }
                          }
                          Behavior on color {
                            ColorAnimation { duration: 200 }
                          }
                        }

                        Column {
                          anchors.verticalCenter: parent.verticalCenter
                          width: Math.max(10, vaultLeft.width - Style.space(40))
                          spacing: Style.space(3)

                          Row {
                            spacing: Style.space(8)
                            Text {
                              textFormat: Text.PlainText; text: vaultItem.modelData.name || ""
                              font.family: root.fontFamily; font.pixelSize: Style.font.body
                              font.bold: true; color: root.foreground; elide: Text.ElideRight
                            }
                            BorderSurface {
                              radius: 4; anchors.verticalCenter: parent.verticalCenter
                              color: vaultItem.modelData.isMounted
                                ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, vaultCard.recentlyUnlocked ? 0.35 : 0.2)
                                : (vaultCard.isUnlocking || vaultCard.isHighlighted ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.2) : Qt.rgba(root.dim.r, root.dim.g, root.dim.b, 0.15))
                              implicitWidth: badgeTxt.implicitWidth + Style.space(10)
                              implicitHeight: badgeTxt.implicitHeight + Style.space(4)
                              scale: (vaultCard.recentlyUnlocked || vaultCard.isHighlighted) ? 1.08 : 1.0
                              Behavior on scale {
                                NumberAnimation { duration: 250; easing.type: Easing.OutBack }
                              }
                              Behavior on color {
                                ColorAnimation { duration: 200 }
                              }
                              Text {
                                id: badgeTxt; anchors.centerIn: parent; textFormat: Text.PlainText
                                text: vaultCard.isUnlocking ? "UNLOCKING..." : (vaultItem.modelData.isMounted ? (vaultCard.recentlyUnlocked ? "UNLOCKED 󰄬" : "UNLOCKED") : (vaultCard.isHighlighted ? "ADDED 󰄬" : "LOCKED"))
                                font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true
                                color: vaultItem.modelData.isMounted || vaultCard.isUnlocking || vaultCard.isHighlighted ? Color.accent : root.dim
                              }
                            }
                          }

                          Text {
                            textFormat: Text.PlainText; elide: Text.ElideMiddle; width: parent.width
                            text: "󰉋  " + (vaultItem.modelData.isMounted
                              ? Model.shortPath(vaultItem.modelData.mountPoint)
                              : Model.shortPath(vaultItem.modelData.path))
                            font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.dim
                          }
                        }
                      }

                      // Action buttons
                      Row {
                        id: vaultBtns
                        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                        spacing: Style.space(4)

                        Button {
                          visible: vaultItem.modelData.isMounted === true
                          text: "Browse"; iconText: "󰉋"; bordered: true
                          tooltipText: "Open in file manager"
                          onClicked: cryptomator.revealVault(vaultItem.modelData.mountPoint)
                        }
                        Button {
                          visible: vaultItem.modelData.isMounted === true
                          text: "Lock"; iconText: "󰌾"; bordered: true
                          tooltipText: "Safely unmount & lock"
                          onClicked: cryptomator.lockVault(vaultItem.modelData.mountPoint, vaultItem.modelData.path)
                        }
                        Button {
                          visible: vaultItem.modelData.isMounted !== true && !vaultCard.isPrompting
                          text: "Unlock"; iconText: "󰌿"; bordered: true; accent: Color.accent
                          tooltipText: "Enter passphrase to unlock"
                          onClicked: { vaultCard.unlockError = ""; root.activePasswordVault = vaultItem.modelData.path }
                        }
                        Button {
                          visible: vaultItem.modelData.isMounted !== true && vaultCard.isPrompting
                          iconText: "󰅖"; bordered: true; tooltipText: "Cancel unlock"
                          onClicked: { vaultCard.unlockError = ""; root.activePasswordVault = ""; keyCatcher.forceActiveFocus() }
                        }
                        Button {
                          visible: !vaultItem.modelData.isMounted
                          iconText: cryptomator.removingVaultPath === vaultItem.modelData.path ? "󰑐" : "󰆴"
                          enabled: !cryptomator.removingVaultProcess
                          bordered: true
                          tooltipText: "Remove from list (files are NOT deleted)"
                          onClicked: cryptomator.removeVault(vaultItem.modelData.path)
                        }
                      }
                    }

                    // Inline password prompt
                    Item {
                      visible: vaultItem.modelData.isMounted !== true && vaultCard.isPrompting
                      width: parent.width
                      implicitHeight: pwArea.implicitHeight

                      Column {
                        id: pwArea
                        width: parent.width
                        spacing: Style.space(6)

                        RowLayout {
                          width: parent.width; spacing: Style.space(6)

                          TextField {
                            id: pwField
                            Layout.fillWidth: true; password: !eyeBtn.revealed
                            placeholderText: "Vault passphrase..."; foreground: root.foreground
                            font.family: root.fontFamily; enabled: !vaultCard.isUnlocking
                            onAccepted: {
                              if (text.length > 0 && !vaultCard.isUnlocking) {
                                vaultCard.unlockError = ""
                                cryptomator.unlockVaultWithPassword(vaultItem.modelData.path, vaultItem.modelData.mountPoint, text)
                              }
                            }
                            Keys.onEscapePressed: { vaultCard.unlockError = ""; root.activePasswordVault = ""; keyCatcher.forceActiveFocus() }
                            onVisibleChanged: {
                              if (visible) { text = ""; Qt.callLater(function() { pwField.forceActiveFocus() }) }
                              else { text = "" }
                            }
                          }
                          Button { id: eyeBtn; property bool revealed: false; iconText: revealed ? "󰈈" : "󰈉"; bordered: true; tooltipText: revealed ? "Hide" : "Show"; onClicked: revealed = !revealed }
                          Button {
                            text: vaultCard.isUnlocking ? "Unlocking..." : "Unlock"
                            iconText: vaultCard.isUnlocking ? "󰑐" : "󰌿"
                            bordered: true; accent: Color.accent
                            enabled: !vaultCard.isUnlocking && pwField.text.length > 0
                            onClicked: {
                              if (pwField.text.length > 0) {
                                vaultCard.unlockError = ""
                                cryptomator.unlockVaultWithPassword(vaultItem.modelData.path, vaultItem.modelData.mountPoint, pwField.text)
                              }
                            }
                          }
                        }

                        RowLayout {
                          visible: vaultCard.unlockError !== ""; width: parent.width; spacing: Style.space(6)
                          Text { textFormat: Text.PlainText; text: "󰅚"; font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent }
                          Text { textFormat: Text.PlainText; text: vaultCard.unlockError; font.family: root.fontFamily; font.pixelSize: Style.font.caption; color: root.urgent; Layout.fillWidth: true; elide: Text.ElideRight }
                        }
                      }
                    }
                  }
                }
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ═══════════════════════════════════════════ FOOTER
          RowLayout {
            width: parent.width
            Text {
              textFormat: Text.PlainText
              text: cryptomator.isBundled ? "󰌾 Bundled CLI"
                  : (cryptomator.cliInstalled ? "󰌾 System CLI"
                  : (cryptomator.guiInstalled ? "󰌾 GUI" : "󰌾 Not installed"))
              font.family: root.fontFamily; font.pixelSize: Style.font.caption
              color: root.dim; Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            Button { iconText: "󰑐"; text: "Refresh"; fontSize: Style.font.bodySmall; onClicked: cryptomator.refresh() }
          }
        }
      }
    }
  }

  onOpenedChanged: {
    if (opened) {
      root.activePasswordVault = ""
      root.closeForm()
      cryptomator.refresh()
      Qt.callLater(function() { keyCatcher.forceActiveFocus() })
    } else {
      root.activePasswordVault = ""
      root.closeForm()
    }
  }
}
