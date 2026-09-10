# Omarchy Cryptomator Plugin

<p align="center">
  <img src="assets/overview.png" alt="Omarchy Cryptomator Plugin Banner" width="680" />
</p>

<p align="center">
  <strong>Native Omarchy status bar widget and keyboard-driven control panel for <a href="https://cryptomator.org/">Cryptomator</a> vaults.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Omarchy%20Linux-blue?style=flat-square" alt="Platform" />
  <img src="https://img.shields.io/badge/Engine-Quickshell%20%2F%20QML-orange?style=flat-square" alt="Engine" />
  <img src="https://img.shields.io/badge/Window%20Manager-Hyprland-brightgreen?style=flat-square" alt="Hyprland" />
  <img src="https://img.shields.io/badge/CLI-Bundled%200.6.2-blueviolet?style=flat-square" alt="Cryptomator CLI" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License" />
</p>

---

## Overview

The **Omarchy Cryptomator Plugin** (`pandac.cryptomator`) brings seamless, privacy-first encrypted vault management directly to the Omarchy status bar. Decrypt, mount, browse, lock, or initialize Cryptomator vaults without opening heavyweight desktop windows or losing your workflow.

Designed specifically for **Omarchy Linux** and **Hyprland**, it conforms dynamically to your active system theme colors, accent highlights, typography, and keyboard layer navigation.

---

## Visual Showcase

<div align="center">

| **Vaults Overview (Locked / Idle)** | **Inline Passphrase Unlock** |
| :---: | :---: |
| <img src="assets/overview.png" alt="Vaults Overview" width="420" /> | <img src="assets/unlock-prompt.png" alt="Inline Passphrase Prompt" width="420" /> |
| *Status overview, registered vaults, and action bar* | *Instant in-place unlocking with password toggle* |

| **Create New Encrypted Vault** | **Add Existing Vault** |
| :---: | :---: |
| <img src="assets/create-vault.png" alt="Create New Vault" width="420" /> | <img src="assets/add-vault.png" alt="Add Existing Vault" width="420" /> |
| *Initialize fresh AES-256 vaults directly in panel* | *Import existing vaults containing masterkey.cryptomator* |

</div>

---

## ✨ Features

- 🔒 **Live Status Bar Widget**:
  - Discreet optical glyph icon (`󰌾` locked, `󰌿` unlocked).
  - Automatically lights up in your theme's active accent color whenever any vault is mounted.
  - Informative tooltips displaying total and unlocked vault counts.
  - Smooth unlock/lock pulse animations for instantaneous visual feedback.

- ⚡ **Instant Inline Unlock**:
  - Decrypt and mount vaults directly inside the panel with zero context switching.
  - Secure STDIN password stream — your passphrases are **never** exposed in process arguments (`argv`) or shell history.
  - Show/hide password reveal toggle (`󰈈` / `󰈉`).
  - Strict UI hygiene: password inputs are scrubbed from memory immediately upon submit, cancel, or dialog dismiss.

- ➕ **Full Vault Lifecycle**:
  - **Create New Vault**: Initialize an empty folder as a brand-new encrypted Cryptomator vault (AES-256) with confirmation checks and automatic registration.
  - **Import Existing**: Register any pre-existing Cryptomator vault (`masterkey.cryptomator`) with a single click.
  - **Open in File Manager**: One-click browsing of decrypted mount points using your preferred file manager (`xdg-open` / Nautilus / Dolphin).
  - **Individual Lock**: Clean unmounting and process termination via `fusermount3 -u`.
  - **Emergency Lock All**: Panic button / shortcut to lock all mounted vaults simultaneously.

- 📦 **Zero-Config Bundled CLI**:
  - Includes a self-contained, pre-packaged `cryptomator-cli` runtime.
  - Operates standalone without requiring a separate Java JDK installation or manual CLI PATH setup.
  - Automatic fallback detection for system-installed GUI or CLI binaries.

- 🛡️ **Security-Hardened Architecture**:
  - File registry saved strictly under `~/.local/share/pandac.cryptomator/vaults.json` with restricted `0600` permissions.
  - Decrypted vaults mounted safely inside user-exclusive runtime directory `/run/user/$UID/cryptomator/` (`0700`).
  - Robust Zip-Slip path traversal defense and SHA-256 checksum verification on CLI bundles.
  - Sanitized process management against regex injection.

- ⌨️ **Keyboard & Hyprland Friendly**:
  - First-class layer-shell keycatcher integration.
  - Quick navigation shortcuts: `r` to refresh, `l` to lock all, `Esc` to dismiss.

