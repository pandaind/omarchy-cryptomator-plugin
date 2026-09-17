#!/usr/bin/env python3
"""Process launching and cleanup helpers shared by the lock/unlock flows."""

import sys
sys.dont_write_bytecode = True

import os
import re
import subprocess
import time


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


def terminate_matching(pattern, force=False):
    """SIGTERM (then SIGKILL if force) every process whose command line matches
    pattern, ignoring this process itself."""
    my_pid = os.getpid()
    try:
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return
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


def cleanup_cli_for_mount(mount_point, force=False):
    """Ensure any cryptomator-cli process associated with mount_point terminates."""
    if not mount_point:
        return
    pattern = f"cryptomator-cli.*--mountPoint={re.escape(str(mount_point))}"
    terminate_matching(pattern, force=force)


def cleanup_cli_for_vault(vault_path, force=False):
    """Ensure any cryptomator-cli process associated with vault_path terminates."""
    if not vault_path:
        return
    pattern = f"cryptomator-cli.*{re.escape(str(vault_path))}"
    terminate_matching(pattern, force=force)
