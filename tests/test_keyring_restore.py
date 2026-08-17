"""Tests for keyring_restore.py (unseal + wallet password restore)."""
from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import keyring_crypto
import keyring_restore as kr


class _Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, tuple):
            return self._value
        return (self._value,)


def test_is_safe_username():
    assert kr.is_safe_username("alice")
    assert not kr.is_safe_username("../root")
    assert not kr.is_safe_username("")


def test_list_sealed_users_software_and_tpm(tmp_path):
    keys = tmp_path / "keys"
    tpm = tmp_path / "tpm"
    keys.mkdir()
    tpm.mkdir()
    (keys / "alice").write_text("UH1:x\n")
    (keys / ".hidden").write_text("nope")
    (tpm / "bob.pub").write_text("p")
    (tpm / "bob.priv").write_text("r")
    (tpm / "orphan.pub").write_text("p")
    assert kr.list_sealed_users(str(keys), str(tpm)) == ["alice", "bob"]


def test_list_sealed_users_missing_dirs(tmp_path):
    assert kr.list_sealed_users(str(tmp_path / "nope"), str(tmp_path / "also-nope")) == []


def test_unseal_password_uh1(tmp_path, monkeypatch):
    monkeypatch.setattr(keyring_crypto, "CONFIG_DIR", tmp_path / "etc")
    monkeypatch.setattr(keyring_crypto, "MASTER_KEY_PATH", tmp_path / "etc" / "keyring-master.key")
    (tmp_path / "etc").mkdir()
    keys = tmp_path / "keys"
    keys.mkdir()
    blob = keyring_crypto.encrypt_password("secret-pw")
    (keys / "alice").write_text(blob + "\n")
    assert kr.unseal_password("alice", str(keys), str(tmp_path / "tpm")) == "secret-pw"


def test_unseal_password_rejects_unsafe_and_legacy(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "alice").write_text("deadbeef\n")
    assert kr.unseal_password("../x", str(keys)) == ""
    assert kr.unseal_password("alice", str(keys), str(tmp_path / "tpm")) == ""
    assert kr.unseal_password("missing", str(keys), str(tmp_path / "tpm")) == ""


def test_unseal_password_prefers_tpm(tmp_path, monkeypatch):
    keys = tmp_path / "keys"
    tpm = tmp_path / "tpm"
    keys.mkdir()
    tpm.mkdir()
    (keys / "alice").write_text("UH1:ignored\n")
    (tpm / "alice.pub").write_text("p")
    (tpm / "alice.priv").write_text("r")
    monkeypatch.setattr(kr.shutil, "which", lambda _n: "/usr/bin/tpm2_unseal")

    def run(cmd, **kwargs):
        if cmd[0] == "tpm2_unseal":
            return SimpleNamespace(stdout=b"tpm-secret\n")
        return SimpleNamespace(stdout=b"", returncode=0)

    assert kr.unseal_password("alice", str(keys), str(tpm), run=run) == "tpm-secret"


def test_unseal_tpm_failure_falls_back_empty(tmp_path, monkeypatch):
    tpm = tmp_path / "tpm"
    tpm.mkdir()
    (tpm / "alice.pub").write_text("p")
    (tpm / "alice.priv").write_text("r")
    monkeypatch.setattr(kr.shutil, "which", lambda _n: "/usr/bin/tpm2_unseal")

    def run(cmd, **kwargs):
        raise kr.subprocess.CalledProcessError(1, cmd)

    assert kr.unseal_password("alice", str(tmp_path / "keys"), str(tpm), run=run) == ""


def test_unseal_tpm_missing_tools(tmp_path, monkeypatch):
    tpm = tmp_path / "tpm"
    tpm.mkdir()
    (tpm / "alice.pub").write_text("p")
    (tpm / "alice.priv").write_text("r")
    monkeypatch.setattr(kr.shutil, "which", lambda _n: None)
    assert kr.unseal_password("alice", str(tmp_path / "keys"), str(tpm)) == ""


