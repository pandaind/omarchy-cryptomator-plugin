#!/usr/bin/env python3
"""Locking and password-based unlocking of Cryptomator vaults.

Password-bearing execution always goes through cli_trust.open_trusted_cli_for_exec(),
which hands back a verified, already-open file descriptor for the cryptomator-cli
launcher — see that module for why a path string alone is not trustworthy here.
"""

import sys
sys.dont_write_bytecode = True

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import cli_trust
import file_manager
import process_utils

# How long to wait for a password-correct unlock to actually finish mounting before
# giving up. Needs headroom for a cold JVM start plus, since open_trusted_cli_for_exec
# now snapshots and re-verifies the whole bundle on every attempt, that snapshot step
# itself -- a too-tight budget here would report a slow-but-genuine unlock as a timeout
# failure, even though the mount completes moments later.
MOUNT_WAIT_TIMEOUT_SECONDS = 20.0


def lock_mount(mount_point, vault_path=None):
    """Unmount/lock a specific mount point using fusermount3, forcing lazy unmount if busy."""
    # 1. Close any file manager window currently viewing this folder
    file_manager.close_file_manager_for_mount(mount_point, vault_path)

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
            process_utils.cleanup_cli_for_vault(vault_path, force=True)
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
    process_utils.cleanup_cli_for_mount(mount_point, force=True)
    if vault_path:
        process_utils.cleanup_cli_for_vault(vault_path, force=True)

    # Opportunistically mop up any per-unlock bundle snapshots (see
    # cli_trust.open_trusted_cli_for_exec) whose scheduled cleanup never ran.
    cli_trust.sweep_stale_run_dirs(cli_trust.run_dir_root())

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

    # Forward only the env vars that status.py needs. Under the Quickshell
    # /usr/bin/env -i sandbox the inherited environment is stripped, so without
    # forwarding XDG vars status.py cannot locate vaults.json and silently returns an
    # empty vault list. Uses sys.executable rather than resolving python3 via PATH, for
    # the same reason as everywhere else this codebase spawns a Python subprocess: an
    # ambient-PATH lookup is same-uid-attacker-redirectable, while sys.executable is
    # the interpreter already running this code.
    python_bin = sys.executable or "/usr/bin/python3"
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
        process_utils.terminate_matching("cryptomator-cli unlock")
    except Exception as e:
        print(f"Error locking all vaults: {e}", file=sys.stderr)


def unlock_vault(vault_path):
    """Direct GUI unlock is disabled. Use inline unlock with password via bundled cryptomator-cli."""
    print("GUI unlock is disabled; using bundled CLI.", file=sys.stderr)
    return False


def unlock_with_password(vault_path, mount_point, password):
    """Unlock a Cryptomator vault headlessly using the verified bundled cryptomator-cli."""
    if not vault_path or not os.path.exists(vault_path):
        print(f"Vault path does not exist: {vault_path}", file=sys.stderr)
        return False

    if not password:
        print("Password cannot be empty", file=sys.stderr)
        return False

    cli_fd, cli_result, snapshot_dir = cli_trust.open_trusted_cli_for_exec()
    if cli_fd is None:
        print(cli_result, file=sys.stderr)
        return False

    try:
        return _run_unlock(vault_path, mount_point, password, cli_fd, snapshot_dir)
    finally:
        os.close(cli_fd)


def _run_unlock(vault_path, mount_point, password, cli_fd, snapshot_dir):
    """Drive the actual unlock subprocess. cli_fd must be an already-verified, open
    file descriptor for the cryptomator-cli launcher (see cli_trust.open_trusted_cli_for_exec);
    it is executed via /proc/self/fd so the verified inode, not a path lookup, is what
    actually runs. snapshot_dir is the private bundle snapshot cli_fd was opened from;
    this function is responsible for either cleaning it up immediately (if it never gets
    used) or scheduling its removal once the process reading it exits."""
    if not mount_point:
        vault_name = Path(vault_path).name
        data_dir = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        mount_point = str(Path(data_dir) / "Cryptomator" / "mnt" / vault_name)

    os.makedirs(mount_point, exist_ok=True)

    if os.path.ismount(mount_point):
        print(f"Vault is already mounted at {mount_point}")
        cli_trust.cleanup_run_dir_now(snapshot_dir)
        return True

    # Ensure any stale or lingering process for this mount or vault is terminated
    process_utils.cleanup_cli_for_mount(mount_point, force=True)
    process_utils.cleanup_cli_for_vault(vault_path, force=True)

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
        "cryptomator-cli",
        "unlock",
        "--password:stdin",
        f"--mountPoint={mount_point}",
        "--mounter=org.cryptomator.frontend.fuse.mount.LinuxFuseMountProvider",
        vault_path,
    ]

    proc = None
    try:
        try:
            with open(log_fd, "w", encoding="utf-8", closefd=True) as log_f:
                proc = subprocess.Popen(
                    cmd,
                    executable=f"/proc/self/fd/{cli_fd}",
                    pass_fds=(cli_fd,),
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
    finally:
        if proc is not None:
            cli_trust.schedule_run_dir_cleanup(snapshot_dir, proc.pid)
        else:
            cli_trust.cleanup_run_dir_now(snapshot_dir)

    # Poll for success or failure
    start_time = time.time()
    while time.time() - start_time < MOUNT_WAIT_TIMEOUT_SECONDS:
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
