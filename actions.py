#!/usr/bin/env python3
"""Cryptomator action runner for Omarchy shell with bundled cryptomator-cli support."""

import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

# Vault creation module (pure Python, no dependencies)
try:
    from vault_create import create_vault as _create_vault_impl
    _VAULT_CREATE_AVAILABLE = True
except ImportError:
    _VAULT_CREATE_AVAILABLE = False


def find_cryptomator_cli():
    """Locate cryptomator-cli for non-password operations (status, lock, reveal).

    Checks bundled vendor/, user-installed bundle, and ~/.local/bin.
    Do NOT use this for password-bearing operations — use find_trusted_cli().
    """
    plugin_dir = Path(__file__).resolve().parent

    # 1. Bundled inside the plugin directory (integrity-verified on setup)
    bundled = plugin_dir / "vendor" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)

    # 2. In user's local share directory (written by setup_bundle after checksum verification)
    user_bundle = Path.home() / ".local" / "share" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if user_bundle.is_file() and os.access(user_bundle, os.X_OK):
        return str(user_bundle)

    # 3. In ~/.local/bin (user-controlled; acceptable for non-password ops only)
    local_bin = Path.home() / ".local" / "bin" / "cryptomator-cli"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)

    return None


def find_trusted_cli():
    """Locate cryptomator-cli for password-bearing operations ONLY.

    Accepts ONLY paths written by setup_bundle() after pinned SHA-256 verification:
      1. vendor/cryptomator-cli/ inside the plugin directory
      2. ~/.local/share/cryptomator-cli/ (installer artifact)

    ~/.local/bin and ambient PATH are intentionally excluded: those paths are
    user-writable and unverified. A rogue binary placed there could capture vault
    passphrases that are delivered over stdin.
    """
    plugin_dir = Path(__file__).resolve().parent

    # 1. Bundled vendor/ — only written by setup_bundle() after SHA-256 verification
    bundled = plugin_dir / "vendor" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)

    # 2. ~/.local/share/cryptomator-cli/ — also only written by setup_bundle()
    user_bundle = Path.home() / ".local" / "share" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if user_bundle.is_file() and os.access(user_bundle, os.X_OK):
        return str(user_bundle)

    return None


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


def cleanup_cli_for_mount(mount_point, force=False):
    """Ensure any cryptomator-cli process associated with mount_point terminates."""
    if not mount_point:
        return
    my_pid = os.getpid()
    try:
        escaped_mount = re.escape(str(mount_point))
        pattern = f"cryptomator-cli.*--mountPoint={escaped_mount}"
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            pids = [int(p) for p in res.stdout.split() if p.isdigit() and int(p) != my_pid]
            for pid in pids:
                try:
                    os.kill(pid, 15)  # SIGTERM
                except OSError:
                    pass
            if force and pids:
                time.sleep(0.3)
                for pid in pids:
                    try:
                        os.kill(pid, 9)  # SIGKILL
                    except OSError:
                        pass
    except Exception:
        pass


def cleanup_cli_for_vault(vault_path, force=False):
    """Ensure any cryptomator-cli process associated with vault_path terminates."""
    if not vault_path:
        return
    my_pid = os.getpid()
    try:
        escaped_vault = re.escape(str(vault_path))
        pattern = f"cryptomator-cli.*{escaped_vault}"
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            pids = [int(p) for p in res.stdout.split() if p.isdigit() and int(p) != my_pid]
            for pid in pids:
                try:
                    os.kill(pid, 15)  # SIGTERM
                except OSError:
                    pass
            if force and pids:
                time.sleep(0.3)
                for pid in pids:
                    try:
                        os.kill(pid, 9)  # SIGKILL
                    except OSError:
                        pass
    except Exception:
        pass


