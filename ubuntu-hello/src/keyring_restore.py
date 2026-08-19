"""Restore the OS login wallet password from a sealed Ubuntu Hello credential.

Used on uninstall / ``ubuntu-hello keyring restore``. Decrypts the SUW-stored
login password (TPM or UH1:) and re-asserts it as the GNOME Keyring / KWallet
master password over the user's session bus. Never prints the password.
"""
from __future__ import annotations

import hashlib
import os
import pwd
import re
import shutil
import subprocess
import sys
from typing import Callable, Optional

from keyring_crypto import decrypt_password
from wallet_backend import detect_wallet_backend

KEYRING_KEYS_DIR = "/etc/ubuntu-hello/keyring-keys"
TPM_KEYS_DIR = "/etc/ubuntu-hello/tpm-keys"

_SAFE_USER = re.compile(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$")
_KWALLET_SERVICES = (
    "org.kde.kwalletd6",
    "org.kde.kwalletd5",
)
_KWALLET_PAM_SALTSIZE = 56
_KWALLET_PAM_ITERATIONS = 50000
_KWALLET_PAM_KEYSIZE = 56
_SECRETS_BUS = "org.freedesktop.secrets"
_SECRETS_PATH = "/org/freedesktop/secrets"
_SECRETS_IFACE = "org.freedesktop.Secret.Service"
_GKR_IFACE = "org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface"
_KWALLET_IFACE = "org.kde.KWallet"


class RestoreError(Exception):
    """Non-secret failure while restoring a wallet password."""


def is_safe_username(user: str) -> bool:
    return bool(user) and bool(_SAFE_USER.fullmatch(user))


def list_sealed_users(
    keys_dir: str = KEYRING_KEYS_DIR,
    tpm_dir: str = TPM_KEYS_DIR,
) -> list[str]:
    """Return usernames that have a software blob and/or TPM pub+priv pair."""
    users: set[str] = set()
    try:
        for name in os.listdir(keys_dir):
            if name.startswith("."):
                continue
            path = os.path.join(keys_dir, name)
            if os.path.isfile(path) and is_safe_username(name):
                users.add(name)
    except OSError:
        pass
    try:
        for name in os.listdir(tpm_dir):
            if not name.endswith(".pub"):
                continue
            user = name[:-4]
            pub = os.path.join(tpm_dir, f"{user}.pub")
            priv = os.path.join(tpm_dir, f"{user}.priv")
            if is_safe_username(user) and os.path.isfile(pub) and os.path.isfile(priv):
                users.add(user)
    except OSError:
        pass
    return sorted(users)


def unseal_password(
    user: str,
    keys_dir: str = KEYRING_KEYS_DIR,
    tpm_dir: str = TPM_KEYS_DIR,
    run: Optional[Callable] = None,
) -> str:
    """Return the sealed login password for *user*, or ``""`` on failure.

    Prefers TPM when ``<user>.pub`` and ``<user>.priv`` both exist, matching
    ``try_set_keyring_authtok`` in the PAM module.
    """
    if not is_safe_username(user):
        return ""
    runner = run or subprocess.run
    pub = os.path.join(tpm_dir, f"{user}.pub")
    priv = os.path.join(tpm_dir, f"{user}.priv")
    if os.path.isfile(pub) and os.path.isfile(priv):
        password = _unseal_tpm(user, pub, priv, tpm_dir, runner)
        if password:
            return password
    key_file = os.path.join(keys_dir, user)
    try:
        with open(key_file, encoding="utf-8") as handle:
            blob = handle.readline().strip()
    except OSError:
        return ""
    if not blob:
        return ""
    try:
        return decrypt_password(blob)
    except ValueError:
        return ""


def _unseal_tpm(
    user: str,
    pub: str,
    priv: str,
    tpm_dir: str,
    run: Callable,
) -> str:
    if shutil.which("tpm2_unseal") is None:
        return ""
    pid = os.getpid()
    p_ctx = os.path.join(tpm_dir, f"p_{pid}.ctx")
    s_ctx = os.path.join(tpm_dir, f"s_{pid}.ctx")
    try:
        run(
            ["tpm2_createprimary", "-C", "o", "-c", p_ctx],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        run(
            ["tpm2_load", "-C", p_ctx, "-u", pub, "-r", priv, "-c", s_ctx],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result = run(
            ["tpm2_unseal", "-c", s_ctx],
            check=True,
            capture_output=True,
        )
        raw = result.stdout.decode("utf-8", errors="replace")
        return raw.split("\n", 1)[0]
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        for path in (p_ctx, s_ctx):
            try:
                os.unlink(path)
            except OSError:
                pass


def session_bus_env(user: str) -> Optional[dict[str, str]]:
    """Build a minimal env that can talk to *user*'s session bus."""
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return None
    runtime = f"/run/user/{pw.pw_uid}"
    bus = os.path.join(runtime, "bus")
    if not os.path.exists(bus):
        return None
    env: dict[str, str] = {
        "HOME": pw.pw_dir,
        "USER": user,
        "LOGNAME": user,
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
        "PATH": os.environ.get(
            "PATH",
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for key in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_TYPE"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env


def _dbus_index(reply, index: int, label: str):
    """Return *index* from a GLib Variant reply or raise ``RestoreError``."""
    if reply is None:
        raise RestoreError(f"{label}: empty D-Bus reply")
    try:
        values = reply.unpack()
    except Exception as exc:
        raise RestoreError(f"{label}: invalid D-Bus reply") from exc
    if not isinstance(values, tuple):
        raise RestoreError(f"{label}: unexpected D-Bus reply shape")
    if len(values) <= index:
        raise RestoreError(f"{label}: short D-Bus reply")
    return values[index]


def restore_login_wallet_password(
    user: str,
    password: str,
    environ: Optional[dict[str, str]] = None,
    call_dbus: Optional[Callable] = None,
    variant: Optional[Callable] = None,
) -> bool:
    """Re-assert *password* as the login wallet master password for *user*."""
    if not password:
        return False
    env = environ if environ is not None else session_bus_env(user)
    if not env or "DBUS_SESSION_BUS_ADDRESS" not in env:
        print(
            f"warning: no session bus for user {user}; skip wallet password restore",
            file=sys.stderr,
        )
        return False
    backend = detect_wallet_backend(env)
    dbus_call = call_dbus or _gio_call
    make_variant = variant or _glib_variant
    try:
        if backend == "kwallet":
            ok = _restore_kwallet(password, env, dbus_call, make_variant)
            if ok:
                return True
            print(
                f"warning: KWallet restore failed for {user}; "
                "confirm the wallet password matches the login password in System Settings",
                file=sys.stderr,
            )
            return False
        ok = _restore_gnome_keyring(password, env, dbus_call, make_variant)
        if ok:
            return True
        print(
            f"warning: login keyring restore failed for {user}; "
            "set the Login keyring password in Seahorse if prompts persist",
            file=sys.stderr,
        )
        return False
    except RestoreError as exc:
        print(f"warning: wallet restore failed for {user}: {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"warning: wallet restore failed for {user}: {exc}", file=sys.stderr)
        return False


def restore_user(user: str) -> bool:
    """Unseal and restore one user. Leaves sealed material in place."""
    password = ""
    try:
        password = unseal_password(user)
        if not password:
            print(
                f"warning: no sealed login password for {user}; skip wallet restore",
                file=sys.stderr,
            )
            return False
        return restore_login_wallet_password(user, password)
    finally:
        password = ""


def restore_all_users() -> tuple[int, int]:
    """Restore every sealed user. Returns ``(ok_count, fail_count)``."""
    ok = 0
    fail = 0
    users = list_sealed_users()
    if not users:
        return (0, 0)
    for user in users:
        if restore_user(user):
            ok += 1
        else:
            fail += 1
    return (ok, fail)


def _gio_call(
    bus_name: str,
    object_path: str,
    interface: str,
    method: str,
    parameters,
    timeout_ms: int,
    environ: dict[str, str],
):
    """Synchronous D-Bus method call via a private Gio connection."""
    from gi.repository import Gio

    address = environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not address:
        raise RestoreError("DBUS_SESSION_BUS_ADDRESS missing")
    try:
        conn = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
    except Exception as exc:
        raise RestoreError(str(exc)) from exc
    try:
        return conn.call_sync(
            bus_name,
            object_path,
            interface,
            method,
            parameters,
            None,
            Gio.DBusCallFlags.NONE,
            timeout_ms,
            None,
        )
    except Exception as exc:
        raise RestoreError(str(exc)) from exc
    finally:
        try:
            conn.close_sync(None)
        except Exception:
            pass


def _glib_variant(signature: str, value):
    from gi.repository import GLib

    return GLib.Variant(signature, value)


def _kwallet_salt_path(home: str) -> str:
    return os.path.join(home, ".local", "share", "kwalletd", "kdewallet.salt")


def _kwallet_pam_password_hash(password: str, home: str) -> bytes:
    """Derive the PBKDF2-SHA512 hash KWallet ``pamOpen`` expects."""
    path = _kwallet_salt_path(home)
    try:
        with open(path, "rb") as handle:
            salt = handle.read()
    except OSError as exc:
        raise RestoreError(f"KWallet salt unavailable: {exc}") from exc
    if len(salt) != _KWALLET_PAM_SALTSIZE:
        raise RestoreError("invalid KWallet salt file size")
    return hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        salt,
        _KWALLET_PAM_ITERATIONS,
        dklen=_KWALLET_PAM_KEYSIZE,
    )


def _restore_gnome_keyring(
    password: str,
    environ: dict[str, str],
    call_dbus: Callable,
    variant: Callable,
) -> bool:
    alias = call_dbus(
        _SECRETS_BUS,
        _SECRETS_PATH,
        _SECRETS_IFACE,
        "ReadAlias",
        variant("(s)", ("login",)),
        10000,
        environ,
    )
    if alias is None:
        coll_path = ""
    else:
        coll_path = _dbus_index(alias, 0, "ReadAlias") or ""
    if not coll_path:
        coll_path = "/org/freedesktop/secrets/collection/login"
    opened = call_dbus(
        _SECRETS_BUS,
        _SECRETS_PATH,
        _SECRETS_IFACE,
        "OpenSession",
        variant("(sv)", ("plain", variant("s", ""))),
        10000,
        environ,
    )
    sess_path = _dbus_index(opened, 1, "OpenSession")
    secret = variant(
        "(oayays)",
        (sess_path, b"", password.encode("utf-8"), "text/plain"),
    )
    call_dbus(
        _SECRETS_BUS,
        _SECRETS_PATH,
        _GKR_IFACE,
        "ChangeWithMasterPassword",
        variant("(o@(oayays)@(oayays))", (coll_path, secret, secret)),
        30000,
        environ,
    )
    return True


def _restore_kwallet(
    password: str,
    environ: dict[str, str],
    call_dbus: Callable,
    variant: Callable,
) -> bool:
    """Verify the sealed login password opens KWallet via ``pamOpen``.

    Does not change the wallet password; Plasma users must set it manually in
    System Settings when verification fails.
    """
    home = environ.get("HOME")
    if not home:
        raise RestoreError("HOME missing for KWallet restore")
    password_hash = _kwallet_pam_password_hash(password, home)
    last_error = None
    for bus_name in _KWALLET_SERVICES:
        obj_path = "/modules/kwalletd6" if bus_name.endswith("6") else "/modules/kwalletd5"
        try:
            wallets = call_dbus(
                bus_name,
                obj_path,
                _KWALLET_IFACE,
                "wallets",
                None,
                10000,
                environ,
            )
            names = list(_dbus_index(wallets, 0, "wallets"))
            wallet_name = "kdewallet"
            if "kdewallet" not in names and names:
                wallet_name = names[0]
            result = call_dbus(
                bus_name,
                obj_path,
                _KWALLET_IFACE,
                "pamOpen",
                variant("(sayi)", (wallet_name, password_hash, 0)),
                15000,
                environ,
            )
            handle = int(_dbus_index(result, 0, "pamOpen"))
            if handle < 0:
                raise RestoreError("KWallet pamOpen rejected")
            return True
        except RestoreError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return False
