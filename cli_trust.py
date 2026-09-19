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

# Safety ceiling for the background cleanup process (see schedule_run_dir_cleanup): it
# normally removes a per-unlock snapshot the moment the process using it exits, however
# long that takes, but if that process's pid gets reused by something unrelated before
# we ever see it exit, this bounds how long the snapshot can be kept alive by mistake.
# A stale-directory sweep with a similar threshold acts as a second safety net for
# snapshots whose cleanup process didn't survive at all (e.g. killed with the session).
RUN_DIR_MAX_WAIT_SECONDS = 24 * 60 * 60
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


def _copy_and_hash_tree(src: Path, dst: Path) -> str:
    """Copy every regular file under src into dst, hashing each file's content as it is
    written so the returned manifest digest reflects exactly the bytes that landed in
    dst -- without a second full read of the tree just to verify it (this bundle is
    tens of MB, and unlocking a vault does this on every attempt, so halving the I/O
    here matters for how long the caller's mount-wait timeout budget has left).

    Refuses to touch any symlink found anywhere in the tree, since a symlink could
    otherwise alias content outside the verified bundle and defeat the point of hashing
    the copy afterward.
    """
    entries = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_symlink():
            raise ValueError(f"refusing to copy symlink in trusted bundle: {rel}")
        dest = dst / rel
        if p.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            with open(p, "rb") as src_f, open(dest, "wb") as dst_f:
                while chunk := src_f.read(1024 * 1024):
                    hasher.update(chunk)
                    dst_f.write(chunk)
            shutil.copymode(p, dest)
            entries.append((rel.as_posix(), hasher.hexdigest()))

    manifest = hashlib.sha256()
    for rel_str, digest in sorted(entries):
        manifest.update(rel_str.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    return manifest.hexdigest()


_CLEANUP_SCRIPT = (
    "import os, shutil, stat, sys, time\n"
    "root, pid, max_wait = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])\n"
    "deadline = time.monotonic() + max_wait\n"
    "while time.monotonic() < deadline:\n"
    "    try:\n"
    "        os.kill(pid, 0)\n"
    "    except OSError:\n"
    "        break\n"
    "    time.sleep(1)\n"
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


def cleanup_run_dir_now(path: Path):
    """Synchronously remove a per-unlock snapshot that was never handed to a running
    process -- e.g. the vault turned out to already be mounted, or launching
    cryptomator-cli failed before it started. Nothing else can be using it, so there's
    no reason to defer this to the background cleanup process."""
    make_tree_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def schedule_run_dir_cleanup(path: Path, wait_for_pid: int, max_wait_seconds: float = RUN_DIR_MAX_WAIT_SECONDS):
    """Spawn a detached process that removes a per-unlock bundle snapshot once the
    process using it (wait_for_pid, e.g. the cryptomator-cli launcher) exits.

    The snapshot must outlive the short-lived Python process that created it (that
    process returns almost immediately after starting cryptomator-cli, while the
    launched process keeps running for as long as the vault stays mounted), so cleanup
    can't happen in a normal try/finally here -- it has to survive this process exiting.
    Waiting on the actual pid rather than a guessed delay means cleanup happens exactly
    when it's safe to, whether that's seconds or hours later, with max_wait_seconds only
    as a ceiling against wait_for_pid being reused by an unrelated process before we
    observe it exit. Resolves the interpreter via sys.executable rather than searching
    PATH, and passes the path via argv rather than a shell string, so nothing here is
    resolved or built from anything a same-uid attacker could redirect.
    """
    python_bin = sys.executable or "/usr/bin/python3"
    try:
        subprocess.Popen(
            [python_bin, "-c", _CLEANUP_SCRIPT, str(path), str(wait_for_pid), str(max_wait_seconds)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass


def run_dir_root() -> Path:
    """Base directory for per-unlock bundle snapshots (see open_trusted_cli_for_exec).

    Deliberately outside the plugin's own directory tree. Omarchy's shell watches
    locally-linked plugin directories and reloads the whole plugin the moment any file
    under them changes -- writing a snapshot's files there triggered a reload storm that
    tore down the in-flight unlock's Process (and the panel with it) mid-copy, which is
    what made the first unlock attempt after this snapshot scheme was added appear to
    fail every time. XDG_RUNTIME_DIR is a per-session tmpfs outside any watched config
    or plugin tree, and is already private to this user.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir()) / f"omarchy-cryptomator-plugin-{os.getuid()}"
    root = base / "omarchy-cryptomator-plugin"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def install_staging_root() -> Path:
    """Base directory for downloading and staging a new cryptomator-cli bundle before
    it's published into vendor/cryptomator-cli/ (see setup_bundle in bundle_installer.py).

    Deliberately outside the plugin's own directory tree, for the same reason as
    run_dir_root(): Omarchy's shell watches locally-linked plugin directories and
    reloads the whole plugin -- tearing down the in-flight install Process (and the
    panel) along with it -- the instant any file under them changes. Downloading and
    extracting a bundle's hundreds of files directly into vendor/ triggers exactly that
    storm. Unlike run_dir_root() (XDG_RUNTIME_DIR, a tmpfs that's commonly a different
    filesystem from the plugin's own), this lives under the user's cache directory,
    which is normally on the same filesystem as the plugin -- letting the final publish
    step move the verified result into place with a single rename instead of a
    file-by-file copy into the watched tree.
    """
    cache_dir = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_dir) if cache_dir else Path.home() / ".cache"
    root = base / "omarchy-cryptomator-plugin" / "install-staging"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def sweep_stale_run_dirs(base_dir: Path, max_age_seconds: float = RUN_DIR_STALE_AGE_SECONDS):
    """Best-effort garbage collection for per-unlock snapshot directories that outlived
    their scheduled cleanup (e.g. the cleanup process was killed along with the user's
    session). Only removes directories older than max_age_seconds, which is chosen to
    be far longer than any realistic unlock needs its snapshot for."""
    try:
        candidates = list(base_dir.glob("cryptomator-cli-run-*"))
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

    arch = detect_arch()
    if not arch or arch not in TRUSTED_BUNDLE_MANIFEST_SHA256:
        return None, f"Unsupported architecture for verified cryptomator-cli: {platform.machine()}", None

    if not bundle_root.is_dir():
        return None, (
            "cryptomator-cli bundle not found. Run setup-bundle first to install the "
            "verified bundle. Only the integrity-verified bundle may be used for "
            "password-bearing operations."
        ), None

    run_root = run_dir_root()
    sweep_stale_run_dirs(run_root)

    staging_root = Path(tempfile.mkdtemp(prefix="cryptomator-cli-run-", dir=str(run_root)))

    def _fail(message):
        make_tree_writable(staging_root)
        shutil.rmtree(staging_root, ignore_errors=True)
        return None, message, None

    try:
        manifest_digest = _copy_and_hash_tree(bundle_root, staging_root)
    except (OSError, ValueError) as err:
        return _fail(f"Failed to snapshot cryptomator-cli bundle for verification: {err}")

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
