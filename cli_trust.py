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
from pathlib import Path

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

    Only the vendor/cryptomator-cli/ bundle installed by setup_bundle() is trusted;
    ~/.local/bin and ambient PATH are never consulted here, since those locations are
    user-writable and a substituted binary there could capture vault passphrases sent
    over stdin. Returns (fd, display_path) on success, or (None, error_message).
    """
    bundle_root = _plugin_dir() / "vendor" / "cryptomator-cli"
    binary_path = bundle_root / "bin" / "cryptomator-cli"

    arch = detect_arch()
    if not arch or arch not in TRUSTED_BUNDLE_MANIFEST_SHA256:
        return None, f"Unsupported architecture for verified cryptomator-cli: {platform.machine()}"

    if not bundle_root.is_dir():
        return None, (
            "cryptomator-cli bundle not found. Run setup-bundle first to install the "
            "verified bundle. Only the integrity-verified bundle may be used for "
            "password-bearing operations."
        )

    try:
        manifest_digest = bundle_manifest_sha256(bundle_root)
    except OSError as err:
        return None, f"Failed to read cryptomator-cli bundle for verification: {err}"

    if manifest_digest != TRUSTED_BUNDLE_MANIFEST_SHA256[arch]:
        return None, (
            "Security error: installed cryptomator-cli bundle does not match its pinned, "
            "verified digest. Refusing to send the vault passphrase to it. Re-run "
            "setup-bundle to reinstall a verified copy."
        )

    fd = _open_verified_binary(binary_path, TRUSTED_CLI_BINARY_SHA256[arch])
    if fd is None:
        return None, (
            "Security error: cryptomator-cli launcher failed identity verification. "
            "Refusing to send the vault passphrase to it."
        )
    return fd, str(binary_path)