def test_session_bus_env_missing_user():
    assert kr.session_bus_env("definitely-not-a-user-xyz") is None


def test_session_bus_env_no_bus_socket(tmp_path, monkeypatch):
    pw = SimpleNamespace(pw_uid=7, pw_dir="/home/alice")
    monkeypatch.setattr(kr.pwd, "getpwnam", lambda _u: pw)
    monkeypatch.setattr(kr.os.path, "exists", lambda _p: False)
    assert kr.session_bus_env("alice") is None


def test_session_bus_env_ok(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    runtime.mkdir()
    (runtime / "bus").write_text("")
    pw = SimpleNamespace(pw_uid=4242, pw_dir="/home/alice")
    monkeypatch.setattr(kr.pwd, "getpwnam", lambda _u: pw)
    monkeypatch.setattr(kr.os.path, "exists", lambda p: str(p).endswith("/bus"))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    env = kr.session_bus_env("alice")
    assert env is not None
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/4242/bus"
    assert env["XDG_CURRENT_DESKTOP"] == "GNOME"


def test_restore_unknown_backend_uses_gnome():
    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "ReadAlias":
            return _Reply("/login")
        if method == "OpenSession":
            return _Reply(("", "/s"))
        if method == "ChangeWithMasterPassword":
            return _Reply(None)
        raise kr.RestoreError(method)

    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus"}
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    ) is True


def test_restore_skips_empty_password():
    assert kr.restore_login_wallet_password("alice", "") is False


def test_restore_skips_without_session_bus():
    assert kr.restore_login_wallet_password("alice", "pw", environ={}) is False


def test_restore_gnome_change_with_master_password():
    calls = []

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        calls.append(method)
        if method == "ReadAlias":
            return _Reply("/org/freedesktop/secrets/collection/login")
        if method == "OpenSession":
            return _Reply(("", "/session/1"))
        if method == "ChangeWithMasterPassword":
            return _Reply(None)
        raise kr.RestoreError(method)

    env = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "XDG_CURRENT_DESKTOP": "GNOME",
    }
    ok = kr.restore_login_wallet_password(
        "alice",
        "pw",
        environ=env,
        call_dbus=call_dbus,
        variant=lambda sig, val: (sig, val),
    )
    assert ok is True
    assert "ChangeWithMasterPassword" in calls


def test_restore_gnome_empty_alias_uses_default_path():
    seen = {}

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "ReadAlias":
            return _Reply("")
        if method == "OpenSession":
            return _Reply(("", "/session/1"))
        if method == "ChangeWithMasterPassword":
            seen["coll"] = params[0]
            return _Reply(None)
        raise kr.RestoreError(method)

    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "GNOME"}
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    )
    assert seen["coll"] == "/org/freedesktop/secrets/collection/login"


def test_restore_gnome_false_path():
    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "ReadAlias":
            return _Reply("/login")
        if method == "OpenSession":
            return _Reply(("", "/session/1"))
        if method == "ChangeWithMasterPassword":
            return _Reply(None)
        raise kr.RestoreError(method)

    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "GNOME"}
    with patch.object(kr, "_restore_gnome_keyring", return_value=False):
        assert kr.restore_login_wallet_password(
            "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
        ) is False


def test_restore_gnome_dbus_error():
    def call_dbus(*_a, **_k):
        raise kr.RestoreError("no daemon")

    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "GNOME"}
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    ) is False


def test_restore_kwallet_pam_open(monkeypatch):
    monkeypatch.setattr(kr, "_kwallet_pam_password_hash", lambda _p, _h: b"x" * kr._KWALLET_PAM_KEYSIZE)

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "wallets":
            return _Reply((["kdewallet"],))
        if method == "pamOpen":
            return _Reply(7)
        raise kr.RestoreError(method)

    env = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus",
        "HOME": "/home/alice",
        "XDG_CURRENT_DESKTOP": "KDE",
    }
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: (s, v)
    ) is True


