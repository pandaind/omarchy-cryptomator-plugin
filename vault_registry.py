#!/usr/bin/env python3
"""Persistent registry of known vaults (vaults.json) and vault lifecycle
operations (add, remove, create) that touch it."""

import sys
sys.dont_write_bytecode = True

import json
import os
import shutil
import uuid
from pathlib import Path

# Vault creation module (pure Python, no dependencies)
try:
    from vault_create import create_vault as _create_vault_impl
    _VAULT_CREATE_AVAILABLE = True
except ImportError:
    _VAULT_CREATE_AVAILABLE = False


def write_json_secure(path: Path, data):
    """Atomically write JSON data with restrictive 0600 permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        fd = os.open(temp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def get_vaults_file():
    """Return path to persistent vaults registry outside the plugin directory.
    This prevents Quickshell's plugin watcher from triggering a full plugin reload on every edit.
    """
    data_dir = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    storage_dir = Path(data_dir) / "omarchy-cryptomator-plugin"
    storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_file = storage_dir / "vaults.json"

    if not target_file.exists():
        legacy_file = Path(data_dir) / "pandac.cryptomator" / "vaults.json"
        if legacy_file.exists():
            try:
                shutil.copy2(legacy_file, target_file)
                return target_file
            except Exception:
                pass
        plugin_file = Path(__file__).resolve().parent / "vaults.json"
        if plugin_file.exists():
            try:
                shutil.copy2(plugin_file, target_file)
            except Exception:
                pass
    return target_file


def add_vault(vault_path, display_name=None):
    """Add a vault directory to local vaults.json."""
    if not vault_path:
        print("Vault path required", file=sys.stderr)
        return False

    path_obj = Path(vault_path).expanduser().resolve()
    if not path_obj.exists():
        print(f"Path does not exist: {path_obj}", file=sys.stderr)
        return False
    if not path_obj.is_dir():
        print(f"Path is not a directory: {path_obj}", file=sys.stderr)
        return False

    name = display_name or path_obj.name
    vaults_file = get_vaults_file()

    vaults = []
    if vaults_file.exists():
        try:
            with open(vaults_file, "r", encoding="utf-8") as f:
                vaults = json.load(f)
                if not isinstance(vaults, list):
                    vaults = []
        except Exception:
            vaults = []

    for v in vaults:
        if Path(v.get("path", "")).expanduser().resolve() == path_obj:
            print(f"Vault already registered: {name}")
            return True

    data_dir = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    default_mnt = str(Path(data_dir) / "Cryptomator" / "mnt" / name)

    vaults.append({
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "path": str(path_obj),
        "mountPoint": default_mnt,
        "readOnly": False,
    })

    write_json_secure(vaults_file, vaults)

    print(f"Added vault '{name}' ({path_obj})")
    return True


def remove_vault(vault_path):
    """Remove a vault from plugin vaults.json and/or the GUI app settings.json."""
    if not vault_path:
        print("Vault path required", file=sys.stderr)
        return False

    path_obj = Path(vault_path).expanduser().resolve()
    removed_any = False

    # 1. Remove from plugin's vaults.json
    vaults_file = get_vaults_file()
    if vaults_file.exists():
        try:
            with open(vaults_file, "r", encoding="utf-8") as f:
                vaults = json.load(f)
            if isinstance(vaults, list):
                new_vaults = [v for v in vaults if Path(v.get("path", "")).expanduser().resolve() != path_obj]
                if len(new_vaults) < len(vaults):
                    write_json_secure(vaults_file, new_vaults)
                    removed_any = True
        except Exception as e:
            print(f"Warning: could not update vaults.json: {e}", file=sys.stderr)

    # 2. Remove from GUI app's settings.json (if it exists)
    settings_candidates = [
        Path(os.environ.get("XDG_CONFIG_HOME", "")) / "Cryptomator" / "settings.json",
        Path.home() / ".config" / "Cryptomator" / "settings.json",
        Path.home() / ".Cryptomator" / "settings.json",
    ]
    for settings_file in settings_candidates:
        if not settings_file.exists():
            continue
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            dirs = settings.get("directories", [])
            if not isinstance(dirs, list):
                continue
            new_dirs = [d for d in dirs if Path(d.get("path", "")).expanduser().resolve() != path_obj]
            if len(new_dirs) < len(dirs):
                settings["directories"] = new_dirs
                with open(settings_file, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=2)
                removed_any = True
                break
        except Exception as e:
            print(f"Warning: could not update settings.json: {e}", file=sys.stderr)

    if not removed_any:
        print(f"Vault not found in any registry: {path_obj}", file=sys.stderr)
        return False

    print("Vault removed successfully")
    return True


def create_new_vault(vault_path, password, display_name=None):
    """Create a brand new Cryptomator vault at vault_path with the given password."""
    if not vault_path:
        print("Vault path required", file=sys.stderr)
        return False
    if not password:
        print("Password required to create vault", file=sys.stderr)
        return False

    if not _VAULT_CREATE_AVAILABLE:
        print("vault_create module not available", file=sys.stderr)
        return False

    path_obj = Path(vault_path).expanduser().resolve()

    # Allow creating in an empty or new directory
    if path_obj.exists() and path_obj.is_dir():
        contents = list(path_obj.iterdir())
        if any(f.name in ('masterkey.cryptomator', 'vault.cryptomator') for f in contents):
            print(f"A Cryptomator vault already exists at {path_obj}", file=sys.stderr)
            return False
        if contents:
            print(f"Directory is not empty: {path_obj}", file=sys.stderr)
            return False

    result = _create_vault_impl(str(path_obj), password)
    if not result.get('ok'):
        print(result.get('error', 'Failed to create vault'), file=sys.stderr)
        return False

    # Register in vaults.json
    return add_vault(str(path_obj), display_name)
