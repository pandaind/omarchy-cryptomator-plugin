#!/usr/bin/env python3
"""Cryptomator action runner for Omarchy shell with bundled cryptomator-cli support."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
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


def cleanup_cli_for_mount(mount_point):
    """Ensure any cryptomator-cli process associated with mount_point terminates."""
    if not mount_point:
        return
    try:
        escaped_mount = re.escape(str(mount_point))
        res = subprocess.run(["pgrep", "-f", f"--mountPoint={escaped_mount}"], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            for pid_str in res.stdout.split():
                try:
                    os.kill(int(pid_str), 15)  # SIGTERM
                except OSError:
                    pass
    except Exception:
        pass


def lock_mount(mount_point):
    """Unmount/lock a specific mount point using fusermount3."""
    if not mount_point or not os.path.exists(mount_point):
        return False
    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    if not tool:
        print("Neither fusermount3 nor fusermount found.", file=sys.stderr)
        return False
    res = subprocess.run([tool, "-u", mount_point], check=False, capture_output=True, text=True)
    if res.returncode != 0:
        gio = shutil.which("gio")
        if gio:
            subprocess.run([gio, "mount", "-u", mount_point], check=False, capture_output=True)
    cleanup_cli_for_mount(mount_point)
    return res.returncode == 0


def lock_all():
    """Find all active Cryptomator mounts and lock them."""
    script_dir = Path(__file__).resolve().parent
    status_script = script_dir / "status.py"
    if not status_script.exists():
        return
    res = subprocess.run([sys.executable, str(status_script)], check=False, capture_output=True, text=True)
    if res.returncode != 0:
        return
    try:
        data = json.loads(res.stdout)
        vaults = data.get("vaults", [])
        for v in vaults:
            if v.get("isMounted"):
                lock_mount(v.get("mountPoint"))
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

    cli = find_cryptomator_cli()
    if not cli:
        print("cryptomator-cli is not installed or bundled. Run setup-bundle first.", file=sys.stderr)
        return False

    if not mount_point:
        vault_name = Path(vault_path).name
        data_dir = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        mount_point = str(Path(data_dir) / "Cryptomator" / "mnt" / vault_name)

    os.makedirs(mount_point, exist_ok=True)

    if os.path.ismount(mount_point):
        print(f"Vault is already mounted at {mount_point}")
        return True

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
                    raise SecurityError(f"Potential Zip Slip path traversal detected: {member.filename}")
            zf.extractall(vendor_dir)
        zip_path.unlink(missing_ok=True)

        bin_file = target_dir / "bin" / "cryptomator-cli"
        if bin_file.exists():
            bin_file.chmod(bin_file.stat().st_mode | 0o755)
        launcher = target_dir / "lib" / "libapplauncher.so"
        if launcher.exists():
            launcher.chmod(launcher.stat().st_mode | 0o755)

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
    storage_dir = Path(data_dir) / "pandac.cryptomator"
    storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_file = storage_dir / "vaults.json"

    if not target_file.exists():
        plugin_file = Path(__file__).resolve().parent / "vaults.json"
        if plugin_file.exists():
            return plugin_file
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
        lock_mount(arg)
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
