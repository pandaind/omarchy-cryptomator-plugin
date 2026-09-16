import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})

  property bool installed: false
  property bool cliInstalled: false
  property bool guiInstalled: false
  property bool isBundled: false
  property string cliPath: ""
  property bool running: false
  property int totalVaults: 0
  property int unlockedCount: 0
  property var vaults: []
  property bool refreshing: false
  property string lastError: ""

  property bool unlocking: unlockProcess.running
  property string unlockingVaultPath: ""
  property string lastUnlockError: ""
  property bool settingUpBundle: setupProcess.running
  property bool addingVaultProcess: false
  property bool creatingVaultProcess: false
  property bool removingVaultProcess: false
  property string removingVaultPath: ""

  signal unlockFinished(string vaultPath, bool success, string message)
  signal setupFinished(bool success, string message)
  signal addVaultFinished(bool success, string message)
  signal createVaultFinished(bool success, string message)
  signal removeVaultFinished(bool success, string message)

  readonly property int refreshIntervalSec: {
    var val = settings ? settings.refreshIntervalSec : 10
    var n = parseInt(String(val), 10)
    return isFinite(n) && n >= 2 ? n : 10
  }

  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property string statusScript: pluginDir + "/status.py"
  readonly property string actionsScript: pluginDir + "/actions.py"

  property var pythonCmd: {
    var e = ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "HOME=" + Quickshell.env("HOME")]
    if (Quickshell.env("XDG_DATA_HOME")) e.push("XDG_DATA_HOME=" + Quickshell.env("XDG_DATA_HOME"))
    if (Quickshell.env("XDG_CONFIG_HOME")) e.push("XDG_CONFIG_HOME=" + Quickshell.env("XDG_CONFIG_HOME"))
    if (Quickshell.env("WAYLAND_DISPLAY")) e.push("WAYLAND_DISPLAY=" + Quickshell.env("WAYLAND_DISPLAY"))
    if (Quickshell.env("XDG_RUNTIME_DIR")) e.push("XDG_RUNTIME_DIR=" + Quickshell.env("XDG_RUNTIME_DIR"))
    if (Quickshell.env("DISPLAY")) e.push("DISPLAY=" + Quickshell.env("DISPLAY"))
    if (Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE")) e.push("HYPRLAND_INSTANCE_SIGNATURE=" + Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE"))
    e.push("/usr/bin/python3", "-B")
    return e
  }

  property string _statusOutput: ""
  property string _statusError: ""
  property string _unlockOutput: ""
  property string _unlockError: ""
  property string _setupOutput: ""
  property string _setupError: ""
  property string _addOutput: ""
  property string _addError: ""
  property string _createOutput: ""
  property string _createError: ""
  property string _removeOutput: ""
  property string _removeError: ""
  property var _refreshCallbacks: []

  function refresh(cb) {
    if (typeof cb === "function") {
      _refreshCallbacks.push(cb)
    }
    if (statusProcess.running) return
    _statusOutput = ""
    _statusError = ""
    refreshing = true
    statusProcess.command = pythonCmd.concat([statusScript])
    statusProcess.running = true
  }

  function applyStatus(raw) {
    var parsed = Model.parseStatus(raw)
    if (!parsed.ok) {
      lastError = parsed.lastError || "Failed to parse Cryptomator status"
      return
    }
    installed = parsed.installed === true
    cliInstalled = parsed.cliInstalled === true
    guiInstalled = parsed.guiInstalled === true
    isBundled = parsed.isBundled === true
    cliPath = parsed.cliPath || ""
    running = parsed.running === true
    totalVaults = parsed.totalVaults || 0
    unlockedCount = parsed.unlockedCount || 0
    vaults = parsed.vaults || []
    lastError = ""
  }

  function runAction(action, arg, arg2) {
    var cmd = pythonCmd.concat([actionsScript, action])
    if (arg && String(arg).trim() !== "") {
      cmd.push(String(arg))
    }
    if (arg2 && String(arg2).trim() !== "") {
      cmd.push(String(arg2))
    }
    Quickshell.execDetached(cmd)
    delayedRefresh.restart()
  }

  function lockVault(mountPoint, vaultPath) {
    var updated = []
    var newUnlockedCount = 0
    for (var i = 0; i < root.vaults.length; i++) {
      var v = Object.assign({}, root.vaults[i])
      if ((mountPoint && v.mountPoint === mountPoint) || (vaultPath && v.path === vaultPath)) {
        v.isMounted = false
      }
      if (v.isMounted) newUnlockedCount++
      updated.push(v)
    }
    root.vaults = updated
    root.unlockedCount = newUnlockedCount
    runAction("lock", mountPoint || "", vaultPath || "")
  }

  function unlockVaultWithPassword(vaultPath, mountPoint, password) {
    if (unlockProcess.running) return false
    _unlockOutput = ""
    _unlockError = ""
    lastUnlockError = ""
    unlockingVaultPath = vaultPath
    unlockProcess.secret = password
    unlockProcess.command = pythonCmd.concat([actionsScript, "unlock-password", vaultPath, mountPoint || ""])
    unlockProcess.running = true
    return true
  }

  function revealVault(mountPoint) {
    runAction("reveal", mountPoint)
  }

  function lockAll() {
    var updated = []
    for (var i = 0; i < root.vaults.length; i++) {
      var v = Object.assign({}, root.vaults[i])
      v.isMounted = false
      updated.push(v)
    }
    root.vaults = updated
    root.unlockedCount = 0
    runAction("lock-all")
  }

  function launchApp() {
    runAction("launch")
  }

  function addVault(vaultPath, name) {
    if (addingVaultProcess) return
    _addOutput = ""
    _addError = ""
    addingVaultProcess = true
    var cmd = pythonCmd.concat([actionsScript, "add-vault", vaultPath])
    if (name && String(name).trim() !== "") cmd.push(String(name))
    addProcess.command = cmd
    addProcess.running = true
  }

  function removeVault(vaultPath) {
    if (removingVaultProcess) return
    _removeOutput = ""
    _removeError = ""
    removingVaultPath = vaultPath
    removingVaultProcess = true
    removeProcess.command = pythonCmd.concat([actionsScript, "remove-vault", vaultPath])
    removeProcess.running = true
  }

  function createVault(vaultPath, password, name) {
    if (creatingVaultProcess) return
    _createOutput = ""
    _createError = ""
    creatingVaultProcess = true
    createProcess.secret = password
    var cmd = pythonCmd.concat([actionsScript, "create-vault", vaultPath])
    if (name && String(name).trim() !== "") cmd.push(String(name))
    createProcess.command = cmd
    createProcess.running = true
  }

  function setupBundle() {
    if (setupProcess.running) return
    _setupOutput = ""
    _setupError = ""
    setupProcess.command = pythonCmd.concat([actionsScript, "setup-bundle"])
    setupProcess.running = true
  }

  Timer {
    id: pollTimer
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  Timer {
    id: delayedRefresh
    interval: 800
    repeat: false
    onTriggered: root.refresh()
  }

  Process {
    id: statusProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: statusStdout
      waitForEnd: true
      onStreamFinished: root._statusOutput = text
    }
    stderr: StdioCollector {
      id: statusStderr
      waitForEnd: true
      onStreamFinished: root._statusError = text
    }
    onExited: function(exitCode) {
      root.refreshing = false
      var out = String(statusStdout.text || root._statusOutput || "")
      var err = String(statusStderr.text || root._statusError || "")
      if (exitCode === 0) {
        root.applyStatus(out)
      } else {
        root.lastError = err || "Process exited with code " + exitCode
      }
      var cbs = root._refreshCallbacks.slice()
      root._refreshCallbacks = []
      for (var i = 0; i < cbs.length; i++) {
        try { cbs[i]() } catch (e) { console.error("refresh callback error:", e) }
      }
    }
  }

  Process {
    id: unlockProcess
    running: false
    property string secret: ""
    stdinEnabled: true
    command: []

    stdout: StdioCollector {
      id: unlockStdout
      waitForEnd: true
      onStreamFinished: root._unlockOutput = text
    }
    stderr: StdioCollector {
      id: unlockStderr
      waitForEnd: true
      onStreamFinished: root._unlockError = text
    }

    onStarted: {
      write(secret + "\n")
      secret = ""
    }

    onExited: function(exitCode) {
      var out = String(unlockStdout.text || root._unlockOutput || "").trim()
      var err = String(unlockStderr.text || root._unlockError || "").trim()
      var vPath = root.unlockingVaultPath
      var success = (exitCode === 0)
      var msg = success ? (out || "Unlocked successfully") : (err || "Failed to unlock vault")

      if (!success) {
        root.lastUnlockError = msg
      } else {
        root.lastUnlockError = ""
        var updated = []
        var newUnlockedCount = 0
        for (var i = 0; i < root.vaults.length; i++) {
          var v = Object.assign({}, root.vaults[i])
          if (v.path === vPath) {
            v.isMounted = true
          }
          if (v.isMounted) newUnlockedCount++
          updated.push(v)
        }
        root.vaults = updated
        root.unlockedCount = newUnlockedCount
        root.refresh()
      }

      root.unlockFinished(vPath, success, msg)
      root.unlockingVaultPath = ""
    }
  }

  Process {
    id: setupProcess
    running: false
    command: []

    stdout: StdioCollector {
      id: setupStdout
      waitForEnd: true
      onStreamFinished: root._setupOutput = text
    }
    stderr: StdioCollector {
      id: setupStderr
      waitForEnd: true
      onStreamFinished: root._setupError = text
    }

    onExited: function(exitCode) {
      var out = String(setupStdout.text || root._setupOutput || "").trim()
      var err = String(setupStderr.text || root._setupError || "").trim()
      var success = (exitCode === 0)
      if (success) root.refresh()
      root.setupFinished(success, success ? (out || "Bundle installed successfully") : (err || "Bundle setup failed"))
    }
  }

  Process {
    id: addProcess
    running: false
    command: []

    stdout: StdioCollector {
      id: addStdout
      waitForEnd: true
      onStreamFinished: root._addOutput = text
    }
    stderr: StdioCollector {
      id: addStderr
      waitForEnd: true
      onStreamFinished: root._addError = text
    }

    onExited: function(exitCode) {
      var out = String(addStdout.text || root._addOutput || "").trim()
      var err = String(addStderr.text || root._addError || "").trim()
      var success = (exitCode === 0)
      if (success) {
        root.refresh(function() {
          root.addingVaultProcess = false
          root.addVaultFinished(true, out || "Vault registered")
        })
      } else {
        root.addingVaultProcess = false
        root.addVaultFinished(false, err || "Failed to add vault")
      }
    }
  }

  Process {
    id: createProcess
    running: false
    stdinEnabled: true
    property string secret: ""
    command: []

    stdout: StdioCollector {
      id: createStdout
      waitForEnd: true
      onStreamFinished: root._createOutput = text
    }
    stderr: StdioCollector {
      id: createStderr
      waitForEnd: true
      onStreamFinished: root._createError = text
    }

    onStarted: {
      write(secret + "\n")
      secret = ""
    }

    onExited: function(exitCode) {
      var out = String(createStdout.text || root._createOutput || "").trim()
      var err = String(createStderr.text || root._createError || "").trim()
      var success = (exitCode === 0)
      if (success) {
        root.refresh(function() {
          root.creatingVaultProcess = false
          root.createVaultFinished(true, out || "Vault created successfully")
        })
      } else {
        root.creatingVaultProcess = false
        root.createVaultFinished(false, err || "Failed to create vault")
      }
    }
  }

  Process {
    id: removeProcess
    running: false
    command: []

    stdout: StdioCollector {
      id: removeStdout
      waitForEnd: true
      onStreamFinished: root._removeOutput = text
    }
    stderr: StdioCollector {
      id: removeStderr
      waitForEnd: true
      onStreamFinished: root._removeError = text
    }

    onExited: function(exitCode) {
      var out = String(removeStdout.text || root._removeOutput || "").trim()
      var err = String(removeStderr.text || root._removeError || "").trim()
      var success = (exitCode === 0)
      var targetPath = root.removingVaultPath
      root.removingVaultPath = ""
      if (success) {
        var updated = []
        for (var i = 0; i < root.vaults.length; i++) {
          if (root.vaults[i].path !== targetPath) {
            updated.push(root.vaults[i])
          }
        }
        root.vaults = updated
        root.totalVaults = updated.length
        root.refresh(function() {
          root.removingVaultProcess = false
          root.removeVaultFinished(true, out || "Vault removed")
        })
      } else {
        root.removingVaultProcess = false
        root.removeVaultFinished(false, err || "Failed to remove vault")
      }
    }
  }

  Component.onCompleted: {
    refresh()
  }
}
