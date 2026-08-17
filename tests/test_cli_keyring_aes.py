"""Tests for CLI keyring software AES path, migration, and related branches."""
import builtins
import io
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import keyring_crypto
from cli import keyring as keyring_mod

_REAL_EXISTS = os.path.exists


@pytest.fixture
def key_env(tmp_path, monkeypatch):
    monkeypatch.setattr(keyring_crypto, "CONFIG_DIR", tmp_path / "etc")
    monkeypatch.setattr(keyring_crypto, "MASTER_KEY_PATH", tmp_path / "etc" / "keyring-master.key")
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)

    keys = tmp_path / "keyring-keys"
    tpm = tmp_path / "tpm-keys"
    pending = tmp_path / "pending"
    keys.mkdir()
    tpm.mkdir()
    pending.mkdir()
    monkeypatch.setattr(keyring_mod, "KEYRING_KEYS_DIR", str(keys))
    monkeypatch.setattr(keyring_mod, "TPM_KEYS_DIR", str(tpm))
    monkeypatch.setattr(keyring_mod, "PENDING_DIR", str(pending))
    return {"keys": keys, "tpm": tpm, "pending": pending, "tmp": tmp_path}


def test_usage_exits():
    with pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("user", [])
    assert exc.value.code == 1


def test_invalid_action_exits():
    with pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("user", ["nope"])
    assert exc.value.code == 1


def test_software_enable_writes_uh1(key_env):
    stdin = io.StringIO("mypassword\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.shutil.which", return_value=None), \
         patch("cli.keyring.os.path.exists", side_effect=lambda p: False), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.os.chmod"):
        keyring_mod.run_keyring("alice", ["enable"])

    blob = (key_env["keys"] / "alice").read_text().strip()
    assert blob.startswith("UH1:")
    assert keyring_crypto.decrypt_password(blob) == "mypassword"


def test_software_enable_migrates_legacy_xor(key_env):
    """Existing XOR hex blob must be overwritten with UH1 on enable."""
    key_file = key_env["keys"] / "alice"
    key_file.write_text("aabbccddeeff001122334455\n")

    def exists(p):
        p = str(p)
        if p in ("/dev/tpmrm0", "/dev/tpm0"):
            return False
        return _REAL_EXISTS(p)

    stdin = io.StringIO("new-secret\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.shutil.which", return_value=None), \
         patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.os.chmod"):
        keyring_mod.run_keyring("alice", ["enable"])

    blob = key_file.read_text().strip()
    assert blob.startswith("UH1:")
    assert "aabbcc" not in blob
    assert keyring_crypto.decrypt_password(blob) == "new-secret"


def test_software_enable_encrypt_failure(key_env):
    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch.object(keyring_mod.shutil, "which", return_value=None), \
         patch.object(keyring_mod.os.path, "exists", return_value=False), \
         patch.object(keyring_mod.sys, "stdin", stdin), \
         patch.object(keyring_mod, "encrypt_password", side_effect=OSError("no key")), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["enable"])
    assert exc.value.code == 1


def test_enable_tty_empty_password():
    with patch.object(keyring_mod.sys.stdin, "isatty", return_value=True), \
         patch.object(keyring_mod.getpass, "getpass", return_value=""), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["enable"])
    assert exc.value.code == 1


def test_enable_tty_mismatch():
    with patch("cli.keyring.sys.stdin.isatty", return_value=True), \
         patch("cli.keyring.getpass.getpass", side_effect=["a", "b"]), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["enable"])
    assert exc.value.code == 1


def test_enable_tty_success_software(key_env):
    with patch("cli.keyring.shutil.which", return_value=None), \
         patch("cli.keyring.os.path.exists", return_value=False), \
         patch("cli.keyring.sys.stdin.isatty", return_value=True), \
         patch("cli.keyring.getpass.getpass", side_effect=["pw", "pw"]), \
         patch("cli.keyring.os.chmod"):
        keyring_mod.run_keyring("alice", ["enable"])
    assert (key_env["keys"] / "alice").read_text().startswith("UH1:")