def test_restore_kwallet_fallback_wallet_name_and_d5(monkeypatch):
    monkeypatch.setattr(kr, "_kwallet_pam_password_hash", lambda _p, _h: b"x" * kr._KWALLET_PAM_KEYSIZE)
    seen = []

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        seen.append((bus, method))
        if bus.endswith("6"):
            raise kr.RestoreError("no d6")
        if method == "wallets":
            return _Reply((["custom"],))
        if method == "pamOpen":
            return _Reply(3)
        raise kr.RestoreError(method)

    env = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus",
        "HOME": "/home/alice",
        "XDG_CURRENT_DESKTOP": "Plasma",
    }
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    ) is True
    assert any(b.endswith("5") for b, _m in seen)


def test_restore_kwallet_pam_open_rejected(monkeypatch):
    monkeypatch.setattr(kr, "_kwallet_pam_password_hash", lambda _p, _h: b"x" * kr._KWALLET_PAM_KEYSIZE)

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "wallets":
            return _Reply(([],))
        if method == "pamOpen":
            return _Reply(-1)
        raise kr.RestoreError(method)

    env = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus",
        "HOME": "/home/alice",
        "XDG_CURRENT_DESKTOP": "KDE",
    }
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    ) is False


def test_restore_kwallet_none_wallets_reply(monkeypatch):
    monkeypatch.setattr(kr, "_kwallet_pam_password_hash", lambda _p, _h: b"x" * kr._KWALLET_PAM_KEYSIZE)

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "wallets":
            return None
        raise kr.RestoreError(method)

    env = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus",
        "HOME": "/home/alice",
        "XDG_CURRENT_DESKTOP": "KDE",
    }
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    ) is False


def test_restore_kwallet_false_path():
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "KDE"}
    with patch.object(kr, "_restore_kwallet", return_value=False):
        assert kr.restore_login_wallet_password(
            "alice", "pw", environ=env, call_dbus=lambda *a, **k: None, variant=lambda s, v: v
        ) is False


def test_restore_kwallet_all_services_fail():
    def call_dbus(*_a, **_k):
        raise kr.RestoreError("down")

    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "KDE"}
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    ) is False


def test_restore_user_and_all(monkeypatch):
    monkeypatch.setattr(kr, "unseal_password", lambda u: "pw" if u == "alice" else "")
    monkeypatch.setattr(kr, "restore_login_wallet_password", lambda u, p: u == "alice")
    monkeypatch.setattr(kr, "list_sealed_users", lambda: ["alice", "bob"])
    assert kr.restore_user("alice") is True
    assert kr.restore_user("bob") is False
    assert kr.restore_all_users() == (1, 1)
    monkeypatch.setattr(kr, "list_sealed_users", lambda: [])
    assert kr.restore_all_users() == (0, 0)


def test_restore_all_users_uses_distinct_session_buses(monkeypatch):
    addresses = {
        "alice": "unix:path=/run/user/1000/bus",
        "bob": "unix:path=/run/user/1001/bus",
    }
    seen = []

    def fake_session_bus_env(user):
        return {
            "HOME": f"/home/{user}",
            "DBUS_SESSION_BUS_ADDRESS": addresses[user],
            "XDG_CURRENT_DESKTOP": "GNOME",
        }

    def fake_restore_user(user):
        env = fake_session_bus_env(user)
        seen.append(env["DBUS_SESSION_BUS_ADDRESS"])
        return True

    monkeypatch.setattr(kr, "list_sealed_users", lambda: ["alice", "bob"])
    monkeypatch.setattr(kr, "restore_user", fake_restore_user)

    assert kr.restore_all_users() == (2, 0)
    assert seen == [addresses["alice"], addresses["bob"]]


