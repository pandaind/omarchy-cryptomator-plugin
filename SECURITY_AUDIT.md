# Security Audit Report: Omarchy Cryptomator Plugin

**Repository**: `omarchy-cryptomator-plugin` (`pandac.cryptomator`)  
**Date**: September 11, 2026  
**Auditor**: Antigravity Security Assessment Engine  
**Version Evaluated**: 1.0.0 (commit `3495a75` and hardened `HEAD`)  
**Status**: All Identified High/Medium Vulnerabilities Remediated & Verified  

---

## 1. Executive Summary

A comprehensive source code security assessment and architecture audit was conducted on the **Omarchy Cryptomator Plugin**. The plugin provides a native status bar widget and keyboard-accessible control panel for the [Cryptomator](https://cryptomator.org/) cryptographic storage utility under Omarchy (Arch Linux + Hyprland + Quickshell).

The core architecture exhibits solid defensive security principles:
* **No `shell=True` Subprocess Execution**: All child processes (`cryptomator-cli`, `fusermount3`, `xdg-open`) are executed using argument lists, eliminating classic shell injection vulnerabilities.
* **Stdin Passphrase Ingestion**: Passphrases for vault decryption and creation are transmitted through standard input streams (`sys.stdin`) rather than command-line arguments, preventing password leakage in process tables (`/proc/*/cmdline`, `ps`, audit logs).
* **Restricted IPC Surface**: The Quickshell IPC endpoint does not expose any unlock or password acceptance APIs.

However, the audit discovered several medium- and low-severity vulnerabilities concerning **file permissions**, **temporary log handling**, **unverified binary downloads**, and **regex-based process filtering**. All identified vulnerabilities have been remediated, verified, and hardened in the repository.

---

## 2. Scope & Target Components

| Component | Language / Format | Security-Relevant Role |
| :--- | :--- | :--- |
| `actions.py` | Python 3 | Headless CLI executor, mount/unmount manager, bundle downloader |
| `status.py` | Python 3 | State detection, `/proc/mounts` inspection, vault registry aggregation |
| `vault_create.py`| Python 3 / ctypes | Pure-Python Cryptomator Format 8 vault generator (scrypt, AES-SIV, RFC 5297) |
| `Panel.qml` | QML / Qt Quick | User interface, password inputs, keyboard trap, IPC handler |
| `Service.qml` | QML / Quickshell | Async process coordination, state management, signals |
| `Model.js` | JavaScript | Parsing and sanitization helper for status outputs |
| `manifest.json` | JSON | Omarchy plugin entrypoint declaration and schema |

---

## 3. Vulnerability Findings & Remediation Matrix

| ID | Title | Severity | CWE | Status |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Insecure Default Permissions on Vault Registry & Logs | **Medium** | CWE-276 | **Remediated** |
| **SEC-02** | Predictable Fallback Log File in Shared `/tmp` (Symlink Attack) | **Medium** | CWE-377, CWE-59 | **Remediated** |
| **SEC-03** | Unverified Binary Download & Zip Slip Risk in `setup_bundle` | **Medium** | CWE-494, CWE-22 | **Remediated** |
| **SEC-04** | Unescaped Regular Expression Pattern in Process Termination | **Low** | CWE-185, CWE-776| **Remediated** |
| **SEC-05** | Fragile `libcrypto` Hardcoding in Vault Generator | **Low** | CWE-1104 | **Remediated** |
| **SEC-06** | Quickshell IPC Local Information Exposure | **Informational** | CWE-200 | **Documented** |
| **SEC-07** | Passphrase Lifetime in Managed Runtime Memory | **Informational** | CWE-226 | **Documented** |

---

## 4. Detailed Audit Findings

### SEC-01: Insecure Default Permissions on Vault Registry & Logs
* **Severity**: Medium (CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N — 5.5)
* **CWE**: CWE-276 (Incorrect Default Permissions)
* **Description**:  
  The plugin's persistent vault registry (`~/.local/share/pandac.cryptomator/vaults.json`) and CLI execution logs (`~/.local/state/cryptomator/*.log`) were previously created using the standard system umask (typically `0644`). On multi-user Linux workstations, any local user could read `vaults.json`, exposing full directory paths to private vaults and sensitive folder structures.
* **Remediation**:  
  Implemented `_write_json_secure()` in [`actions.py`](file:///home/pandac/Projects/omarchy-cryptomator-plugin/actions.py) using `os.open()` with flags `O_CREAT | O_WRONLY | O_TRUNC` and explicit `0600` (read/write by owner only) permissions. Directories are created with `0700` modes.

---

### SEC-02: Predictable Fallback Log Path in Shared `/tmp` (Symlink / TOCTOU Risk)
* **Severity**: Medium (CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:U/C:L/I:H/A:N — 5.3)
* **CWE**: CWE-377 (Insecure Temporary File), CWE-59 (Improper Link Resolution Before File Access)
* **Description**:  
  In [`actions.py`](file:///home/pandac/Projects/omarchy-cryptomator-plugin/actions.py), if `~/.local/state/cryptomator` failed to be created, the process fell back to:
  ```python
  log_path = Path("/tmp") / f"cryptomator-{Path(vault_path).name}.log"
  with open(log_path, "w", encoding="utf-8") as log_f:
  ```
  Because `/tmp` is world-writable and the filename is predictable, an unprivileged local attacker could pre-create a symbolic link pointing to a critical user file (e.g., `~/.bashrc`), causing `actions.py` to truncate or overwrite it with CLI unlock output.
* **Remediation**:  
  Replaced predictable `/tmp` path creation with `tempfile.NamedTemporaryFile(prefix=..., suffix=".log")` which securely invokes `O_CREAT | O_EXCL` with mode `0600`.

---

### SEC-03: Unverified Binary Download & Zip Slip Risk in `setup_bundle`
* **Severity**: Medium (CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:H/A:H — 7.5)
* **CWE**: CWE-494 (Download of Code Without Integrity Check), CWE-22 (Improper Limitation of a Pathname)
* **Description**:  
  `setup_bundle()` automated the download of the official upstream `cryptomator-cli` release zip archive from GitHub. The archive was downloaded over standard HTTPS without verifying cryptographic SHA-256 hashes. Furthermore, extracting the zip via `zf.extractall(vendor_dir)` without verifying member destination paths posed a potential Zip Slip path-traversal risk if an untrusted archive were unpacked.
* **Remediation**:  
  * Added pinned cryptographic SHA-256 checksum verification in [`actions.py`](file:///home/pandac/Projects/omarchy-cryptomator-plugin/actions.py) for upstream releases (`x64` and `aarch64`). The download aborts and purges the file if the computed hash does not match.
  * Added canonical path validation for every zip member before extraction to ensure no file can extract outside `vendor/`.

---

### SEC-04: Unescaped Regular Expression Pattern in Process Termination
* **Severity**: Low (CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:L — 3.3)
* **CWE**: CWE-185 (Incorrect Regular Expression), CWE-776
* **Description**:  
  In [`actions.py`](file:///home/pandac/Projects/omarchy-cryptomator-plugin/actions.py), `cleanup_cli_for_mount(mount_point)` executed:
  ```python
  subprocess.run(["pgrep", "-f", f"--mountPoint={mount_point}"])
  ```
  Because `pgrep -f` interprets the pattern as a regular expression, special characters in `mount_point` (such as `+`, `(`, `[`) could cause regex parsing errors or match unintended processes.
* **Remediation**:  
  Applied `re.escape(str(mount_point))` to sanitize the argument before passing it to `pgrep`.

---

### SEC-05: Fragile `libcrypto` Hardcoding in Vault Generator
* **Severity**: Low (CVSS:3.1/AV:L/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:L — 2.9)
* **CWE**: CWE-1104 (Use of Unmaintained Third Party Component)
* **Description**:  
  [`vault_create.py`](file:///home/pandac/Projects/omarchy-cryptomator-plugin/vault_create.py) hardcoded `ctypes.CDLL("libcrypto.so.3")`. On systems where OpenSSL 3.x is packaged with a different soname or unversioned `libcrypto.so`, initialization would fail with an unhandled exception.
* **Remediation**:  
  Enhanced library loading using `ctypes.util.find_library("crypto")` with automated fallbacks to `libcrypto.so.3` and `libcrypto.so`. Enforced `0700` and `0600` permissions on created vault directories and metadata files (`masterkey.cryptomator`, `vault.cryptomator`).

---

### SEC-06: Quickshell IPC Local Information Exposure
* **Severity**: Informational
* **CWE**: CWE-200 (Exposure of Sensitive Information)
* **Description**:  
  The plugin exports status queries via Quickshell IPC (`dumpVaults()`, `dumpState()`, `status()`). Any process executing under the same Linux UID can interact with the user's Quickshell socket and enumerate configured vault paths.
* **Assessment & Guidance**:  
  This is standard behavior for user desktop session IPC (comparable to D-Bus session bus services). Crucially, **passwords are never transmitted, accepted, or exposed across IPC**. System administrators should verify that `/run/user/$UID` retains `0700` ownership.

---

### SEC-07: Passphrase Lifetime in Managed Runtime Memory
* **Severity**: Informational
* **CWE**: CWE-226 (Sensitive Information in Resource Not Removed Before Reuse)
* **Description**:  
  Passphrases entered in [`Panel.qml`](file:///home/pandac/Projects/omarchy-cryptomator-plugin/Panel.qml) and passed into Python are stored in standard memory strings (`QString` / Python `str`). In garbage-collected managed runtimes, strings cannot be securely overwritten (`memset_s` / zeroed) immediately upon use.
* **Assessment & Guidance**:  
  The plugin mitigates this by zeroing input properties (`text = ""`, `createVaultPw = ""`) immediately upon submission or cancelation. On Linux systems, enabling swap encryption (`dm-crypt`) or configuring `vm.swappiness` prevents memory pages containing transient passphrases from being written to persistent storage.

---

## 5. Cryptographic Implementation Review (`vault_create.py`)

The pure-Python vault creation module was audited against Cryptomator Vault Format 8 specifications:
1. **Key Derivation Function**: Uses standard `hashlib.scrypt` with RFC 7914 parameters ($N=16384, r=8, p=1$), matching Cryptomator desktop defaults.
2. **Key Encryption Key (KEK)**: 256-bit KEK derived with 32 bytes of cryptographically secure pseudorandom salt (`os.urandom(32)`).
3. **Master Key Wrapping**: 256-bit AES-SIV wrapping adhering to RFC 5297.
4. **JWT Header & Signature**: Uses HMAC-SHA256 over canonical header/payload representations using the 256-bit HMAC master key.

---

## 6. Verification and Hardening Checklist

- [x] All child processes execute via argv arrays; no shell interpretation.
- [x] Passwords ingested via STDIN and never exposed in process argv.
- [x] Pinned SHA-256 integrity verification on external CLI bundles.
- [x] Path traversal (Zip Slip) protection during archive extraction.
- [x] Vault registry (`vaults.json`) protected with `0600` permissions.
- [x] Vault creation outputs (`masterkey.cryptomator`, `vault.cryptomator`) protected with `0600` permissions.
- [x] Temporary log files use randomized names and `0600` modes.
- [x] Process matching patterns sanitized against regex injection.
- [x] Working tree clean and passing syntax checks.