def close_file_manager_for_mount(mount_point=None, vault_path=None):
    """Close any open file manager windows displaying this mount point or vault."""
    targets = set()
    if mount_point and str(mount_point).strip():
        mp = str(mount_point).strip()
        targets.add(Path(mp).name.lower())      # just the folder name (what FM title shows)
        targets.add(mp.lower())                 # full path (some FMs show it)
    if vault_path and str(vault_path).strip():
        vp = str(vault_path).strip()
        targets.add(Path(vp).name.lower())
        targets.add(vp.lower())

    # Remove empty strings that would cause false-positive matches on any window
    targets.discard("")

    if not targets:
        return

    # Build a minimal env for hyprctl. Under the Quickshell /usr/bin/env -i
    # sandbox, HYPRLAND_INSTANCE_SIGNATURE is stripped from the process
    # environment, so hyprctl cannot locate the compositor socket and outputs
    # nothing. Explicitly forward the vars hyprctl needs.
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
            fm_classes = {
                "org.gnome.nautilus", "nautilus", "org.kde.dolphin", "dolphin",
                "thunar", "nemo", "pcmanfm", "io.elementary.files",
                "org.gnome.files",  # Nautilus alternate class on some distros
            }
            for c in clients:
                c_class = str(c.get("class", "")).lower()
                c_title = str(c.get("title", "")).lower()
                c_initial = str(c.get("initialTitle", "")).lower()
                addr = c.get("address")
                if not addr:
                    continue
                is_fm = any(fm in c_class for fm in fm_classes)
                matches = any(t in c_title or t in c_initial for t in targets)
                if is_fm and matches:
                    # Use hyprctl eval with Lua API — hyprctl dispatch does not
                    # accept Lua expressions and will error on address: syntax
                    lua = f'hl.dsp.window.close({{ window = "address:{addr}" }})'
                    subprocess.run(
                        ["hyprctl", "eval", lua],
                        capture_output=True,
                        check=False,
                        env=hypr_env,
                    )
    except Exception:
        pass


def lock_mount(mount_point, vault_path=None):
    """Unmount/lock a specific mount point using fusermount3, forcing lazy unmount if busy."""
    # 1. Close any file manager window currently viewing this folder
    close_file_manager_for_mount(mount_point, vault_path)

    # 2. If mount_point not provided, try to find it from vault_path or mounts
    if not mount_point and vault_path:
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and (Path(vault_path).name in parts[1] or "cryptomator" in parts[1].lower()):
                        mount_point = parts[1]
                        break
        except Exception:
            pass

    if not mount_point or not os.path.exists(mount_point):
        if vault_path:
            cleanup_cli_for_vault(vault_path, force=True)
        return True

    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    if not tool:
        print("Neither fusermount3 nor fusermount found.", file=sys.stderr)
        return False

    # 3. Try standard unmount
    res = subprocess.run([tool, "-u", str(mount_point)], check=False, capture_output=True, text=True)

    # 4. If busy, try lazy unmount (-z) so it forces unmount immediately
    if res.returncode != 0:
        res = subprocess.run([tool, "-u", "-z", str(mount_point)], check=False, capture_output=True, text=True)

    if res.returncode != 0:
        gio = shutil.which("gio")
        if gio:
            subprocess.run([gio, "mount", "-u", str(mount_point)], check=False, capture_output=True)

    # 5. Clean up any lingering CLI process
    cleanup_cli_for_mount(mount_point, force=True)
    if vault_path:
        cleanup_cli_for_vault(vault_path, force=True)

    # Re-verify if unmounted from /proc/mounts
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            is_still_mounted = str(mount_point) in f.read()
    except Exception:
        is_still_mounted = os.path.ismount(str(mount_point))

    return not is_still_mounted


