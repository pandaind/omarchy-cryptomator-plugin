#!/usr/bin/env python3
"""Trust verification for cryptomator-cli.

This module is the single source of truth for deciding whether an installed
cryptomator-cli binary is safe to receive a vault passphrase over stdin. It
must stay isolated from download/install logic (see bundle_installer.py) and
from the unlock flow (see vault_unlock.py) so the verification rules can be
audited in one small place.
"""

import sys
sys.dont_write_bytecode = True

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

# How long a per-unlock bundle snapshot (see open_trusted_cli_for_exec) is left alone
# before the background cleanup process removes it. Must comfortably outlast JVM
# bootstrap and initial classloading, since those are the file accesses that actually
# need the snapshot; anything reached only much later would just fail to load rather
# than pose a security risk. A stale-directory sweep with a much longer threshold acts
# as a safety net if the cleanup process itself never got to run.
RUN_DIR_CLEANUP_DELAY_SECONDS = 45
RUN_DIR_STALE_AGE_SECONDS = 600

# SHA-256 of the official release archive, from Cryptomator's published checksums.
OFFICIAL_SHA256 = {
    "x64": "6c2ac174f94a2ff30fdfa00ac43669703f1bca1fa633a762dc336bf9d794b1cb",
    "aarch64": "bd8d0dc62a707d7b378027772e16298333cfbe8e17ec235188f9bb50521dbb66",
}

# SHA-256 of the exact launcher binary (bin/cryptomator-cli) extracted from the
# archives pinned in OFFICIAL_SHA256 above, computed once from the verified download.
TRUSTED_CLI_BINARY_SHA256 = {
    "x64": "dd673c6a879163fa42b552a92d0ae23d0963476fb42b51ea7caab51127a21ef3",
    "aarch64": "7d6ff4579320f2ea180487e7dcf9d4e4d1991590b0dd8ff3b616daf18defc134",
}

# SHA-256 over a manifest of every file's relative path and content-hash in the
# extracted, post-setup bundle tree (see bundle_manifest_sha256). This covers the
# supporting jars and config file, not just the launcher binary, so swapping any
# single file inside vendor/cryptomator-cli/ is detected before a password is sent.
TRUSTED_BUNDLE_MANIFEST_SHA256 = {
    "x64": "65fc6eccce543b552fc0438ee354b15544fbf08b77c816298e144627959037a2",
    "aarch64": "dc72ba8e41197a84d4a1522b51c1f5e1aa48cd870888d78def04dfc9d02b3738",
}


def detect_arch():
    """Map platform.machine() to the arch key used by the pinned trust tables."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return None


def file_sha256(path):
    """Return the SHA-256 hex digest of a file's contents."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def bundle_manifest_sha256(root: Path) -> str:
    """Compute a deterministic digest over every regular file's relative path and
    content hash under root, sorted for stability. Any added, removed, or modified
    file changes the result, so this catches tampering anywhere in the bundle tree
    (jars, native launcher, config), not just the single launcher binary."""
    manifest = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            manifest.update(rel.encode("utf-8") + b"\0" + file_sha256(p).encode("ascii") + b"\n")
    return manifest.hexdigest()


