# Omarchy Cryptomator Plugin

Native Omarchy bar widget and popup panel for [Cryptomator](https://cryptomator.org/).

## Features

- **Live Bar Widget**: Displays Cryptomator status in the status bar. The icon lights up in your theme's accent color when any vault is decrypted & mounted.
- **Auto Vault Discovery**: Automatically reads all configured vaults from `~/.config/Cryptomator/settings.json`.
- **Inline Passphrase Prompt**: Decrypt & unlock any vault directly inside the widget panel without opening the desktop application.
  - Secure STDIN password transmission (passwords are never exposed in process argv or logs).
  - Password show/hide toggle (`󰈈` / `󰈉`).
  - Enter-to-submit and Esc-to-cancel shortcuts.
  - Helpful inline validation and error messages.
- **One-Click Actions**:
  - **Unlock**: Unlocks directly in the widget with passphrase or fallback to GUI app.
  - **Browse**: Opens the decrypted mount point in your default file manager (`xdg-open`).
  - **Lock**: Cleanly unmounts and locks the vault (`fusermount3 -u`).
  - **Lock All**: Emergency one-click button to lock all active vaults immediately.
- **Graceful Onboarding**: If Cryptomator is not installed, the panel provides a 1-click button to install `cryptomator-bin` from AUR.
- **Keyboard Friendly**:
  - `l` / `L`: Lock all vaults
  - `r` / `R`: Refresh status
  - `o` / `O`: Open Cryptomator GUI
  - `esc`: Close popup / cancel password prompt

## Bar Mouse Controls

- **Left Click**: Open / close the vaults panel.
- **Right Click**: Quick lock all vaults.
- **Middle Click**: Launch/focus the Cryptomator desktop application.

## IPC Commands

You can control the plugin via Quickshell IPC or keybindings:

```bash
quickshell ipc -p /usr/share/omarchy/shell call pandac.cryptomator toggle
quickshell ipc -p /usr/share/omarchy/shell call pandac.cryptomator lockAll
quickshell ipc -p /usr/share/omarchy/shell call pandac.cryptomator status
quickshell ipc -p /usr/share/omarchy/shell call pandac.cryptomator refresh
```

## Requirements

- `cryptomator` (Desktop GUI)
- `cryptomator-cli` (Official headless CLI tool, installed in `~/.local/bin/cryptomator-cli`)
- `fuse3` / `fusermount3` (installed on Omarchy by default)
- `python3`

