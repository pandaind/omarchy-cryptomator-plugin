# Agent Guidelines: Omarchy Cryptomator Plugin

This document defines architecture, operational rules, runtime invariants, and git workflows for AI agents and developers working on the `omarchy-cryptomator-plugin` repository.

---

## 1. Project Overview & Architecture

The **Omarchy Cryptomator Plugin** is a native Omarchy Quattro status bar widget and control panel for managing Cryptomator encrypted vaults without requiring the desktop GUI or system Java runtime.

### Key Components

- **`manifest.json`**: Plugin descriptor for Omarchy shell (`kinds: ["bar-widget"]`, entry point `Panel.qml`).
- **`Panel.qml`**: User interface containing the status bar button, popup keyboard panel, vault cards, and forms.
- **`Service.qml`**: Background process orchestrator, reactive state container (`totalVaults`, `unlockedCount`, `vaults`), and IPC handler.
- **`Model.js`**: Pure JavaScript helpers for JSON parsing, path shortening, and status formatting.
- **`status.py`**: Fast, headless inspector querying active FUSE mounts (`/proc/mounts`) and registered vaults.
- **`actions.py`**: CLI action runner handling vault mounting, unmounting, registration, and process lifecycle.
- **`vault_create.py`**: Pure Python Cryptomator v8 vault generator leveraging `libcrypto.so.3` via `ctypes` (RFC 5297 AES-SIV / AES-CMAC).
- **`vendor/cryptomator-cli/`**: Embedded headless Cryptomator CLI packaged with a memory-tuned OpenJDK JRE.

---

## 2. Critical Runtime Invariants

### A. Python Bytecode Suppression (Strict Requirement)
- **Problem**: Omarchy's shell runs `inotifywait -m -r` on `~/.config/omarchy/plugins/`. If Python writes `.pyc` files into `__pycache__/` inside the plugin directory, `inotifywait` detects filesystem modification and triggers `shell.reloadPlugins()`, causing **all status bar widgets to reset and flicker**.
- **Rule 1**: Every Python file (`status.py`, `actions.py`, `vault_create.py`) MUST have bytecode suppression at the very top:
  ```python
  import sys
  sys.dont_write_bytecode = True
  ```
- **Rule 2**: Every Python process executed from `Service.qml` MUST pass the `-B` flag:
  ```qml
  statusProcess.command = ["python3", "-B", statusScript]
  ```
- **Rule 3**: Never commit `__pycache__` directories or `.pyc` files.

### B. Persistent State Isolation
- **Rule**: Never store runtime state or registries inside the plugin directory.
- All persistent configuration must be written to:
  `$XDG_DATA_HOME/omarchy-cryptomator-plugin/vaults.json` (fallback: `~/.local/share/omarchy-cryptomator-plugin/vaults.json`).

### C. Reactive State & Asynchronous Timing
- **Property Binding**: Use the strongly-typed integer `cryptomator.totalVaults` (not `vaults.length` on `property var`) for QML visibility and condition bindings.
- **Form Closure Sequence**: When adding or creating a vault, `Service.qml` must await the `status.py` refresh completion callback before setting `addingVaultProcess = false` and emitting finished signals. This prevents the empty state card from flashing on screen before the new vault card mounts.

### D. Safe Process Lifecycle & Unmounting
- Unmounting must first attempt clean unmount with `fusermount3 -u <mountpoint>`. If busy, fall back to lazy unmount with `fusermount3 -u -z <mountpoint>`.
- Auto-close any open Hyprland windows browsing the vault directory before unmounting.
- The bundled Java runtime memory must remain capped via `-Xmx128m` in `vendor/cryptomator-cli/lib/app/cryptomator-cli.cfg`.

---

## 3. Local Development & Testing

1. **Repository vs. Installed Plugin**:
   - Working repository: `/home/pandac/Projects/omarchy-cryptomator-plugin`
   - Active Omarchy shell plugin directory: `~/.config/omarchy/plugins/omarchy-cryptomator-plugin`

2. **Syncing & Reloading**:
   - After making code changes in the working repository, synchronize the files to the active plugin directory:
     ```bash
     rsync -av --exclude='.git' --exclude='__pycache__' /home/pandac/Projects/omarchy-cryptomator-plugin/ ~/.config/omarchy/plugins/omarchy-cryptomator-plugin/
     ```
   - Restart the Omarchy shell to reload QML components into memory:
     ```bash
     omarchy-restart-shell
     ```

3. **Verifying Zero-Reload Invariant**:
   - Confirm that adding or locking vaults does not trigger Omarchy plugin reload events:
     ```bash
     journalctl --user -n 30 --no-pager | grep "Local plugin changed"
     ```
     *(The output must be empty).*

---

## 4. Git & Release Operations

1. **Commit Rule**:
   - **Always commit when done**. Every discrete change or bug fix must be committed to git immediately after verification.
   - Use concise, imperative commit subjects (e.g. `Fix duplicate action buttons when no vaults exist`).

2. **Branching & Remote**:
   - Main branch is `master`.
   - Always push verified commits to `origin/master`.

3. **Tagging & Release Workflow**:
   - GitHub Actions workflow (`.github/workflows/release.yml`) automatically generates and publishes an official GitHub Release whenever a tag matching `v*` is pushed.
   - **DO NOT create or push git tags unless explicitly instructed by the user**.

4. **Author & Privacy**:
   - The declared author and copyright holder across all metadata (`manifest.json`, `LICENSE`, `README.md`) must be **`pandac.in`**.
   - Never commit private credentials, absolute user paths, or sensitive debugging artifacts.