def test_gio_call_uses_private_connection(monkeypatch):
    seen = {}

    class FakeConn:
        def call_sync(self, *a, **k):
            return "ok"

        def close_sync(self, *a, **k):
            seen["closed"] = True

    class FakeDBusConnection:
        @staticmethod
        def new_for_address_sync(address, flags, observer, cancellable):
            seen["address"] = address
            seen["flags"] = flags
            return FakeConn()

    class FakeGio:
        DBusConnection = FakeDBusConnection

        class DBusConnectionFlags:
            AUTHENTICATION_CLIENT = 1
            MESSAGE_BUS_CONNECTION = 2

        class DBusCallFlags:
            NONE = 0

    fake_gi = SimpleNamespace(repository=SimpleNamespace(Gio=FakeGio, GLib=object()))
    monkeypatch.setitem(__import__("sys").modules, "gi", fake_gi)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository", fake_gi.repository)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.Gio", FakeGio)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.GLib", object())
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "old")
    out = kr._gio_call(
        "bus",
        "/path",
        "iface",
        "Method",
        None,
        1000,
        {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus", "XDG_RUNTIME_DIR": "/run/user/1"},
    )
    assert out == "ok"
    assert seen["address"] == "unix:path=/tmp/bus"
    assert seen["closed"] is True
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "old"


def test_gio_call_wraps_errors(monkeypatch):
    class FakeDBusConnection:
        @staticmethod
        def new_for_address_sync(*a, **k):
            raise RuntimeError("boom")

    class FakeGio:
        DBusConnection = FakeDBusConnection

        class DBusConnectionFlags:
            AUTHENTICATION_CLIENT = 1
            MESSAGE_BUS_CONNECTION = 2

    fake_gi = SimpleNamespace(repository=SimpleNamespace(Gio=FakeGio, GLib=object()))
    monkeypatch.setitem(__import__("sys").modules, "gi", fake_gi)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository", fake_gi.repository)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.Gio", FakeGio)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.GLib", object())
    try:
        kr._gio_call("b", "/p", "i", "M", None, 1, {"DBUS_SESSION_BUS_ADDRESS": "x"})
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "boom" in str(exc)


def test_glib_variant_uses_gi(monkeypatch):
    class FakeVariant:
        def __init__(self, sig, val):
            self.sig = sig
            self.val = val

    class FakeGLib:
        Variant = FakeVariant

    fake_gi = SimpleNamespace(repository=SimpleNamespace(GLib=FakeGLib))
    monkeypatch.setitem(__import__("sys").modules, "gi", fake_gi)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository", fake_gi.repository)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.GLib", FakeGLib)
    out = kr._glib_variant("(s)", "login")
    assert out.sig == "(s)"
    assert out.val == "login"


def test_unseal_empty_blob(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "alice").write_text("\n")
    assert kr.unseal_password("alice", str(keys), str(tmp_path / "tpm")) == ""


def test_unseal_tpm_failure_falls_back_to_uh1(tmp_path, monkeypatch):
    monkeypatch.setattr(keyring_crypto, "CONFIG_DIR", tmp_path / "etc")
    monkeypatch.setattr(keyring_crypto, "MASTER_KEY_PATH", tmp_path / "etc" / "keyring-master.key")
    (tmp_path / "etc").mkdir()
    keys = tmp_path / "keys"
    tpm = tmp_path / "tpm"
    keys.mkdir()
    tpm.mkdir()
    blob = keyring_crypto.encrypt_password("soft-pw")
    (keys / "alice").write_text(blob + "\n")
    (tpm / "alice.pub").write_text("p")
    (tpm / "alice.priv").write_text("r")
    monkeypatch.setattr(kr.shutil, "which", lambda _n: "/usr/bin/tpm2_unseal")

    def run(cmd, **kwargs):
        raise kr.subprocess.CalledProcessError(1, cmd)

    assert kr.unseal_password("alice", str(keys), str(tpm), run=run) == "soft-pw"


def test_unseal_tpm_unlink_error(tmp_path, monkeypatch):
    tpm = tmp_path / "tpm"
    tpm.mkdir()
    (tpm / "alice.pub").write_text("p")
    (tpm / "alice.priv").write_text("r")
    monkeypatch.setattr(kr.shutil, "which", lambda _n: "/usr/bin/tpm2_unseal")
    monkeypatch.setattr(kr.os, "unlink", lambda _p: (_ for _ in ()).throw(OSError("busy")))

    def run(cmd, **kwargs):
        if cmd[0] == "tpm2_unseal":
            return SimpleNamespace(stdout=b"ok\n")
        return SimpleNamespace(stdout=b"", returncode=0)

    assert kr.unseal_password("alice", str(tmp_path / "keys"), str(tpm), run=run) == "ok"