def lock_all():
    """Find all active Cryptomator mounts and lock them."""
    script_dir = Path(__file__).resolve().parent
    status_script = script_dir / "status.py"
    if not status_script.exists():
        return

    # Use an explicit absolute python3 path and forward only the env vars that
    # status.py needs. Under the Quickshell /usr/bin/env -i sandbox the inherited
    # environment is stripped, so without forwarding XDG vars status.py cannot
    # locate vaults.json and silently returns an empty vault list.
    python_bin = shutil.which("python3") or "/usr/bin/python3"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(Path.home())),
    }
    for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
        val = os.environ.get(key)
        if val:
            env[key] = val

    res = subprocess.run(
        [python_bin, "-B", str(status_script)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if res.returncode != 0:
        return
    try:
        data = json.loads(res.stdout)
        vaults = data.get("vaults", [])
        for v in vaults:
            if v.get("isMounted"):
                lock_mount(v.get("mountPoint"), v.get("path"))
        # Force clean any remaining cryptomator-cli processes
        res_pgrep = subprocess.run(
            ["pgrep", "-f", "cryptomator-cli unlock"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_pgrep.returncode == 0:
            for pid_str in res_pgrep.stdout.split():
                try:
                    os.kill(int(pid_str), 15)
                except OSError:
                    pass
    except Exception as e:
        print(f"Error locking all vaults: {e}", file=sys.stderr)


def unlock_vault(vault_path):
    """Direct GUI unlock is disabled. Use inline unlock with password via bundled cryptomator-cli."""
    print("GUI unlock is disabled; using bundled CLI.", file=sys.stderr)
    return False


def unlock_with_password(vault_path, mount_point, password):
    """Unlock a Cryptomator vault headlessly using bundled or system cryptomator-cli."""
    if not vault_path or not os.path.exists(vault_path):
        print(f"Vault path does not exist: {vault_path}", file=sys.stderr)
        return False

    if not password:
        print("Password cannot be empty", file=sys.stderr)
        return False

    cli = find_trusted_cli()
    if not cli:
        print(
            "cryptomator-cli bundle not found. Run setup-bundle first to install the "
            "verified bundle. Only the integrity-verified bundle may be used for "
            "password-bearing operations.",
            file=sys.stderr,
        )
        return False

    if not mount_point:
        vault_name = Path(vault_path).name
        data_dir = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        mount_point = str(Path(data_dir) / "Cryptomator" / "mnt" / vault_name)

    os.makedirs(mount_point, exist_ok=True)

    if os.path.ismount(mount_point):
        print(f"Vault is already mounted at {mount_point}")
        return True

    # Ensure any stale or lingering process for this mount or vault is terminated
    cleanup_cli_for_mount(mount_point, force=True)
    cleanup_cli_for_vault(vault_path, force=True)

    log_dir = Path.home() / ".local" / "state" / "cryptomator"
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', Path(vault_path).name) or "vault"
    try:
        log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        log_path = log_dir / f"{safe_name}.log"
        log_fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    except OSError:
        tmp_handle = tempfile.NamedTemporaryFile(prefix=f"cryptomator-{safe_name}-", suffix=".log", delete=False)
        log_path = Path(tmp_handle.name)
        log_fd = tmp_handle.fileno()

    cmd = [
        cli,
        "unlock",
        "--password:stdin",
        f"--mountPoint={mount_point}",
        "--mounter=org.cryptomator.frontend.fuse.mount.LinuxFuseMountProvider",
        vault_path,
    ]

    try:
        with open(log_fd, "w", encoding="utf-8", closefd=True) as log_f:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            proc.stdin.write((password + "\n").encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()
    except Exception as err:
        print(f"Failed to start unlock process: {err}", file=sys.stderr)
        return False

    # Poll for success or failure
    start_time = time.time()
    while time.time() - start_time < 8.0:
        ret = proc.poll()
        if ret is not None:
            err_msg = "Incorrect password"
            try:
                if log_path.exists():
                    content = log_path.read_text(encoding="utf-8", errors="replace")
                    if "InvalidPassphraseException" in content:
                        err_msg = "Incorrect password"
                    elif "already mounted" in content.lower() or "busy" in content.lower():
                        err_msg = "Mount point is busy or in use"
                    else:
                        for line in content.splitlines():
                            line = line.strip()
                            if line and not line.startswith("Enter value for") and not line.startswith("at "):
                                err_msg = line
                                break
            except OSError:
                pass
            print(err_msg, file=sys.stderr)
            return False

        if os.path.ismount(mount_point):
            print(f"Successfully unlocked and mounted at {mount_point}")
            return True

        time.sleep(0.15)

    if os.path.ismount(mount_point):
        print(f"Successfully unlocked and mounted at {mount_point}")
        return True

    print("Unlock timed out", file=sys.stderr)
    try:
        proc.terminate()
    except OSError:
        pass
    return False


def setup_bundle():
    """Download and extract official cryptomator-cli into vendor/ directory."""
    machine = platform.machine().lower()
    if machine in ["x86_64", "amd64"]:
        arch = "x64"
    elif machine in ["aarch64", "arm64"]:
        arch = "aarch64"
    else:
        print(f"Unsupported architecture: {machine}", file=sys.stderr)
        return False

    OFFICIAL_SHA256 = {
        "x64": "6c2ac174f94a2ff30fdfa00ac43669703f1bca1fa633a762dc336bf9d794b1cb",
        "aarch64": "bd8d0dc62a707d7b378027772e16298333cfbe8e17ec235188f9bb50521dbb66",
    }

    version = "0.6.2"
    url = f"https://github.com/cryptomator/cli/releases/download/{version}/cryptomator-cli-{version}-linux-{arch}.zip"

    plugin_dir = Path(__file__).resolve().parent
    vendor_dir = plugin_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    target_dir = vendor_dir / "cryptomator-cli"

    zip_path = vendor_dir / f"cryptomator-cli-{version}.zip"

    print(f"Downloading cryptomator-cli {version} from {url}...")
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        print(f"Download failed: {e}", file=sys.stderr)
        return False

    print("Verifying binary checksum...")
    hasher = hashlib.sha256()
    try:
        with open(zip_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        computed_sha = hasher.hexdigest()
        expected_sha = OFFICIAL_SHA256.get(arch)
        if computed_sha != expected_sha:
            print(f"Security error: Checksum mismatch for downloaded archive!", file=sys.stderr)
            print(f"Expected: {expected_sha}", file=sys.stderr)
            print(f"Got:      {computed_sha}", file=sys.stderr)
            zip_path.unlink(missing_ok=True)
            return False
    except Exception as e:
        print(f"Checksum verification failed: {e}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return False

    print("Extracting bundle safely...")
    try:
        if target_dir.exists():
            shutil.rmtree(target_dir)

        resolved_vendor = vendor_dir.resolve()
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Zip Slip prevention: check destination for all members
            for member in zf.infolist():
                dest = (vendor_dir / member.filename).resolve()
                if not (dest == resolved_vendor or str(dest).startswith(str(resolved_vendor) + "/")):
                    raise ValueError(f"Potential Zip Slip path traversal detected: {member.filename}")
            zf.extractall(vendor_dir)
        zip_path.unlink(missing_ok=True)

        bin_file = target_dir / "bin" / "cryptomator-cli"
        if bin_file.exists():
            bin_file.chmod(bin_file.stat().st_mode | 0o755)
        launcher = target_dir / "lib" / "libapplauncher.so"
        if launcher.exists():
            launcher.chmod(launcher.stat().st_mode | 0o755)

        # Cap memory heap to 96M in cryptomator-cli.cfg to prevent excessive RAM usage
        cfg_file = target_dir / "lib" / "app" / "cryptomator-cli.cfg"
        if cfg_file.exists():
            try:
                cfg_content = cfg_file.read_text(encoding="utf-8")
                cfg_content = re.sub(r'-Xmx\d+m', '-Xmx128m', cfg_content)
                cfg_file.write_text(cfg_content, encoding="utf-8")
            except Exception:
                pass

        print(f"Successfully installed verified cryptomator-cli to {target_dir}")
        return True
    except Exception as e:
        print(f"Extraction failed: {e}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return False


def _write_json_secure(path: Path, data):
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
            import shutil
            try:
                shutil.copy2(legacy_file, target_file)
                return target_file
            except Exception:
                pass
        plugin_file = Path(__file__).resolve().parent / "vaults.json"
        if plugin_file.exists():
            try:
                import shutil
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

    import uuid
    vaults.append({
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "path": str(path_obj),
        "mountPoint": default_mnt,
        "readOnly": False,
    })

    _write_json_secure(vaults_file, vaults)

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
                    _write_json_secure(vaults_file, new_vaults)
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

    print(f"Vault removed successfully")
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
        return run_detached([xdg_open, mount_point])
    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: actions.py <lock|lock-all|unlock|unlock-password|add-vault|remove-vault|setup-bundle|launch|reveal> [arg] [arg2]")
        sys.exit(1)

    action = sys.argv[1].lower()
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "lock":
        arg2 = sys.argv[3] if len(sys.argv) > 3 else ""
        if not lock_mount(arg, arg2):
            sys.exit(1)
    elif action == "lock-all":
        lock_all()
    elif action == "unlock":
        unlock_vault(arg)
    elif action == "unlock-password":
        mount_point = sys.argv[3] if len(sys.argv) > 3 else ""
        password = sys.stdin.readline().rstrip("\r\n")
        if not unlock_with_password(arg, mount_point, password):
            sys.exit(1)
    elif action == "setup-bundle":
        if not setup_bundle():
            sys.exit(1)
    elif action == "add-vault":
        name = sys.argv[3] if len(sys.argv) > 3 else None
        if not add_vault(arg, name):
            sys.exit(1)
    elif action == "create-vault":
        name = sys.argv[3] if len(sys.argv) > 3 else None
        password = sys.stdin.readline().rstrip("\r\n")
        if not create_new_vault(arg, password, name):
            sys.exit(1)
    elif action == "remove-vault":
        if not remove_vault(arg):
            sys.exit(1)
    elif action == "launch":
        launch_cryptomator()
    elif action == "reveal":
        reveal_in_file_manager(arg)
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
