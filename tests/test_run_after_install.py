"""Tests for Wayland-aware post-install setup wizard launcher."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "ubuntu-hello-gtk" / "bin" / "run_after_install.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("run_after_install", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rai():
    return _load_launcher()


def test_detect_wayland_display_prefers_existing_socket(tmp_path, rai):
    sock = tmp_path / "wayland-0"
    sock.write_text("")
    assert rai.detect_wayland_display(str(tmp_path)) == "wayland-0"


def test_detect_wayland_display_honors_env(tmp_path, monkeypatch, rai):
    sock = tmp_path / "wayland-1"
    sock.write_text("")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert rai.detect_wayland_display(str(tmp_path)) == "wayland-1"


def test_detect_wayland_display_glob_fallback(tmp_path, monkeypatch, rai):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    (tmp_path / "wayland-9").write_text("")
    (tmp_path / "wayland-9.lock").write_text("")
    assert rai.detect_wayland_display(str(tmp_path)) == "wayland-9"


def test_detect_wayland_display_absolute_env(tmp_path, monkeypatch, rai):
    sock = tmp_path / "wayland-0"
    sock.write_text("")
    monkeypatch.setenv("WAYLAND_DISPLAY", str(sock))
    assert rai.detect_wayland_display(str(tmp_path)) == "wayland-0"


def test_detect_wayland_display_none(tmp_path, monkeypatch, rai):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert rai.detect_wayland_display(str(tmp_path)) is None


def test_first_existing(tmp_path, rai):
    missing = tmp_path / "missing"
    present = tmp_path / "present"
    present.write_text("x")
    assert rai._first_existing(str(missing), "", str(present)) == str(present)
    assert rai._first_existing(str(missing), "") is None


def test_detect_xauthority(tmp_path, monkeypatch, rai):
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "run"
    runtime.mkdir()
    xauth = home / ".Xauthority"
    xauth.write_text("auth")
    monkeypatch.delenv("XAUTHORITY", raising=False)
    assert rai.detect_xauthority("alice", str(home), str(runtime)) == str(xauth)

    env_auth = tmp_path / "env-auth"
    env_auth.write_text("e")
    monkeypatch.setenv("XAUTHORITY", str(env_auth))
    assert rai.detect_xauthority("alice", str(home), str(runtime)) == str(env_auth)


def test_detect_xauthority_mutter_glob(tmp_path, monkeypatch, rai):
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "run"
    runtime.mkdir()
    mutter = runtime / ".mutter-Xwaylandauth.abc123"
    mutter.write_text("m")
    monkeypatch.delenv("XAUTHORITY", raising=False)
    assert rai.detect_xauthority("alice", str(home), str(runtime)) == str(mutter)


def test_build_user_gui_env_sets_runtime_wayland_and_bus(tmp_path, monkeypatch, rai):
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "run"
    runtime.mkdir()
    (runtime / "bus").write_text("")
    (runtime / "wayland-0").write_text("")

    class FakePw:
        pw_dir = str(home)
        pw_uid = 4242
        pw_shell = "/bin/bash"

    real_isdir = os.path.isdir
    real_exists = os.path.exists

    def isdir(path):
        if path == "/run/user/4242":
            return True
        return real_isdir(path)

    def exists(path):
        mapping = {
            "/run/user/4242/bus": True,
            "/run/user/4242/wayland-0": True,
        }
        if path in mapping:
            return mapping[path]
        return real_exists(path)

    monkeypatch.setattr(rai.pwd, "getpwnam", lambda _u: FakePw())
    monkeypatch.setattr(rai.os.path, "isdir", isdir)
    monkeypatch.setattr(rai.os.path, "exists", exists)
    monkeypatch.setattr(
        rai,
        "detect_wayland_display",
        lambda runtime_dir: "wayland-0" if runtime_dir == "/run/user/4242" else None,
    )
    monkeypatch.setattr(rai, "detect_xauthority", lambda *a, **k: None)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")

    env = rai.build_user_gui_env("alice")
    assert env["XDG_RUNTIME_DIR"] == "/run/user/4242"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/4242/bus"
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["HOME"] == str(home)
    assert env["XDG_CURRENT_DESKTOP"] == "GNOME"


def test_build_user_gui_env_x11_fallback(tmp_path, monkeypatch, rai):
    home = tmp_path / "home"
    home.mkdir()

    class FakePw:
        pw_dir = str(home)
        pw_uid = 99
        pw_shell = "/bin/zsh"

    monkeypatch.setattr(rai.pwd, "getpwnam", lambda _u: FakePw())
    monkeypatch.setattr(rai.os.path, "isdir", lambda _p: False)
    monkeypatch.setattr(rai, "detect_xauthority", lambda *a, **k: "/tmp/xauth")
    monkeypatch.setenv("DISPLAY", ":1")
    env = rai.build_user_gui_env("bob")
    assert env["DISPLAY"] == ":1"
    assert env["XAUTHORITY"] == "/tmp/xauth"
    assert env["SHELL"] == "/bin/zsh"


def test_build_user_gui_env_unknown_user(monkeypatch, rai):
    monkeypatch.setattr(
        rai.pwd, "getpwnam", lambda _u: (_ for _ in ()).throw(KeyError("nope"))
    )
    with pytest.raises(ValueError, match="unknown user"):
        rai.build_user_gui_env("ghost")


def test_resolve_install_user_prefers_sudo_user(monkeypatch, rai):
    monkeypatch.setenv("SUDO_USER", "alice")
    assert rai.resolve_install_user() == "alice"


def test_resolve_install_user_loginctl(monkeypatch, rai):
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(
        rai.subprocess,
        "check_output",
        lambda *a, **k: "3 1000 alice seat0\n",
    )
    assert rai.resolve_install_user() == "alice"


def test_resolve_install_user_home_fallback(tmp_path, monkeypatch, rai):
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(
        rai.subprocess,
        "check_output",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no loginctl")),
    )
    entries = {"/home": ["lost+found", ".hidden", "alice"]}
    isdir_map = {
        "/home/alice": True,
        "/home/lost+found": True,
        "/home/.hidden": True,
    }
    monkeypatch.setattr(rai.os, "listdir", lambda p: list(entries.get(p, [])))
    monkeypatch.setattr(rai.os.path, "isdir", lambda p: bool(isdir_map.get(p, False)))
    assert rai.resolve_install_user() == "alice"


def test_resolve_install_user_none(monkeypatch, rai):
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(
        rai.subprocess,
        "check_output",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.SubprocessError()),
    )
    monkeypatch.setattr(
        rai.os, "listdir", lambda _p: (_ for _ in ()).throw(OSError())
    )
    assert rai.resolve_install_user() is None


def test_main_skips_when_destdir_set(monkeypatch, rai):
    monkeypatch.setenv("DESTDIR", "/tmp/stage")
    assert rai.main() == 0


def test_main_skips_when_uh_skip_set(monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.setenv("UH_SKIP_POSTINSTALL_GUI", "1")
    assert rai.main() == 0


def test_main_skips_when_models_enrolled(tmp_path, monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.delenv("UH_FORCE_POSTINSTALL_GUI", raising=False)
    models = tmp_path / "models"
    models.mkdir()
    (models / "alice.dat").write_text("x", encoding="utf-8")
    monkeypatch.setattr(rai, "models_enrolled", lambda: True)
    logged = []
    monkeypatch.setattr(rai, "_log", logged.append)
    assert rai.main() == 0
    assert any("already enrolled" in m for m in logged)


def test_models_enrolled_true_false(tmp_path, rai):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rai.models_enrolled(str(empty)) is False
    filled = tmp_path / "filled"
    filled.mkdir()
    (filled / "bob.dat").write_bytes(b"x")
    assert rai.models_enrolled(str(filled)) is True
    missing = tmp_path / "missing"
    assert rai.models_enrolled(str(missing)) is False


def test_models_enrolled_ignores_symlink_only(tmp_path, rai):
    models = tmp_path / "models"
    models.mkdir()
    target = tmp_path / "real.dat"
    target.write_bytes(b"x")
    (models / "link.dat").symlink_to(target)
    assert rai.models_enrolled(str(models)) is False


def test_main_launches_when_only_symlink_models(tmp_path, monkeypatch, rai):
    models = tmp_path / "models"
    models.mkdir()
    target = tmp_path / "real.dat"
    target.write_bytes(b"x")
    (models / "link.dat").symlink_to(target)
    monkeypatch.setattr(rai, "models_enrolled", lambda *a, **k: False)
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.delenv("UH_FORCE_POSTINSTALL_GUI", raising=False)
    monkeypatch.setattr(rai, "_acquire_single_flight_lock", lambda: 7)
    monkeypatch.setattr(rai, "resolve_install_user", lambda: "alice")
    monkeypatch.setattr(
        rai,
        "build_user_gui_env",
        lambda _u: {"HOME": "/home/alice", "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"},
    )
    seen = {}

    def _launch(user, env, force_onboarding=False):
        seen["launched"] = True
        return MagicMock(pid=42)

    monkeypatch.setattr(rai, "launch_setup_wizard", _launch)
    monkeypatch.setattr(rai, "_log", lambda _m: None)
    assert rai.main() == 0
    assert seen.get("launched") is True


def test_main_skips_when_lock_held(tmp_path, monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.setattr(rai, "models_enrolled", lambda: False)
    monkeypatch.setattr(rai, "LOCK_PATH", str(tmp_path / "lock"))
    monkeypatch.setattr(rai, "_acquire_single_flight_lock", lambda: None)
    assert rai.main() == 0


def test_main_skips_when_no_user(monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.setattr(rai, "models_enrolled", lambda: False)
    monkeypatch.setattr(rai, "_acquire_single_flight_lock", lambda: 3)
    monkeypatch.setattr(rai, "resolve_install_user", lambda: None)
    assert rai.main() == 0


def test_main_skips_on_value_error(monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.setattr(rai, "models_enrolled", lambda: False)
    monkeypatch.setattr(rai, "_acquire_single_flight_lock", lambda: 3)
    monkeypatch.setattr(rai, "resolve_install_user", lambda: "alice")
    monkeypatch.setattr(
        rai, "build_user_gui_env", lambda _u: (_ for _ in ()).throw(ValueError("bad"))
    )
    assert rai.main() == 0


def test_main_launch_success(monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.delenv("UH_FORCE_POSTINSTALL_GUI", raising=False)
    monkeypatch.setattr(rai, "models_enrolled", lambda: False)
    monkeypatch.setattr(rai, "_acquire_single_flight_lock", lambda: 7)
    monkeypatch.setattr(rai, "resolve_install_user", lambda: "alice")
    monkeypatch.setattr(
        rai,
        "build_user_gui_env",
        lambda _u: {"HOME": "/home/alice", "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"},
    )
    proc = MagicMock()
    proc.pid = 12345
    seen = {}

    def _launch(user, env, force_onboarding=False):
        seen["force"] = force_onboarding
        return proc

    monkeypatch.setattr(rai, "launch_setup_wizard", _launch)
    monkeypatch.setattr(rai, "_log", lambda _m: None)
    assert rai.main() == 0
    assert rai._POSTINSTALL_LOCK_FD == 7
    assert seen["force"] is False


def test_main_launch_oserror(monkeypatch, rai):
    monkeypatch.delenv("DESTDIR", raising=False)
    monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
    monkeypatch.setattr(rai, "models_enrolled", lambda: False)
    monkeypatch.setattr(rai, "_acquire_single_flight_lock", lambda: 7)
    monkeypatch.setattr(rai, "resolve_install_user", lambda: "alice")
    monkeypatch.setattr(
        rai,
        "build_user_gui_env",
        lambda _u: {"HOME": "/home/alice", "DISPLAY": ":0"},
    )
    monkeypatch.setattr(
        rai,
        "launch_setup_wizard",
        lambda user, env, force_onboarding=False: (_ for _ in ()).throw(OSError("fail")),
    )
    assert rai.main() == 1


def test_launch_setup_wizard_builds_env_command(tmp_path, monkeypatch, rai):
    log = tmp_path / "post.log"
    monkeypatch.setattr(rai, "LOG_PATH", str(log))
    monkeypatch.setattr(rai.os.path, "isfile", lambda _p: False)
    seen = {}

    def fake_popen(cmd, stdout=None, stderr=None, start_new_session=None):
        seen["cmd"] = cmd
        seen["stdout"] = stdout
        return MagicMock(pid=99)

    monkeypatch.setattr(rai.subprocess, "Popen", fake_popen)
    env = {"HOME": "/home/alice", "USER": "alice", "DISPLAY": ":0"}
    proc = rai.launch_setup_wizard("alice", env)
    assert proc.pid == 99
    assert seen["cmd"][:6] == ["sudo", "-u", "alice", "-H", "--", "env"]
    assert "env" in seen["cmd"]
    assert "HOME=/home/alice" in seen["cmd"]
    assert seen["cmd"][-1] == "ubuntu-hello-gtk"
    assert "--force-onboarding" not in seen["cmd"]
    assert log.exists()


def test_launch_setup_wizard_force_flag(tmp_path, monkeypatch, rai):
    log = tmp_path / "post.log"
    monkeypatch.setattr(rai, "LOG_PATH", str(log))
    monkeypatch.setattr(rai.os.path, "isfile", lambda _p: True)
    seen = {}

    def fake_popen(cmd, stdout=None, stderr=None, start_new_session=None):
        seen["cmd"] = cmd
        return MagicMock(pid=1)

    monkeypatch.setattr(rai.subprocess, "Popen", fake_popen)
    rai.launch_setup_wizard("alice", {"HOME": "/h", "USER": "alice"}, force_onboarding=True)
    assert seen["cmd"][-2:] == ["/usr/bin/ubuntu-hello-gtk", "--force-onboarding"]


def test_resolve_install_user_prefers_seated_session(monkeypatch, rai):
    monkeypatch.delenv("SUDO_USER", raising=False)
    # manager session first, seated user session second — prefer seated.
    monkeypatch.setattr(
        rai.subprocess,
        "check_output",
        lambda *a, **k: (
            "3 1000 alice - 5830 manager - no -\n"
            "2 1000 alice seat0 5507 user tty2 no -\n"
        ),
    )
    assert rai.resolve_install_user() == "alice"


def test_single_flight_lock_blocks_second_caller(tmp_path, monkeypatch, rai):
    lock = tmp_path / "lock"
    monkeypatch.setattr(rai, "LOCK_PATH", str(lock))
    fd1 = rai._acquire_single_flight_lock()
    assert fd1 is not None
    fd2 = rai._acquire_single_flight_lock()
    assert fd2 is None
    os.close(fd1)


def test_default_runtime_paths(rai):
    assert rai.RUN_DIR == "/run/ubuntu-hello"
    assert rai.LOG_PATH == "/run/ubuntu-hello/postinstall.log"
    assert rai.LOCK_PATH == "/run/ubuntu-hello/postinstall.lock"


def test_ensure_run_dir_creates_0700(tmp_path, monkeypatch, rai):
    run_dir = tmp_path / "ubuntu-hello"
    monkeypatch.setattr(rai, "RUN_DIR", str(run_dir))
    rai._ensure_run_dir()
    assert run_dir.is_dir()
    assert not run_dir.is_symlink()
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700


def test_ensure_run_dir_refuses_symlink(tmp_path, monkeypatch, rai):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setattr(rai, "RUN_DIR", str(link))
    with pytest.raises(OSError, match="symlink"):
        rai._ensure_run_dir()
    assert target.is_dir()


def test_open_secure_prepares_run_dir(tmp_path, monkeypatch, rai):
    run_dir = tmp_path / "ubuntu-hello"
    monkeypatch.setattr(rai, "RUN_DIR", str(run_dir))
    log = run_dir / "postinstall.log"
    fd = rai._open_secure(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    assert run_dir.is_dir()
    assert log.is_file()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_open_secure_rejects_symlink(tmp_path, rai):
    target = tmp_path / "target"
    link = tmp_path / "link"
    target.write_text("x", encoding="utf-8")
    link.symlink_to(target)
    with pytest.raises(OSError, match="symlink"):
        rai._open_secure(str(link), os.O_RDWR | os.O_CREAT, 0o600)


def test_open_secure_requires_nofollow(tmp_path, monkeypatch, rai):
    monkeypatch.setattr(rai.os, "O_NOFOLLOW", 0)
    with pytest.raises(OSError, match="O_NOFOLLOW"):
        rai._open_secure(str(tmp_path / "f"), os.O_WRONLY | os.O_CREAT, 0o600)


def test_open_secure_replaces_foreign_owned_regular_file(tmp_path, monkeypatch, rai):
    path = tmp_path / "log"
    path.write_text("old\n", encoding="utf-8")
    real_lstat = os.lstat

    class _St:
        def __init__(self, base):
            self.st_mode = base.st_mode
            # Force the foreign-owner replace path without needing root.
            self.st_uid = base.st_uid + 1

    monkeypatch.setattr(rai.os, "lstat", lambda p: _St(real_lstat(p)))
    fd = rai._open_secure(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == ""


def test_log_skips_symlink_path(tmp_path, monkeypatch, rai):
    target = tmp_path / "target"
    link = tmp_path / "log"
    target.write_text("secret\n", encoding="utf-8")
    link.symlink_to(target)
    monkeypatch.setattr(rai, "LOG_PATH", str(link))
    rai._log("should-not-write")
    assert target.read_text(encoding="utf-8") == "secret\n"


def test_log_writes(tmp_path, monkeypatch, rai):
    log = tmp_path / "log.txt"
    monkeypatch.setattr(rai, "LOG_PATH", str(log))
    rai._log("hello")
    assert "hello" in log.read_text(encoding="utf-8")
