#!/usr/bin/env python3
"""Downloads and installs the verified cryptomator-cli bundle into vendor/."""

import sys
sys.dont_write_bytecode = True

import os
import platform
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import cli_trust

VERSION = "0.6.2"

# Bounds for the installer download: a generous byte ceiling (the real archives are
# ~45 MB today) and timeouts so a stalled or malicious endpoint can't hang the
# installer forever or fill the disk before the checksum check ever runs.
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
DOWNLOAD_SOCKET_TIMEOUT = 15  # seconds of inactivity allowed on the connection/read
DOWNLOAD_TOTAL_TIMEOUT = 300  # seconds, wall-clock ceiling for the whole download


def _download_bounded(url: str, dest_dir: Path, max_bytes: int, socket_timeout: float, total_timeout: float) -> Path:
    """Download url into a securely-created, private temp file inside dest_dir.

    The file is created exclusively (tempfile.mkstemp) so it can't be pre-staged as a
    symlink by another process, and is never written at a predictable name. Enforces
    a declared Content-Length ceiling, a hard byte ceiling as the response streams in
    (in case Content-Length is absent or lies), and a wall-clock ceiling on top of the
    per-read socket timeout. Raises on any violation; the caller must still verify the
    checksum before trusting the result. Returns the temp file path on success.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=".cryptomator-cli-download-", suffix=".part", dir=str(dest_dir))
    tmp_path = Path(tmp_name)
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "omarchy-cryptomator-plugin"})
        with urllib.request.urlopen(req, timeout=socket_timeout) as resp:
            content_length = resp.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = None
                if declared is not None and declared > max_bytes:
                    raise ValueError(f"Declared download size {declared} exceeds limit {max_bytes}")

            written = 0
            with os.fdopen(fd, "wb") as out_f:
                fd = -1  # ownership transferred to out_f; don't double-close below
                while True:
                    if time.monotonic() - start > total_timeout:
                        raise TimeoutError(f"Download exceeded {total_timeout}s wall-clock limit")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"Download exceeded {max_bytes}-byte limit")
                    out_f.write(chunk)
        return tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass


def setup_bundle():
    """Download and extract the official cryptomator-cli into vendor/, verifying its
    identity both as a whole archive and, afterward, as an installed bundle tree."""
    arch = cli_trust.detect_arch()
    if not arch:
        print(f"Unsupported architecture: {platform.machine()}", file=sys.stderr)
        return False
    if arch not in cli_trust.OFFICIAL_SHA256 or arch not in cli_trust.TRUSTED_BUNDLE_MANIFEST_SHA256:
        print(f"No pinned trust data available for architecture: {arch}", file=sys.stderr)
        return False

    url = f"https://github.com/cryptomator/cli/releases/download/{VERSION}/cryptomator-cli-{VERSION}-linux-{arch}.zip"

    plugin_dir = Path(__file__).resolve().parent
    vendor_dir = plugin_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    target_dir = vendor_dir / "cryptomator-cli"

    print(f"Downloading cryptomator-cli {VERSION} from {url}...")
    try:
        zip_path = _download_bounded(
            url, vendor_dir, MAX_DOWNLOAD_BYTES, DOWNLOAD_SOCKET_TIMEOUT, DOWNLOAD_TOTAL_TIMEOUT
        )
    except Exception as e:
        print(f"Download failed: {e}", file=sys.stderr)
        return False

    print("Verifying archive checksum...")
    try:
        computed_sha = cli_trust.file_sha256(zip_path)
        expected_sha = cli_trust.OFFICIAL_SHA256[arch]
        if computed_sha != expected_sha:
            print("Security error: Checksum mismatch for downloaded archive!", file=sys.stderr)
            print(f"Expected: {expected_sha}", file=sys.stderr)
            print(f"Got:      {computed_sha}", file=sys.stderr)
            zip_path.unlink(missing_ok=True)
            return False
    except OSError as e:
        print(f"Checksum verification failed: {e}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return False

    print("Extracting bundle into a private staging area...")
    staging_dir = Path(tempfile.mkdtemp(prefix=".cryptomator-cli-staging-", dir=str(vendor_dir)))
    try:
        resolved_staging = staging_dir.resolve()
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Zip Slip prevention: check destination for all members
            for member in zf.infolist():
                dest = (staging_dir / member.filename).resolve()
                if not (dest == resolved_staging or str(dest).startswith(str(resolved_staging) + "/")):
                    raise ValueError(f"Potential Zip Slip path traversal detected: {member.filename}")
            zf.extractall(staging_dir)
        zip_path.unlink(missing_ok=True)

        staged_root = staging_dir / "cryptomator-cli"

        bin_file = staged_root / "bin" / "cryptomator-cli"
        if bin_file.exists():
            bin_file.chmod(bin_file.stat().st_mode | 0o755)
        launcher = staged_root / "lib" / "libapplauncher.so"
        if launcher.exists():
            launcher.chmod(launcher.stat().st_mode | 0o755)

        # Cap memory heap to 128M in cryptomator-cli.cfg to prevent excessive RAM usage
        cfg_file = staged_root / "lib" / "app" / "cryptomator-cli.cfg"
        if cfg_file.exists():
            try:
                cfg_content = cfg_file.read_text(encoding="utf-8")
                cfg_content = re.sub(r'-Xmx\d+m', '-Xmx128m', cfg_content)
                cfg_file.write_text(cfg_content, encoding="utf-8")
            except Exception:
                pass

        print("Verifying staged bundle identity...")
        manifest_digest = cli_trust.bundle_manifest_sha256(staged_root)
        if manifest_digest != cli_trust.TRUSTED_BUNDLE_MANIFEST_SHA256[arch]:
            print(
                "Security error: extracted bundle does not match its pinned, verified "
                "digest. Discarding untrusted staging area.",
                file=sys.stderr,
            )
            return False

        # Publish atomically: swap the verified staged tree into place with a single
        # rename, so a reader only ever sees either no bundle, the previous verified
        # bundle, or the new verified bundle -- never a partially-extracted one.
        # (Renaming a directory needs write permission on the directory itself, not
        # just its parent, so this must happen before the read-only lockdown below.)
        if target_dir.exists():
            cli_trust.make_tree_writable(target_dir)
            shutil.rmtree(target_dir)
        os.replace(staged_root, target_dir)

        # Lock the published bundle read-only so a later same-user write requires an
        # explicit permission change first, rather than a plain overwrite.
        cli_trust.lock_down_bundle(target_dir)

        print(f"Successfully installed verified cryptomator-cli to {target_dir}")
        return True
    except Exception as e:
        print(f"Extraction failed: {e}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return False
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
