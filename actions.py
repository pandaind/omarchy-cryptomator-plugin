#!/usr/bin/env python3
"""Cryptomator action runner for Omarchy shell."""

import os
import shutil
import subprocess
import sys
import time
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


def cleanup_cli_for_mount(mount_point):
    """Ensure any cryptomator-cli process associated with mount_point terminates."""
    if not mount_point:
        return
    try:
        res = subprocess.run(["pgrep", "-f", f"--mountPoint={mount_point}"], capture_output=True, text=True, check=False)
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
    """Launch Cryptomator GUI with the vault path to prompt unlock."""
    cryptomator_bin = shutil.which("cryptomator")
    if not cryptomator_bin:
        print("Cryptomator not installed", file=sys.stderr)
        return False
    return run_detached([cryptomator_bin, vault_path])


def unlock_with_password(vault_path, mount_point, password):
    """Unlock a Cryptomator vault headlessly using cryptomator-cli."""
    if not vault_path or not os.path.exists(vault_path):
        print(f"Vault path does not exist: {vault_path}", file=sys.stderr)
        return False

    if not password:
        print("Password cannot be empty", file=sys.stderr)
        return False

    cli = shutil.which("cryptomator-cli")
    if not cli:
        fallback = Path.home() / ".local" / "bin" / "cryptomator-cli"
        if fallback.exists():
            cli = str(fallback)

    if not cli:
        print("cryptomator-cli is not installed. Please install it or open the desktop app.", file=sys.stderr)
        return False

    if not mount_point:
        vault_name = Path(vault_path).name
        data_dir = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        mount_point = str(Path(data_dir) / "Cryptomator" / "mnt" / vault_name)

    os.makedirs(mount_point, exist_ok=True)

    # Check if already mounted
    if os.path.ismount(mount_point):
        print(f"Vault is already mounted at {mount_point}")
        return True

    log_dir = Path.home() / ".local" / "state" / "cryptomator"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{Path(vault_path).name}.log"
    except OSError:
        log_path = Path("/tmp") / f"cryptomator-{Path(vault_path).name}.log"

    cmd = [
        cli,
        "unlock",
        "--password:stdin",
        f"--mountPoint={mount_point}",
        "--mounter=org.cryptomator.frontend.fuse.mount.LinuxFuseMountProvider",
        vault_path,
    ]

    try:
        with open(log_path, "w", encoding="utf-8") as log_f:
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
            # Process exited prematurely -> unlock failed
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
    for term in ["ghostty", "alacritty", "kitty", "foot", "xterm"]:
        term_bin = shutil.which(term)
        if term_bin:
            return run_detached([term_bin, "-e", "bash", "-c", "yay -S cryptomator-bin; read -p 'Press enter to exit'"])
    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: actions.py <lock|lock-all|unlock|unlock-password|launch|reveal|install> [arg] [arg2]")
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