def test_list_sealed_users_skips_dirs_and_unsafe(tmp_path):
    keys = tmp_path / "keys"
    tpm = tmp_path / "tpm"
    keys.mkdir()
    tpm.mkdir()
    (keys / "alice").write_text("UH1:x\n")
    (keys / "not a user").write_text("nope")
    (keys / "diruser").mkdir()
    assert kr.list_sealed_users(str(keys), str(tpm)) == ["alice"]


def test_restore_uses_session_bus_env(monkeypatch):
    monkeypatch.setattr(kr, "session_bus_env", lambda _u: {"HOME": "/home/a"})
    assert kr.restore_login_wallet_password("alice", "pw") is False


def test_restore_gnome_none_alias_uses_default_path():
    seen = {}

    def call_dbus(bus, path, iface, method, params, timeout, environ):
        if method == "ReadAlias":
            return None
        if method == "OpenSession":
            return _Reply(("", "/session/1"))
        if method == "ChangeWithMasterPassword":
            seen["coll"] = params[0]
            return _Reply(None)
        raise kr.RestoreError(method)

    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "GNOME"}
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=call_dbus, variant=lambda s, v: v
    )
    assert seen["coll"] == "/org/freedesktop/secrets/collection/login"


def test_restore_kwallet_empty_services(monkeypatch):
    monkeypatch.setattr(kr, "_KWALLET_SERVICES", ())
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "KDE"}
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=lambda *a, **k: None, variant=lambda s, v: v
    ) is False


def test_gio_call_without_runtime_dir_or_prev(monkeypatch):
    seen = {}

    class FakeConn:
        def call_sync(self, *a, **k):
            return "ok"

        def close_sync(self, *a, **k):
            seen["closed"] = True

    class FakeDBusConnection:
        @staticmethod
        def new_for_address_sync(address, flags, observer, cancellable):
            seen["address"] = address
            return FakeConn()

    class FakeGio:
        DBusConnection = FakeDBusConnection

        class DBusConnectionFlags:
            AUTHENTICATION_CLIENT = 1
            MESSAGE_BUS_CONNECTION = 2

        class DBusCallFlags:
            NONE = 0

    fake_gi = SimpleNamespace(repository=SimpleNamespace(Gio=FakeGio, GLib=object()))
    monkeypatch.setitem(__import__("sys").modules, "gi", fake_gi)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository", fake_gi.repository)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.Gio", FakeGio)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.GLib", object())
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    out = kr._gio_call(
        "bus",
        "/path",
        "iface",
        "Method",
        None,
        1000,
        {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
    )
    assert out == "ok"
    assert seen["address"] == "unix:path=/tmp/bus"
    assert seen["closed"] is True
    assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ
    assert "XDG_RUNTIME_DIR" not in os.environ


class _UnpackRaises:
    def unpack(self):
        raise TypeError("not a variant")


class _UnpackNonTuple:
    def unpack(self):
        return "not-a-tuple"


class _UnpackShort:
    def unpack(self):
        return ("only-one",)


def test_dbus_index_unpack_error():
    try:
        kr._dbus_index(_UnpackRaises(), 0, "ReadAlias")
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "invalid D-Bus reply" in str(exc)


def test_dbus_index_non_tuple():
    try:
        kr._dbus_index(_UnpackNonTuple(), 0, "ReadAlias")
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "unexpected D-Bus reply shape" in str(exc)


def test_dbus_index_short_tuple():
    try:
        kr._dbus_index(_UnpackShort(), 1, "OpenSession")
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "short D-Bus reply" in str(exc)


def test_restore_unexpected_exception():
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus", "XDG_CURRENT_DESKTOP": "GNOME"}
    with patch.object(kr, "_restore_gnome_keyring", side_effect=RuntimeError("boom")):
        assert kr.restore_login_wallet_password("alice", "pw", environ=env) is False


def test_gio_call_missing_address(monkeypatch):
    class FakeGio:
        class DBusConnectionFlags:
            AUTHENTICATION_CLIENT = 1
            MESSAGE_BUS_CONNECTION = 2

        class DBusCallFlags:
            NONE = 0

    fake_gi = SimpleNamespace(repository=SimpleNamespace(Gio=FakeGio, GLib=object()))
    monkeypatch.setitem(__import__("sys").modules, "gi", fake_gi)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository", fake_gi.repository)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.Gio", FakeGio)
    try:
        kr._gio_call("b", "/p", "i", "M", None, 1, {})
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "DBUS_SESSION_BUS_ADDRESS missing" in str(exc)


