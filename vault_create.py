#!/usr/bin/env python3
"""
Cryptomator v8 vault creation — pure Python, no third-party dependencies.

Uses libcrypto.so.3 (OpenSSL 3) via ctypes for AES-ECB block operations,
which are composed into AES-CMAC and AES-SIV as per RFC 5297.

Cryptomator vault format v8:
  - masterkey.cryptomator  : JSON file with wrapped 256-bit master key
  - vault.cryptomator      : JSON vault config
  - d/                     : encrypted data directory
"""

import sys
sys.dont_write_bytecode = True

import ctypes
import ctypes.util
import hashlib
import json
import os
import struct
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Low-level AES primitives via libcrypto
# ---------------------------------------------------------------------------

_lib_candidate = ctypes.util.find_library("crypto") or "libcrypto.so.3"
try:
    _libcrypto = ctypes.CDLL(_lib_candidate)
except OSError:
    try:
        _libcrypto = ctypes.CDLL("libcrypto.so.3")
    except OSError:
        _libcrypto = ctypes.CDLL("libcrypto.so")

class _AES_KEY(ctypes.Structure):
    _fields_ = [("rd_key", ctypes.c_uint32 * 60), ("rounds", ctypes.c_int)]

_libcrypto.AES_set_encrypt_key.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(_AES_KEY)]
_libcrypto.AES_set_encrypt_key.restype = ctypes.c_int
_libcrypto.AES_encrypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_AES_KEY)]
_libcrypto.AES_encrypt.restype = None


def _aes_ecb_encrypt_block(key_bytes: bytes, block: bytes) -> bytes:
    """Encrypt a single 16-byte block with AES-ECB."""
    assert len(key_bytes) in (16, 24, 32)
    assert len(block) == 16
    aes_key = _AES_KEY()
    _libcrypto.AES_set_encrypt_key(key_bytes, len(key_bytes) * 8, ctypes.byref(aes_key))
    out = ctypes.create_string_buffer(16)
    _libcrypto.AES_encrypt(block, out, ctypes.byref(aes_key))
    return bytes(out)


# ---------------------------------------------------------------------------
# AES-CMAC (RFC 4493) — needed for S2V component of AES-SIV
# ---------------------------------------------------------------------------

def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _cmac_generate_subkeys(key: bytes):
    """Generate CMAC subkeys K1, K2 from AES key."""
    Rb = b'\x00' * 15 + b'\x87'
    L = _aes_ecb_encrypt_block(key, b'\x00' * 16)
    if L[0] & 0x80:
        K1 = _xor(_lshift1(L), Rb)
    else:
        K1 = _lshift1(L)
    if K1[0] & 0x80:
        K2 = _xor(_lshift1(K1), Rb)
    else:
        K2 = _lshift1(K1)
    return K1, K2


def _lshift1(b: bytes) -> bytes:
    """Left-shift a byte string by 1 bit."""
    result = bytearray(len(b))
    carry = 0
    for i in reversed(range(len(b))):
        result[i] = ((b[i] << 1) | carry) & 0xFF
        carry = (b[i] >> 7) & 1
    return bytes(result)