def test_enable_software_unlinks_tpm_files(key_env):
    (key_env["tpm"] / "alice.pub").write_text("x")
    (key_env["tpm"] / "alice.priv").write_text("y")

    def exists(p):
        p = str(p)
        if p in ("/dev/tpmrm0", "/dev/tpm0"):
            return False
        return _REAL_EXISTS(p)

    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.shutil.which", return_value=None), \
         patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.os.chmod"):
        keyring_mod.run_keyring("alice", ["enable"])
    assert not (key_env["tpm"] / "alice.pub").exists()
    assert (key_env["keys"] / "alice").read_text().startswith("UH1:")


def test_enable_tpm_success(key_env):
    def exists(p):
        p = str(p)
        if p in ("/dev/tpmrm0", "/dev/tpm0"):
            return True
        if "keyring-keys" in p:
            return False
        if p.endswith(".ctx"):
            return True
        return False

    mock_popen = MagicMock()
    mock_popen.communicate.return_value = (b"", b"")
    mock_popen.returncode = 0
    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.shutil.which", side_effect=lambda cmd: "/usr/bin/" + cmd), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("cli.keyring.subprocess.Popen", return_value=mock_popen), \
         patch("cli.keyring.os.chmod"), \
         patch("cli.keyring.os.unlink"):
        keyring_mod.run_keyring("alice", ["enable"])


def test_enable_tpm_failure(key_env):
    def exists(p):
        p = str(p)
        return p in ("/dev/tpmrm0", "/dev/tpm0") or p.endswith(".ctx")

    mock_popen = MagicMock()
    mock_popen.communicate.return_value = (b"", b"tpm boom")
    mock_popen.returncode = 1
    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.shutil.which", side_effect=lambda cmd: "/usr/bin/" + cmd), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("cli.keyring.subprocess.Popen", return_value=mock_popen), \
         patch("cli.keyring.os.chmod"), \
         patch("cli.keyring.os.unlink"), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["enable"])
    assert exc.value.code == 1


def test_enable_tpm_autoinstall(key_env):
    which_calls = {"n": 0}

    def which(cmd):
        which_calls["n"] += 1
        if which_calls["n"] <= 2:
            return None
        return "/usr/bin/" + cmd

    def exists(p):
        p = str(p)
        return p in ("/dev/tpmrm0",) or p.endswith(".ctx")

    mock_popen = MagicMock()
    mock_popen.communicate.return_value = (b"", b"")
    mock_popen.returncode = 0
    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.shutil.which", side_effect=which), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run, \
         patch("cli.keyring.subprocess.Popen", return_value=mock_popen), \
         patch("cli.keyring.os.chmod"), \
         patch("cli.keyring.os.unlink"):
        keyring_mod.run_keyring("alice", ["enable"])
        assert any("tpm2-tools" in str(c) for c in mock_run.call_args_list)


def test_enable_tpm_autoinstall_fails_falls_back(key_env):
    def exists(p):
        p = str(p)
        if p in ("/dev/tpmrm0",):
            return True
        if p.endswith((".pub", ".priv")):
            return False
        return False

    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.shutil.which", return_value=None), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.subprocess.run", side_effect=Exception("apt fail")), \
         patch("cli.keyring.os.chmod"):
        keyring_mod.run_keyring("alice", ["enable"])
    assert (key_env["keys"] / "alice").read_text().startswith("UH1:")


def test_enable_tpm_unlinks_key_file_and_ctx_unlink_fails(key_env):
    def exists(p):
        p = str(p)
        if p in ("/dev/tpmrm0", "/dev/tpm0"):
            return True
        if "keyring-keys" in p:
            return True
        if p.endswith(".ctx"):
            return True
        return False

    mock_popen = MagicMock()
    mock_popen.communicate.return_value = (b"", b"")
    mock_popen.returncode = 0
    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False

    def unlink_side_effect(path):
        if str(path).endswith(".ctx"):
            raise OSError("busy")
        return None

    with patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.shutil.which", side_effect=lambda cmd: "/usr/bin/" + cmd), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("cli.keyring.subprocess.Popen", return_value=mock_popen), \
         patch("cli.keyring.os.chmod"), \
         patch("cli.keyring.os.unlink", side_effect=unlink_side_effect):
        keyring_mod.run_keyring("alice", ["enable"])


def test_disable_deletes(key_env):
    with patch("cli.keyring.os.path.exists", return_value=True), \
         patch("cli.keyring.os.unlink") as unlink:
        keyring_mod.run_keyring("alice", ["disable"])
        assert unlink.call_count == 4


