#!/usr/bin/env python3
"""File manager window integration: closing windows over a locked vault, and
opening the mount directory when the user reveals or launches Cryptomator."""

import sys
sys.dont_write_bytecode = True

import json
import os
import shutil
import subprocess
from pathlib import Path

import process_utils

FILE_MANAGER_CLASSES = {
    "org.gnome.nautilus", "nautilus", "org.kde.dolphin", "dolphin",
    "thunar", "nemo", "pcmanfm", "io.elementary.files",
    "org.gnome.files",
}


def close_file_manager_for_mount(mount_point=None, vault_path=None):
    """Close any open file manager windows displaying this mount point or vault."""
    targets = set()
    mp_check = None
    vp_check = None

    if mount_point and str(mount_point).strip():
        mp_check = str(mount_point).strip()
        targets.add(Path(mp_check).name.lower())
        targets.add(mp_check.lower())
    if vault_path and str(vault_path).strip():
        vp_check = str(vault_path).strip()
        targets.add(Path(vp_check).name.lower())
        targets.add(vp_check.lower())

    targets.discard("")

    if not targets and not mp_check and not vp_check:
        return

    hypr_env = {"PATH": "/usr/bin:/bin"}
    for key in ("HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY"):
        val = os.environ.get(key)
        if val:
            hypr_env[key] = val

    try:
        res = subprocess.run(
            ["hyprctl", "clients", "-j"],
            capture_output=True,
            text=True,
            check=False,
            env=hypr_env,
        )
        if res.returncode == 0 and res.stdout.strip():
            clients = json.loads(res.stdout)
            for c in clients:
                c_class = str(c.get("class", "")).lower()
                c_title = str(c.get("title", "")).lower()
                c_initial = str(c.get("initialTitle", "")).lower()
                addr = c.get("address")
                pid = c.get("pid")

                if not addr:
                    continue

                is_fm = any(fm in c_class for fm in FILE_MANAGER_CLASSES)
                if not is_fm:
                    continue

                # 1. Check title matches
                matches = any(t in c_title or t in c_initial for t in targets)

                # 2. If title doesn't match, deeply check process open file descriptors
                # This catches file managers deeply nested inside a vault subfolder
                if not matches and pid:
                    fd_dir = f"/proc/{pid}/fd"
                    if os.path.isdir(fd_dir):
                        for fd in os.listdir(fd_dir):
                            try:
                                target = os.readlink(os.path.join(fd_dir, fd))
                                if (mp_check and target.startswith(mp_check)) or \
                                   (vp_check and target.startswith(vp_check)):
                                    matches = True
                                    break
                            except Exception:
                                pass

                if matches:
                    lua = f'hl.dsp.window.close({{ window = "address:{addr}" }})'
                    subprocess.run(
                        ["hyprctl", "dispatch", lua],
                        capture_output=True,
                        check=False,
                        env=hypr_env,
                    )
    except Exception:
        pass


def launch_cryptomator():
    """Reveal Cryptomator mount directory in file manager."""
    data_dir = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    mount_dir = Path(data_dir) / "Cryptomator" / "mnt"
    mount_dir.mkdir(parents=True, exist_ok=True)
    return reveal_in_file_manager(str(mount_dir))


def reveal_in_file_manager(mount_point):
    """Open mount directory in default file manager."""
    if not mount_point or not os.path.exists(mount_point):
        return False
    xdg_open = shutil.which("xdg-open")
    if xdg_open:
        return process_utils.run_detached([xdg_open, mount_point])
    return False