---

## 🚀 Installation

### 1. Clone the Plugin

Clone this repository into your Omarchy user plugins directory:

```bash
git clone https://github.com/pandac/omarchy-cryptomator-plugin.git ~/.config/omarchy/plugins/pandac.cryptomator
```

### 2. Enable in Shell Configuration

Open your Omarchy shell configuration file (`~/.config/omarchy/shell.json`) and add `"pandac.cryptomator"` to your desired bar section (e.g. `bar.layout.right`):

```json
{
  "bar": {
    "layout": {
      "right": [
        { "id": "pandac.cryptomator" },
        { "id": "omarchy.tray" },
        { "id": "omarchy.network" },
        { "id": "omarchy.audio" },
        { "id": "omarchy.power" }
      ]
    }
  }
}
```

### 3. Reload the Shell

Apply the changes immediately:

```bash
omarchy-shell shell rescanPlugins
# or restart the shell
omarchy-restart-shell
```

The Cryptomator lock icon will now appear in your status bar!

---

## 🎮 Controls & Shortcuts

### Status Bar Mouse Actions

| Action | Result |
| :--- | :--- |
| **Left Click** | Toggle the Cryptomator panel open / closed |
| **Right Click** | **Emergency Lock All** — instantly unmounts all unlocked vaults |
| **Middle Click** | Refresh status and check vault mounts |

### Panel Keyboard Navigation

When the panel is open:

| Key | Action |
| :--- | :--- |
| `r` / `R` | Force refresh vault detection and mount statuses |
| `l` / `L` | Lock all currently unlocked vaults |
| `Esc` | Close active form drawer / cancel password prompt / close panel |
| `Enter` | Submit password or confirm form input |

---

## ⌨️ Hyprland Keybinding

To open or toggle the Cryptomator panel with a global hotkey, add an IPC bind to your Hyprland configuration (e.g., `~/.config/hypr/bindings.lua` or `~/.config/hypr/hyprland.conf`):

### In `~/.config/hypr/hyprland.conf`:
```ini
bind = $mainMod, C, exec, omarchy-shell pandac.cryptomator toggle
bind = $mainMod SHIFT, C, exec, omarchy-shell pandac.cryptomator lockAll
```

### In `~/.config/hypr/bindings.lua`:
```lua
-- Example Hyprland lua binding
{ "SUPER", "C", "omarchy-shell pandac.cryptomator toggle" },
{ "SUPER_SHIFT", "C", "omarchy-shell pandac.cryptomator lockAll" },
```

---

## 💻 IPC Commands

The plugin exposes an IPC interface that can be called directly via `omarchy-shell` from scripts, terminal prompts, or custom widgets:

```bash
# Toggle panel visibility
omarchy-shell pandac.cryptomator toggle

# Open or close the panel
omarchy-shell pandac.cryptomator open
omarchy-shell pandac.cryptomator close

# Lock all open vaults
omarchy-shell pandac.cryptomator lockAll

# Refresh vault state
omarchy-shell pandac.cryptomator refresh

# Print current status summary
omarchy-shell pandac.cryptomator status

# Dump state or vaults in JSON format
omarchy-shell pandac.cryptomator dumpState
omarchy-shell pandac.cryptomator dumpVaults
```

---

## ⚙️ How It Works

1. **Vault Registry**: Vaults are registered and tracked in `~/.local/share/pandac.cryptomator/vaults.json`. If you already use the Cryptomator desktop application, vaults in `~/.config/Cryptomator/settings.json` are also automatically recognized.
2. **Mounting**: Decrypted files are mounted via FUSE into `/run/user/$UID/cryptomator/<vault-name>`.
3. **Engine Execution**: Headless operations use the isolated `vendor/cryptomator-cli` binary. Commands are executed asynchronously with non-blocking subprocesses so your desktop bar remains 60 FPS smooth.

---

## 📋 Requirements

- **Omarchy Linux** (with Hyprland & Quickshell)
- **`fuse3` / `fusermount3`** (pre-installed on Omarchy)
- **`python3`** (standard on Omarchy)
- *(Optional)* **Cryptomator GUI** (`cryptomator` or `cryptomator-bin` from AUR) if you prefer desktop UI access alongside the bar widget.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

<p align="center">
  Crafted with ❤️ for the <a href="https://omarchy.org/">Omarchy Linux</a> Community.
</p>
