#!/usr/bin/env python3
"""Cryptomator status detection helper for Omarchy shell."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_cryptomator_cli():
    """Locate the bundled or system cryptomator-cli binary."""
    plugin_dir = Path(__file__).resolve().parent

    # 1. Bundled inside the plugin directory
    bundled = plugin_dir / "vendor" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)

    # 2. In user's local share directory
    user_bundle = Path.home() / ".local" / "share" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if user_bundle.is_file() and os.access(user_bundle, os.X_OK):
        return str(user_bundle)

    # 3. In PATH
    which_cli = shutil.which("cryptomator-cli")
    if which_cli:
        return which_cli

    # 4. In ~/.local/bin
    local_bin = Path.home() / ".local" / "bin" / "cryptomator-cli"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)

    return None


def get_mount_points_dir():
    data_dir = os.environ.get("XDG_DATA_HOME")
    if data_dir:
        base = Path(data_dir)
    else:
        base = Path.home() / ".local" / "share"
    return base / "Cryptomator" / "mnt"


def parse_proc_mounts():
    """Parse /proc/mounts into a list of (target, fstype, source) tuples."""
    mounts = []
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3:
                    target = parts[1].encode("utf-8").decode("unicode_escape")
                    fstype = parts[2]
                    source = parts[0].encode("utf-8").decode("unicode_escape")
                    mounts.append((target, fstype, source))
    except OSError:
        pass
    return mounts


def is_directory_mounted(mount_path, proc_mounts):
    """Check if mount_path is an active mountpoint."""
    path_str = str(mount_path)
    if os.path.exists(path_str) and os.path.ismount(path_str):
        return True
    for target, _, _ in proc_mounts:
        if target == path_str:
            return True
    return False


def is_cryptomator_running():
    """Check if a cryptomator process is running."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", "cryptomator"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def read_settings():
    """Read Cryptomator settings.json from XDG or ~/.config."""
    config_dir = os.environ.get("XDG_CONFIG_HOME")
    candidates = []
    if config_dir:
        candidates.append(Path(config_dir) / "Cryptomator" / "settings.json")
    candidates.append(Path.home() / ".config" / "Cryptomator" / "settings.json")
    candidates.append(Path.home() / ".Cryptomator" / "settings.json")

    for p in candidates:
        if p.exists():
            try:
                with p.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def get_vaults_file():
    """Return path to persistent vaults registry outside the plugin directory.
    This prevents Quickshell's plugin watcher from triggering a full plugin reload on every edit.
    """
    data_dir = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    storage_dir = Path(data_dir) / "pandac.cryptomator"
    storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_file = storage_dir / "vaults.json"

    if not target_file.exists():
        plugin_file = Path(__file__).resolve().parent / "vaults.json"
        if plugin_file.exists():
            return plugin_file
    return target_file


def read_custom_vaults():
    """Read vaults configured directly in the plugin (standalone)."""
    vaults_file = get_vaults_file()
    if vaults_file.exists():
        try:
            with open(vaults_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def main():
    cli_path = find_cryptomator_cli()
    gui_path = shutil.which("cryptomator")

    cli_installed = (cli_path is not None)
    gui_installed = (gui_path is not None)
    is_bundled = bool(cli_path and "vendor/cryptomator-cli" in cli_path)

    # Installed if CLI is available (GUI is optional)
    installed = cli_installed or gui_installed
    running = is_cryptomator_running()

    settings = read_settings()
    custom_vaults = read_custom_vaults()

    # Combine custom vaults and GUI settings vaults (dedup by path)
    raw_dirs = []
    seen_paths = set()

    for item in custom_vaults:
        if isinstance(item, dict) and item.get("path"):
            p = str(Path(item["path"]).expanduser().resolve())
            if p not in seen_paths:
                seen_paths.add(p)
                raw_dirs.append(item)

    for item in settings.get("directories", []):
        if isinstance(item, dict) and item.get("path"):
            p = str(Path(item["path"]).expanduser().resolve())
            if p not in seen_paths:
                seen_paths.add(p)
                raw_dirs.append(item)

    mounts_base = get_mount_points_dir()
    proc_mounts = parse_proc_mounts()

    vaults = []
    unlocked_count = 0

    for item in raw_dirs:
        if not isinstance(item, dict):
            continue
        vault_id = str(item.get("id", ""))
        vault_path = str(item.get("path", ""))
        display_name = str(item.get("name", "") or item.get("displayName", "")) or os.path.basename(vault_path)

        default_mnt = mounts_base / display_name
        mounted = is_directory_mounted(default_mnt, proc_mounts)

        actual_mount = str(default_mnt) if default_mnt.exists() or mounted else ""
        if not mounted:
            for target, fstype, src in proc_mounts:
                if "fuse" in fstype.lower() and (display_name in target or vault_id in src):
                    mounted = True
                    actual_mount = target
                    break

        if mounted:
            unlocked_count += 1

        vaults.append({
            "id": vault_id,
            "name": display_name,
            "path": vault_path,
            "mountPoint": actual_mount or str(default_mnt),
            "isMounted": mounted,
            "exists": os.path.isdir(vault_path),
            "readOnly": bool(item.get("readOnly", item.get("usesReadOnlyMode", False))),
        })

    output = {
        "ok": True,
        "installed": installed,
        "cliInstalled": cli_installed,
        "guiInstalled": gui_installed,
        "isBundled": is_bundled,
        "cliPath": cli_path or "",
        "running": running,
        "totalVaults": len(vaults),
        "unlockedCount": unlocked_count,
        "vaults": vaults,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
