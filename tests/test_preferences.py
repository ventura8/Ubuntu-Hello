"""Unit tests for user UI preferences (language override)."""
from __future__ import annotations

import os
from pathlib import Path

import preferences


def test_read_language_default_auto(tmp_path, monkeypatch):
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(tmp_path / "preferences.ini"))
	assert preferences.read_language() == preferences.AUTO


def test_write_and_read_language(tmp_path, monkeypatch):
	path = tmp_path / "preferences.ini"
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	preferences.write_language("ro")
	assert preferences.read_language() == "ro"
	text = path.read_text(encoding="utf-8")
	assert "[ui]" in text
	assert "language = ro" in text


def test_write_auto(tmp_path, monkeypatch):
	path = tmp_path / "preferences.ini"
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	preferences.write_language("de")
	preferences.write_language("auto")
	assert preferences.read_language() == preferences.AUTO


def test_invalid_file_falls_back_auto(tmp_path, monkeypatch):
	path = tmp_path / "preferences.ini"
	path.write_text("not-ini{{{", encoding="utf-8")
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	assert preferences.read_language() == preferences.AUTO


def test_preferences_path_override(monkeypatch, tmp_path):
	path = tmp_path / "custom.ini"
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	assert preferences.preferences_path() == str(path)


def test_preferences_path_sudo_user_home(tmp_path, monkeypatch):
	monkeypatch.delenv("UH_PREFERENCES_FILE", raising=False)
	home = tmp_path / "alice"
	home.mkdir()

	class FakePw:
		pw_dir = str(home)

	monkeypatch.setenv("SUDO_USER", "alice")
	monkeypatch.setattr(preferences.pwd, "getpwnam", lambda _u: FakePw())
	assert preferences.preferences_path() == str(
		home / ".config" / "ubuntu-hello" / "preferences.ini"
	)


def test_preferences_path_pkexec_uid(tmp_path, monkeypatch):
	monkeypatch.delenv("UH_PREFERENCES_FILE", raising=False)
	monkeypatch.delenv("SUDO_USER", raising=False)
	home = tmp_path / "bob"
	home.mkdir()

	class FakePw:
		pw_dir = str(home)

	monkeypatch.setenv("PKEXEC_UID", "1001")
	monkeypatch.setattr(preferences.pwd, "getpwuid", lambda _u: FakePw())
	assert preferences.preferences_path() == str(
		home / ".config" / "ubuntu-hello" / "preferences.ini"
	)


def test_preferences_path_xdg_config_home(tmp_path, monkeypatch):
	monkeypatch.delenv("UH_PREFERENCES_FILE", raising=False)
	monkeypatch.delenv("SUDO_USER", raising=False)
	monkeypatch.delenv("PKEXEC_UID", raising=False)
	xdg = tmp_path / "xdg"
	xdg.mkdir()
	monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
	assert preferences.preferences_path() == str(xdg / "ubuntu-hello" / "preferences.ini")


def test_preferences_path_expanduser(tmp_path, monkeypatch):
	monkeypatch.delenv("UH_PREFERENCES_FILE", raising=False)
	monkeypatch.delenv("SUDO_USER", raising=False)
	monkeypatch.delenv("PKEXEC_UID", raising=False)
	monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
	monkeypatch.setattr(preferences.os.path, "expanduser", lambda _p: str(tmp_path))
	assert preferences.preferences_path() == str(
		tmp_path / ".config" / "ubuntu-hello" / "preferences.ini"
	)


def test_real_user_home_keyerror(monkeypatch):
	monkeypatch.setenv("SUDO_USER", "missing")
	monkeypatch.setattr(
		preferences.pwd, "getpwnam", lambda _u: (_ for _ in ()).throw(KeyError())
	)
	monkeypatch.delenv("PKEXEC_UID", raising=False)
	assert preferences._real_user_home() is None


def test_real_user_home_bad_pkexec(monkeypatch):
	monkeypatch.delenv("SUDO_USER", raising=False)
	monkeypatch.setenv("PKEXEC_UID", "not-an-int")
	assert preferences._real_user_home() is None


def test_write_language_empty_and_corrupt_existing(tmp_path, monkeypatch):
	path = tmp_path / "preferences.ini"
	path.write_text("{{{{", encoding="utf-8")
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	preferences.write_language("  ")
	assert preferences.read_language() == preferences.AUTO


def test_write_language_chown_when_root(tmp_path, monkeypatch):
	cfg_dir = tmp_path / "cfg"
	path = cfg_dir / "preferences.ini"
	home = tmp_path / "home"
	home.mkdir()
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	monkeypatch.setattr(preferences, "_real_user_home", lambda: str(home))
	monkeypatch.setattr(preferences.os, "geteuid", lambda: 0)
	seen = []

	def fake_chown(target, uid, gid):
		seen.append((str(target), uid, gid))

	real_stat = preferences.os.stat

	def fake_stat(target):
		# Only mock the real-user home lookup used for chown; keep file checks real.
		if str(target) == str(home):
			class Stat:
				st_uid = 1000
				st_gid = 1000
				st_mode = 0o40700

			return Stat()
		return real_stat(target)

	monkeypatch.setattr(preferences.os, "stat", fake_stat)
	monkeypatch.setattr(preferences.os, "chown", fake_chown)
	preferences.write_language("fr")
	assert preferences.read_language() == "fr"
	assert any(str(path) == t for t, _u, _g in seen)


def test_read_language_missing_section(tmp_path, monkeypatch):
	path = tmp_path / "preferences.ini"
	path.write_text("[other]\nx=1\n", encoding="utf-8")
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	assert preferences.read_language() == preferences.AUTO