def make_tree_writable(root: Path):
    """Restore the owner-write bit across a locked-down bundle tree so it can be
    removed or replaced by a future setup-bundle run."""
    for p in root.rglob("*"):
        try:
            p.chmod(p.stat().st_mode | stat.S_IWUSR)
        except OSError:
            pass
    try:
        root.chmod(root.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass


def lock_down_bundle(root: Path):
    """Strip write permissions across a verified bundle so that tampering with any
    file requires an explicit permission change first, rather than a plain overwrite."""
    for p in root.rglob("*"):
        try:
            if p.is_dir():
                p.chmod(0o555)
            elif p.is_file():
                p.chmod(0o555 if os.access(p, os.X_OK) else 0o444)
        except OSError:
            pass
    try:
        root.chmod(0o555)
    except OSError:
        pass


def _open_verified_binary(path: Path, expected_sha256: str):
    """Open path with O_NOFOLLOW, verify its identity, and return the fd rewound to
    the start — or None if it fails any check.

    The caller must exec using this fd itself (e.g. subprocess.Popen(..., executable=
    f"/proc/self/fd/{fd}", pass_fds=(fd,))) rather than the path string. That closes
    the check-then-use race: even if the file at `path` is replaced immediately after
    this call returns, the fd still refers to the exact inode that was hashed here.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    ok = False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_uid != os.getuid():
            return None
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return None
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
        if hasher.hexdigest() != expected_sha256:
            return None
        os.lseek(fd, 0, os.SEEK_SET)
        ok = True
        return fd
    finally:
        if not ok:
            os.close(fd)


def _copy_tree_no_symlinks(src: Path, dst: Path):
    """Copy every regular file under src into dst, preserving relative paths and
    permission bits. Refuses to touch any symlink found anywhere in the tree, since a
    symlink could otherwise alias content outside the verified bundle and defeat the
    point of hashing the copy afterward."""
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_symlink():
            raise ValueError(f"refusing to copy symlink in trusted bundle: {rel}")
        dest = dst / rel
        if p.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
            shutil.copymode(p, dest)


_CLEANUP_SCRIPT = (
    "import os, shutil, stat, sys, time\n"
    "time.sleep(float(sys.argv[2]))\n"
    "root = sys.argv[1]\n"
    "for dirpath, dirnames, filenames in os.walk(root):\n"
    "    for name in dirnames + filenames:\n"
    "        p = os.path.join(dirpath, name)\n"
    "        try:\n"
    "            os.chmod(p, os.lstat(p).st_mode | stat.S_IWUSR)\n"
    "        except OSError:\n"
    "            pass\n"
    "try:\n"
    "    os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)\n"
    "except OSError:\n"
    "    pass\n"
    "shutil.rmtree(root, ignore_errors=True)\n"
)


def schedule_run_dir_cleanup(path: Path, delay_seconds: float = RUN_DIR_CLEANUP_DELAY_SECONDS):
    """Spawn a detached process that removes a per-unlock bundle snapshot after a delay.

    The snapshot must outlive the short-lived Python process that created it (that
    process returns almost immediately after starting cryptomator-cli, while the
    launched process keeps running for as long as the vault stays mounted), so cleanup
    can't happen in a normal try/finally here -- it has to survive this process exiting.
    Uses argv rather than a shell string to pass the path, so there's nothing to quote
    or inject even though the path is not attacker-influenced.
    """
    python_bin = os.environ.get("PYTHON") or shutil.which("python3") or "/usr/bin/python3"
    try:
        subprocess.Popen(
            [python_bin, "-c", _CLEANUP_SCRIPT, str(path), str(delay_seconds)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass


def sweep_stale_run_dirs(vendor_dir: Path, max_age_seconds: float = RUN_DIR_STALE_AGE_SECONDS):
    """Best-effort garbage collection for per-unlock snapshot directories that outlived
    their scheduled cleanup (e.g. the cleanup process was killed along with the user's
    session). Only removes directories older than max_age_seconds, which is chosen to
    be far longer than any realistic unlock needs its snapshot for."""
    try:
        candidates = list(vendor_dir.glob(".cryptomator-cli-run-*"))
    except OSError:
        return
    now = time.time()
    for d in candidates:
        try:
            if not d.is_dir() or (now - d.stat().st_mtime) < max_age_seconds:
                continue
        except OSError:
            continue
        make_tree_writable(d)
        shutil.rmtree(d, ignore_errors=True)


def _plugin_dir() -> Path:
    return Path(__file__).resolve().parent


def find_cryptomator_cli():
    """Locate cryptomator-cli for non-password operations (status, lock, reveal).

    Checks bundled vendor/, PATH, and ~/.local/bin, in that order.
    Do NOT use this for password-bearing operations — use open_trusted_cli_for_exec().
    """
    plugin_dir = _plugin_dir()

    # 1. Bundled inside the plugin directory (integrity-verified on setup)
    bundled = plugin_dir / "vendor" / "cryptomator-cli" / "bin" / "cryptomator-cli"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)

    # 2. In PATH (e.g. installed via a system package manager)
    which_cli = shutil.which("cryptomator-cli")
    if which_cli:
        return which_cli

    # 3. In ~/.local/bin (user-controlled; acceptable for non-password ops only)
    local_bin = Path.home() / ".local" / "bin" / "cryptomator-cli"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)

    return None


def open_trusted_cli_for_exec():
    """Verify the bundled cryptomator-cli's full identity and open its launcher via a
    verified file descriptor, for password-bearing operations ONLY.

    Hashing the shared vendor/cryptomator-cli/ tree and then executing the launcher out
    of that same, long-lived, user-writable path would still leave a window open: the
    launcher's JVM loads its jars, native libraries, and config by path over the whole
    time it runs, not just at the instant this function checks, so a same-uid attacker
    could swap a dependency in right after the check and have it loaded. To close that,
    this copies the verified bundle into a private, freshly-created snapshot directory,
    verifies *that copy's* bytes against the pinned manifest digest, locks it read-only,
    and only ever hands back a path inside the snapshot -- never the shared vendor/
    tree. Whatever this process (and the launcher it starts) ends up reading was written
    and checked by us, not by whatever happened to be sitting at a predictable path.

    ~/.local/bin and ambient PATH are never consulted here, since those locations are
    user-writable and a substituted binary there could capture vault passphrases sent
    over stdin.

    Returns (fd, display_path, snapshot_dir) on success, or (None, error_message, None).
    The caller must os.close(fd). snapshot_dir must NOT be deleted immediately -- the
    launched process keeps reading from it for as long as it runs -- schedule its
    removal with schedule_run_dir_cleanup() instead.
    """
    bundle_root = _plugin_dir() / "vendor" / "cryptomator-cli"
    vendor_dir = bundle_root.parent

    arch = detect_arch()
    if not arch or arch not in TRUSTED_BUNDLE_MANIFEST_SHA256:
        return None, f"Unsupported architecture for verified cryptomator-cli: {platform.machine()}", None

    if not bundle_root.is_dir():
        return None, (
            "cryptomator-cli bundle not found. Run setup-bundle first to install the "
            "verified bundle. Only the integrity-verified bundle may be used for "
            "password-bearing operations."
        ), None

    sweep_stale_run_dirs(vendor_dir)

    staging_root = Path(tempfile.mkdtemp(prefix=".cryptomator-cli-run-", dir=str(vendor_dir)))

    def _fail(message):
        make_tree_writable(staging_root)
        shutil.rmtree(staging_root, ignore_errors=True)
        return None, message, None

    try:
        _copy_tree_no_symlinks(bundle_root, staging_root)
    except (OSError, ValueError) as err:
        return _fail(f"Failed to snapshot cryptomator-cli bundle for verification: {err}")

    try:
        manifest_digest = bundle_manifest_sha256(staging_root)
    except OSError as err:
        return _fail(f"Failed to verify cryptomator-cli bundle snapshot: {err}")

    if manifest_digest != TRUSTED_BUNDLE_MANIFEST_SHA256[arch]:
        return _fail(
            "Security error: installed cryptomator-cli bundle does not match its pinned, "
            "verified digest. Refusing to send the vault passphrase to it. Re-run "
            "setup-bundle to reinstall a verified copy."
        )

    lock_down_bundle(staging_root)

    binary_path = staging_root / "bin" / "cryptomator-cli"
    fd = _open_verified_binary(binary_path, TRUSTED_CLI_BINARY_SHA256[arch])
    if fd is None:
        return _fail(
            "Security error: cryptomator-cli launcher failed identity verification. "
            "Refusing to send the vault passphrase to it."
        )
    return fd, str(binary_path), staging_root
