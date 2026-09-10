#!/usr/bin/env python3
"""Cryptomator action runner for Omarchy shell."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_detached(cmd):
    """Launch a command in the background detached from the current process."""
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError as err:
        print(f"Failed to launch {' '.join(cmd)}: {err}", file=sys.stderr)
        return False


def lock_mount(mount_point):
    """Unmount/lock a specific mount point using fusermount3."""
    if not mount_point or not os.path.exists(mount_point):
        return False
    # Check if fusermount3 or fusermount is available
    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    if not tool:
        print("Neither fusermount3 nor fusermount found.", file=sys.stderr)
        return False
    res = subprocess.run([tool, "-u", mount_point], check=False, capture_output=True, text=True)
    if res.returncode != 0:
        # Fallback to gio mount -u if needed
        gio = shutil.which("gio")
        if gio:
            subprocess.run([gio, "mount", "-u", mount_point], check=False, capture_output=True)
    return res.returncode == 0


def lock_all():
    """Find all active Cryptomator mounts and lock them."""
    script_dir = Path(__file__).parent
    status_script = script_dir / "status.py"
    if not status_script.exists():
        return
    res = subprocess.run([sys.executable, str(status_script)], check=False, capture_output=True, text=True)
    if res.returncode != 0:
        return
    import json
    try:
        data = json.loads(res.stdout)
        vaults = data.get("vaults", [])
        for v in vaults:
            if v.get("isMounted"):
                lock_mount(v.get("mountPoint"))
    except Exception as e:
        print(f"Error locking all vaults: {e}", file=sys.stderr)


def unlock_vault(vault_path):
    """Launch Cryptomator with the vault path to prompt unlock."""
    cryptomator_bin = shutil.which("cryptomator")
    if not cryptomator_bin:
        print("Cryptomator not installed", file=sys.stderr)
        return False
    return run_detached([cryptomator_bin, vault_path])


def launch_cryptomator():
    """Launch Cryptomator main window."""
    cryptomator_bin = shutil.which("cryptomator")
    if not cryptomator_bin:
        return False
    return run_detached([cryptomator_bin])


def reveal_in_file_manager(mount_point):
    """Open mount directory in default file manager."""
    if not mount_point or not os.path.exists(mount_point):
        return False
    xdg_open = shutil.which("xdg-open")
    if xdg_open:
        return run_detached([xdg_open, mount_point])
    return False


def install_cryptomator():
    """Launch terminal to install cryptomator via omarchy pkg."""
    omarchy_bin = shutil.which("omarchy")
    if omarchy_bin:
        return run_detached([omarchy_bin, "launch", "terminal", "omarchy", "pkg", "aur", "add", "cryptomator-bin"])
    # Fallback to yay in terminal
    for term in ["ghostty", "alacritty", "kitty", "foot", "xterm"]:
        term_bin = shutil.which(term)
        if term_bin:
            return run_detached([term_bin, "-e", "bash", "-c", "yay -S cryptomator-bin; read -p 'Press enter to exit'"])
    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: actions.py <lock|lock-all|unlock|launch|reveal|install> [arg]")
        sys.exit(1)

    action = sys.argv[1].lower()
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "lock":
        lock_mount(arg)
    elif action == "lock-all":
        lock_all()
    elif action == "unlock":
        unlock_vault(arg)
    elif action == "launch":
        launch_cryptomator()
    elif action == "reveal":
        reveal_in_file_manager(arg)
    elif action == "install":
        install_cryptomator()
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