def test_gio_call_sync_and_close_errors(monkeypatch):
    class FakeConn:
        def call_sync(self, *a, **k):
            raise RuntimeError("call fail")

        def close_sync(self, *a, **k):
            raise RuntimeError("close fail")

    class FakeDBusConnection:
        @staticmethod
        def new_for_address_sync(*a, **k):
            return FakeConn()

    class FakeGio:
        DBusConnection = FakeDBusConnection

        class DBusConnectionFlags:
            AUTHENTICATION_CLIENT = 1
            MESSAGE_BUS_CONNECTION = 2

        class DBusCallFlags:
            NONE = 0

    fake_gi = SimpleNamespace(repository=SimpleNamespace(Gio=FakeGio, GLib=object()))
    monkeypatch.setitem(__import__("sys").modules, "gi", fake_gi)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository", fake_gi.repository)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.Gio", FakeGio)
    monkeypatch.setitem(__import__("sys").modules, "gi.repository.GLib", object())
    try:
        kr._gio_call("b", "/p", "i", "M", None, 1, {"DBUS_SESSION_BUS_ADDRESS": "x"})
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "call fail" in str(exc)


def test_kwallet_pam_password_hash_ok(tmp_path):
    salt_dir = tmp_path / ".local" / "share" / "kwalletd"
    salt_dir.mkdir(parents=True)
    salt = b"s" * kr._KWALLET_PAM_SALTSIZE
    (salt_dir / "kdewallet.salt").write_bytes(salt)
    out = kr._kwallet_pam_password_hash("pw", str(tmp_path))
    assert out == hashlib.pbkdf2_hmac(
        "sha512",
        b"pw",
        salt,
        kr._KWALLET_PAM_ITERATIONS,
        dklen=kr._KWALLET_PAM_KEYSIZE,
    )


def test_kwallet_pam_password_hash_missing(tmp_path):
    try:
        kr._kwallet_pam_password_hash("pw", str(tmp_path))
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "salt unavailable" in str(exc)


def test_kwallet_pam_password_hash_bad_size(tmp_path):
    salt_dir = tmp_path / ".local" / "share" / "kwalletd"
    salt_dir.mkdir(parents=True)
    (salt_dir / "kdewallet.salt").write_bytes(b"short")
    try:
        kr._kwallet_pam_password_hash("pw", str(tmp_path))
        assert False, "expected RestoreError"
    except kr.RestoreError as exc:
        assert "salt file size" in str(exc)


def test_restore_kwallet_empty_services_with_home(monkeypatch):
    monkeypatch.setattr(kr, "_KWALLET_SERVICES", ())
    monkeypatch.setattr(kr, "_kwallet_pam_password_hash", lambda _p, _h: b"x" * kr._KWALLET_PAM_KEYSIZE)
    env = {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/bus",
        "HOME": "/home/alice",
        "XDG_CURRENT_DESKTOP": "KDE",
    }
    assert kr.restore_login_wallet_password(
        "alice", "pw", environ=env, call_dbus=lambda *a, **k: None, variant=lambda s, v: v
    ) is False
