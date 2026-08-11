"""AES-256-GCM encryption for software keyring fallback.

Master key lives at ``/etc/ubuntu-hello/keyring-master.key`` (32 random
bytes, mode 0600; parent directory 0700). Ciphertext format:

    UH1: + base64(nonce_12 || ciphertext || tag_16)
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CONFIG_DIR = Path("/etc/ubuntu-hello")
MASTER_KEY_PATH = CONFIG_DIR / "keyring-master.key"
BLOB_PREFIX = "UH1:"
_NONCE_LEN = 12
_TAG_LEN = 16
_KEY_LEN = 32


def ensure_master_key() -> bytes:
    """Return the 32-byte master key, creating it if missing."""
    if MASTER_KEY_PATH.is_file():
        key = MASTER_KEY_PATH.read_bytes()
        if len(key) != _KEY_LEN:
            raise ValueError(
                f"master key at {MASTER_KEY_PATH} has invalid length {len(key)}"
            )
        return key

    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)

    key = os.urandom(_KEY_LEN)
    # Write atomically via temp file in the same directory.
    tmp_path = MASTER_KEY_PATH.with_suffix(".key.tmp")
    fd = os.open(
        tmp_path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL,
        0o600,
    )
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, MASTER_KEY_PATH)
    os.chmod(MASTER_KEY_PATH, 0o600)
    return key


def encrypt_password(plaintext: str) -> str:
    """Encrypt *plaintext* and return a ``UH1:`` blob string."""
    key = ensure_master_key()
    nonce = os.urandom(_NONCE_LEN)
    # AESGCM.encrypt returns ciphertext || tag (16-byte tag appended).
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    blob = nonce + ct_and_tag
    return BLOB_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_password(blob: str) -> str:
    """Decrypt a ``UH1:`` blob. Rejects legacy XOR ciphertext.

    Raises ``ValueError`` for non-``UH1:`` input or malformed data.
    Returns ``""`` on authentication/decrypt failure.
    """
    blob = blob.strip()
    if not blob.startswith(BLOB_PREFIX):
        raise ValueError("legacy or unsupported keyring blob (expected UH1: prefix)")

    try:
        raw = base64.b64decode(blob[len(BLOB_PREFIX) :], validate=True)
    except Exception as exc:
        raise ValueError("malformed UH1 blob (base64)") from exc

    if len(raw) < _NONCE_LEN + _TAG_LEN:
        raise ValueError("malformed UH1 blob (too short)")

    nonce = raw[:_NONCE_LEN]
    ct_and_tag = raw[_NONCE_LEN:]
    key = ensure_master_key()
    try:
        plaintext = AESGCM(key).decrypt(nonce, ct_and_tag, None)
    except Exception:
        return ""
    return plaintext.decode("utf-8")