def test_disable_noop(key_env):
    with patch("cli.keyring.os.path.exists", return_value=False):
        keyring_mod.run_keyring("alice", ["disable"])


def test_disable_unlink_error(key_env):
    with patch("cli.keyring.os.path.exists", return_value=True), \
         patch("cli.keyring.os.unlink", side_effect=OSError("nope")), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["disable"])
    assert exc.value.code == 1


def test_enable_tpm_success_without_ctx_file(key_env):
    """TPM success when primary ctx is already gone (skip unlink)."""
    def exists(p):
        p = str(p)
        if p in ("/dev/tpmrm0", "/dev/tpm0"):
            return True
        return False

    mock_popen = MagicMock()
    mock_popen.communicate.return_value = (b"", b"")
    mock_popen.returncode = 0
    stdin = io.StringIO("pw\n")
    stdin.isatty = lambda: False
    with patch("cli.keyring.os.path.exists", side_effect=exists), \
         patch("cli.keyring.shutil.which", side_effect=lambda cmd: "/usr/bin/" + cmd), \
         patch("cli.keyring.sys.stdin", stdin), \
         patch("cli.keyring.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("cli.keyring.subprocess.Popen", return_value=mock_popen), \
         patch("cli.keyring.os.chmod"), \
         patch("cli.keyring.os.unlink"):
        keyring_mod.run_keyring("alice", ["enable"])


def test_module_entry_calls_run_keyring():
    """Cover ``_cli_autostart`` when command is keyring."""
    builtins.ubuntu_hello_user = "bob"
    builtins.ubuntu_hello_args = SimpleNamespace(command="keyring", arguments=["disable"])
    with patch.object(keyring_mod, "run_keyring") as mock_run:
        keyring_mod._cli_autostart()
        mock_run.assert_called_once_with()


def test_module_entry_skips_when_command_not_keyring():
    """Leftover builtins from other CLI tests must not auto-run keyring."""
    builtins.ubuntu_hello_user = "bob"
    builtins.ubuntu_hello_args = SimpleNamespace(command="set", arguments=["certainty", "4.2"])
    with patch.object(keyring_mod, "run_keyring") as mock_run:
        keyring_mod._cli_autostart()
        mock_run.assert_not_called()


def test_module_entry_skips_without_builtins():
    for attr in ("ubuntu_hello_user", "ubuntu_hello_args"):
        if hasattr(builtins, attr):
            delattr(builtins, attr)
    with patch.object(keyring_mod, "run_keyring") as mock_run:
        keyring_mod._cli_autostart()
        mock_run.assert_not_called()


def test_run_keyring_uses_builtins():
    builtins.ubuntu_hello_user = "bob"
    builtins.ubuntu_hello_args = SimpleNamespace(arguments=[])
    with pytest.raises(SystemExit):
        keyring_mod.run_keyring()


def test_restore_one_user_success(capsys):
    with patch.object(keyring_mod, "restore_user", return_value=True) as restore:
        keyring_mod.run_keyring("alice", ["restore"])
        restore.assert_called_once_with("alice")
    assert "Restored login wallet password for user alice" in capsys.readouterr().out


def test_restore_one_user_failure():
    with patch.object(keyring_mod, "restore_user", return_value=False), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["restore"])
    assert exc.value.code == 1


def test_restore_all_none(capsys):
    with patch.object(keyring_mod, "restore_all_users", return_value=(0, 0)):
        keyring_mod.run_keyring("alice", ["restore", "--all"])
    assert "nothing to restore" in capsys.readouterr().out.lower()


def test_restore_all_partial_failure(capsys):
    with patch.object(keyring_mod, "restore_all_users", return_value=(1, 1)), \
         pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["restore", "--all"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "1 user(s)" in out


def test_restore_all_success(capsys):
    with patch.object(keyring_mod, "restore_all_users", return_value=(2, 0)):
        keyring_mod.run_keyring("alice", ["restore", "--all"])
    assert "2 user(s)" in capsys.readouterr().out


def test_restore_rejects_positional_user():
    with pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["restore", "bob"])
    assert exc.value.code == 1


def test_restore_rejects_restore_local_user_flag():
    with pytest.raises(SystemExit) as exc:
        keyring_mod.run_keyring("alice", ["restore", "-U", "bob"])
    assert exc.value.code == 1

