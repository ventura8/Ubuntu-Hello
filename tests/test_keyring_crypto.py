"""Full coverage tests for keyring_crypto.py (AES-256-GCM / UH1)."""
import base64
import os
from pathlib import Path

import pytest

import keyring_crypto as kc


@pytest.fixture
def key_env(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(kc, "MASTER_KEY_PATH", tmp_path / "keyring-master.key")
    return tmp_path


class TestEnsureMasterKey:
    def test_create_and_reload(self, key_env):
        key = kc.ensure_master_key()
        assert len(key) == 32
        assert kc.MASTER_KEY_PATH.is_file()
        assert kc.MASTER_KEY_PATH.stat().st_mode & 0o777 == 0o600
        assert (key_env.stat().st_mode & 0o777) == 0o700
        assert kc.ensure_master_key() == key

    def test_invalid_length_raises(self, key_env):
        kc.MASTER_KEY_PATH.write_bytes(b"short")
        with pytest.raises(ValueError, match="invalid length"):
            kc.ensure_master_key()


class TestEncryptDecrypt:
    def test_roundtrip(self, key_env):
        blob = kc.encrypt_password("pässwörd")
        assert blob.startswith(kc.BLOB_PREFIX)
        assert kc.decrypt_password(blob) == "pässwörd"
        assert kc.decrypt_password(blob + "\n") == "pässwörd"

    def test_empty_password(self, key_env):
        blob = kc.encrypt_password("")
        assert kc.decrypt_password(blob) == ""

    def test_reject_legacy_xor(self, key_env):
        with pytest.raises(ValueError, match="UH1"):
            kc.decrypt_password("aabbccddee")

    def test_reject_no_prefix(self, key_env):
        with pytest.raises(ValueError, match="UH1"):
            kc.decrypt_password("not-a-blob")

    def test_malformed_base64(self, key_env):
        with pytest.raises(ValueError, match="base64"):
            kc.decrypt_password(kc.BLOB_PREFIX + "!!!not-b64!!!")

    def test_too_short_blob(self, key_env):
        short = kc.BLOB_PREFIX + base64.b64encode(b"short").decode("ascii")
        with pytest.raises(ValueError, match="too short"):
            kc.decrypt_password(short)

    def test_auth_failure_returns_empty(self, key_env):
        blob = kc.encrypt_password("secret")
        # Flip a byte in the payload after the prefix
        raw = bytearray(base64.b64decode(blob[len(kc.BLOB_PREFIX) :]))
        raw[-1] ^= 0xFF
        corrupted = kc.BLOB_PREFIX + base64.b64encode(bytes(raw)).decode("ascii")
        assert kc.decrypt_password(corrupted) == ""

    def test_wrong_master_key_returns_empty(self, key_env):
        blob = kc.encrypt_password("secret")
        kc.MASTER_KEY_PATH.write_bytes(os.urandom(32))
        assert kc.decrypt_password(blob) == ""