def aes_cmac(key: bytes, msg: bytes) -> bytes:
    """Compute AES-CMAC of msg under key (RFC 4493)."""
    K1, K2 = _cmac_generate_subkeys(key)
    n = max(1, (len(msg) + 15) // 16)
    flag_complete = len(msg) % 16 == 0 and len(msg) > 0

    blocks = [msg[i * 16:(i + 1) * 16] for i in range(n)]
    last = blocks[-1]
    if flag_complete:
        last = _xor(last, K1)
    else:
        padded = last + b'\x80' + b'\x00' * (15 - len(last))
        last = _xor(padded, K2)
    blocks[-1] = last

    X = b'\x00' * 16
    for block in blocks:
        X = _aes_ecb_encrypt_block(key, _xor(X, block))
    return X


# ---------------------------------------------------------------------------
# AES-SIV (RFC 5297) — used by Cryptomator to wrap the master key
# ---------------------------------------------------------------------------

def _s2v(key: bytes, *args: bytes) -> bytes:
    """S2V function from RFC 5297 §2.4."""
    D = aes_cmac(key, b'\x00' * 16)
    for s in args[:-1]:
        D = _xor(_lshift1(D), aes_cmac(key, s))
    last = args[-1]
    if len(last) >= 16:
        T = bytearray(last)
        for i, b in enumerate(D):
            T[len(T) - 16 + i] ^= b
        return aes_cmac(key, bytes(T))
    else:
        padded = last + b'\x80' + b'\x00' * (15 - len(last))
        return aes_cmac(key, _xor(_lshift1(D), padded))


def _aes_ctr_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-CTR encryption (IV is the initial counter block)."""
    result = bytearray()
    counter = int.from_bytes(iv, 'big')
    for i in range(0, len(plaintext), 16):
        block = plaintext[i:i + 16]
        keystream = _aes_ecb_encrypt_block(key, counter.to_bytes(16, 'big'))
        result += _xor(keystream[:len(block)], block)
        counter = (counter + 1) & ((1 << 128) - 1)
    return bytes(result)


def aes_siv_encrypt(key: bytes, plaintext: bytes, *ad: bytes) -> bytes:
    """AES-SIV encryption. key must be 2x the AES key size (512 bits for AES-256-SIV).
    Returns SIV (16 bytes) + ciphertext."""
    assert len(key) == 64, "AES-256-SIV requires a 512-bit key"
    k1, k2 = key[:32], key[32:]
    siv = _s2v(k1, *ad, plaintext)
    # Zero bits 31 and 63 of SIV before using as CTR IV
    q = bytearray(siv)
    q[8] &= 0x7F
    q[12] &= 0x7F
    ciphertext = _aes_ctr_encrypt(k2, bytes(q), plaintext)
    return siv + ciphertext


# ---------------------------------------------------------------------------
# Cryptomator v8 vault format
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    """URL-safe Base64 without padding."""
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _derive_kek(password: str, salt: bytes, cost: int = 16384) -> bytes:
    """Derive Key Encryption Key from password using scrypt."""
    return hashlib.scrypt(
        password.encode('utf-8'),
        salt=salt,
        n=cost,
        r=8,
        p=1,
        dklen=32,  # 256-bit KEK
    )


def _write_file_secure(file_path: Path, content: str):
    """Write sensitive vault config with restrictive 0600 permissions."""
    fd = os.open(file_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(content)


def create_vault(vault_path: str, password: str) -> dict:
    """
    Create a new Cryptomator v8 vault at vault_path with the given password.

    Returns {'ok': True} on success or {'ok': False, 'error': str} on failure.
    """
    import base64

    path = Path(vault_path).expanduser().resolve()
    if path.exists() and any(path.iterdir()):
        return {'ok': False, 'error': f"Directory is not empty: {path}"}

    path.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        # 1. Generate 256-bit master key (encryption key + MAC key = 512 bits total)
        master_enc_key = os.urandom(32)  # 256-bit AES key for file content
        master_mac_key = os.urandom(32)  # 256-bit HMAC key

        # 2. Derive KEK from password
        scrypt_salt = os.urandom(32)
        kek = _derive_kek(password, scrypt_salt)

        # 3. Wrap master keys using AES-SIV
        # Cryptomator wraps enc and mac keys separately
        # KEK for SIV must be 512-bit: use kek for both halves (doubling)
        siv_key = kek + kek  # 64 bytes = 512-bit SIV key
        wrapped_enc = aes_siv_encrypt(siv_key, master_enc_key)
        wrapped_mac = aes_siv_encrypt(siv_key, master_mac_key)

        # 4. Build masterkey.cryptomator JSON
        masterkey = {
            "version": 999,  # Cryptomator v8 format marker (999 = use vault.cryptomator)
            "scryptSalt": base64.b64encode(scrypt_salt).decode(),
            "scryptCostParam": 16384,
            "scryptBlockSize": 8,
            "primaryMasterKey": base64.b64encode(wrapped_enc).decode(),
            "hmacMasterKey": base64.b64encode(wrapped_mac).decode(),
            "versionMac": "placeholder",  # Filled below
        }

        # 5. Compute versionMac: HMAC-SHA256(master_mac_key, version=8 as 32-bit BE)
        import hmac as _hmac
        version_mac = _hmac.new(
            master_mac_key,
            struct.pack('>I', 8),
            hashlib.sha256,
        ).digest()
        masterkey["versionMac"] = base64.b64encode(version_mac).decode()

        # 6. vault.cryptomator (vault config JWT-like, but Cryptomator uses a simple JSON)
        vault_id = str(uuid.uuid4())
        vault_config = {
            "keyId": "masterkey.cryptomator",
            "format": 8,
            "cipherCombo": "SIV_GCM",
            "shorteningThreshold": 220,
        }

        # Sign vault config as JWT (Cryptomator uses HS256 JWT)
        # Header
        import hmac as _hmac2
        header = {"alg": "HS256", "kid": "masterkey.cryptomator"}
        payload = {
            "jti": vault_id,
            "format": 8,
            "ciphercombo": "SIV_GCM",
            "shorteningThreshold": 220,
        }
        header_b64 = _b64url(json.dumps(header, separators=(',', ':')).encode())
        payload_b64 = _b64url(json.dumps(payload, separators=(',', ':')).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig = _hmac2.new(master_mac_key, signing_input, hashlib.sha256).digest()
        jwt_token = f"{header_b64}.{payload_b64}.{_b64url(sig)}"

        # 7. Create directory structure with restrictive permissions
        d_dir = path / "d"
        d_dir.mkdir(mode=0o700, exist_ok=True)

        # 8. Write masterkey.cryptomator with 0600 permissions
        _write_file_secure(path / "masterkey.cryptomator", json.dumps(masterkey, indent=2))

        # 9. Write vault.cryptomator (the JWT token) with 0600 permissions
        _write_file_secure(path / "vault.cryptomator", jwt_token)

        return {'ok': True}

    except Exception as e:
        import traceback
        return {'ok': False, 'error': str(e) + '\n' + traceback.format_exc()}
