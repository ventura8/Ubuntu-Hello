"""Tests for multi-DE theme_detect.py."""
import os
from unittest.mock import patch

import theme_detect


class TestDetectDesktop:
	def test_gnome(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}) == "gnome"

	def test_kde(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "KDE"}) == "kde"
		assert theme_detect.detect_desktop({"DESKTOP_SESSION": "plasma"}) == "kde"

	def test_xfce(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "XFCE"}) == "xfce"

	def test_cinnamon(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "X-Cinnamon"}) == "cinnamon"

	def test_mate(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "MATE"}) == "mate"

	def test_budgie(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "Budgie:GNOME"}) == "budgie"

	def test_lxqt(self):
		assert theme_detect.detect_desktop({"XDG_CURRENT_DESKTOP": "LXQt"}) == "lxqt"
		assert theme_detect.detect_desktop({"DESKTOP_SESSION": "lubuntu"}) == "lxqt"

	def test_unknown(self):
		assert theme_detect.detect_desktop({}) == "unknown"


class TestGetThemePreference:
	def test_gnome_prefer_dark(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "gnome")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "prefer-dark")
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_gnome_prefer_light(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "gnome")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "prefer-light")
		assert theme_detect.get_theme_preference(user="alice") == "light"

	def test_gnome_gtk_theme_dark(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "gnome")
		calls = {"n": 0}

		def fake_run(args, user=None, timeout=2.0):
			calls["n"] += 1
			# color-scheme probes return empty; gtk-theme returns dark name
			joined = " ".join(args)
			if "gtk-theme" in joined:
				return "Yaru-dark"
			return ""

		monkeypatch.setattr(theme_detect, "_run_cmd", fake_run)
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_kde_prefers_kreadconfig6(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "kde")
		seen = []

		def fake_run(args, user=None, timeout=2.0):
			seen.append(args[0])
			if args[0] == "kreadconfig6":
				return "BreezeDark"
			return ""

		monkeypatch.setattr(theme_detect, "_run_cmd", fake_run)
		assert theme_detect.get_theme_preference(user="alice") == "dark"
		assert seen[0] == "kreadconfig6"

	def test_kde_falls_back_to_kreadconfig5(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "kde")
		seen = []

		def fake_run(args, user=None, timeout=2.0):
			seen.append(args[0])
			if args[0] == "kreadconfig6":
				return ""
			if args[0] == "kreadconfig5":
				return "BreezeDark"
			return ""

		monkeypatch.setattr(theme_detect, "_run_cmd", fake_run)
		assert theme_detect.get_theme_preference(user="alice") == "dark"
		assert seen[:2] == ["kreadconfig6", "kreadconfig5"]

	def test_kde_kdeglobals_file(self, monkeypatch, tmp_path):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "kde")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "")
		home = tmp_path
		cfg = home / ".config"
		cfg.mkdir()
		(cfg / "kdeglobals").write_text("[General]\nColorScheme=BreezeDark\n")
		monkeypatch.setattr(theme_detect, "_user_home", lambda user: str(home))
		monkeypatch.setattr(theme_detect, "_read_file_text", lambda path, user=None: (cfg / "kdeglobals").read_text())
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_xfce_theme(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "xfce")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "Greybird-dark")
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_cinnamon_theme(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "cinnamon")
		monkeypatch.setattr(
			theme_detect,
			"_gnome_family_theme",
			lambda user, schema: "dark" if "cinnamon" in schema else None,
		)
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_mate_theme(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "mate")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "TraditionalGreen-dark")
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_budgie_uses_gnome_schema(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "budgie")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "prefer-dark")
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_lxqt_theme_file(self, monkeypatch, tmp_path):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "lxqt")
		cfg = tmp_path / ".config" / "lxqt"
		cfg.mkdir(parents=True)
		(cfg / "lxqt.conf").write_text("[General]\ntheme=Dark\n")
		monkeypatch.setattr(theme_detect, "_user_home", lambda user: str(tmp_path))
		monkeypatch.setattr(
			theme_detect,
			"_read_file_text",
			lambda path, user=None: open(path).read() if os.path.exists(path) else "",
		)
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_fallback_light_when_tools_missing(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "kde")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "")
		monkeypatch.setattr(theme_detect, "_read_file_text", lambda *a, **k: "")
		monkeypatch.setattr(theme_detect, "_gnome_family_theme", lambda *a, **k: None)
		assert theme_detect.get_theme_preference(user="alice", default="light") == "light"

	def test_run_cmd_as_user_uses_sudo(self, monkeypatch):
		seen = {}

		def fake_check_output(cmd, text=True, stderr=None, timeout=2.0):
			seen["cmd"] = cmd
			return "'prefer-dark'\n"

		monkeypatch.setattr(os, "geteuid", lambda: 0)
		monkeypatch.setattr(theme_detect.subprocess, "check_output", fake_check_output)
		monkeypatch.setattr(theme_detect, "_user_home", lambda user: f"/home/{user}")
		out = theme_detect._run_cmd(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], user="alice")
		assert out == "prefer-dark"
		assert seen["cmd"][:4] == ["sudo", "-u", "alice", "env"]

	def test_run_cmd_exception_returns_empty(self, monkeypatch):
		monkeypatch.setattr(
			theme_detect.subprocess,
			"check_output",
			lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
		)
		assert theme_detect._run_cmd(["false"]) == ""

	def test_user_home_fallback(self, monkeypatch):
		import pwd as real_pwd

		monkeypatch.setattr(
			real_pwd, "getpwnam", lambda _name: (_ for _ in ()).throw(KeyError("x"))
		)
		assert theme_detect._user_home("alice") == "/home/alice"

	def test_read_file_text_direct(self, tmp_path):
		path = tmp_path / "f.txt"
		path.write_text("hello", encoding="utf-8")
		assert theme_detect._read_file_text(str(path)) == "hello"

	def test_read_file_text_missing(self):
		assert theme_detect._read_file_text("/no/such/file") == ""

	def test_read_file_text_as_user(self, monkeypatch):
		monkeypatch.setattr(os, "geteuid", lambda: 0)
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "via-sudo")
		assert theme_detect._read_file_text("/x", user="alice") == "via-sudo"

	def test_name_is_dark(self):
		assert theme_detect._name_is_dark("Yaru-dark")
		assert not theme_detect._name_is_dark("Yaru")
		assert not theme_detect._name_is_dark("")

	def test_kde_light_scheme(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "kde")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "Breeze")
		assert theme_detect.get_theme_preference(user="alice") == "light"

	def test_xfce_light(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "xfce")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "Greybird")
		assert theme_detect.get_theme_preference(user="alice") == "light"

	def test_mate_light(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "mate")
		monkeypatch.setattr(theme_detect, "_run_cmd", lambda *a, **k: "TraditionalGreen")
		assert theme_detect.get_theme_preference(user="alice") == "light"

	def test_lxqt_light(self, monkeypatch, tmp_path):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "lxqt")
		cfg = tmp_path / ".config" / "lxqt"
		cfg.mkdir(parents=True)
		(cfg / "lxqt.conf").write_text("[General]\ntheme=Light\n")
		monkeypatch.setattr(theme_detect, "_user_home", lambda user: str(tmp_path))
		monkeypatch.setattr(
			theme_detect,
			"_read_file_text",
			lambda path, user=None: open(path).read() if os.path.exists(path) else "",
		)
		assert theme_detect.get_theme_preference(user="alice") == "light"

	def test_unknown_desktop_gnome_fallback(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "unknown")
		monkeypatch.setattr(theme_detect, "_gnome_family_theme", lambda *a, **k: "dark")
		assert theme_detect.get_theme_preference(user="alice") == "dark"

	def test_gnome_family_gtk_light(self, monkeypatch):
		monkeypatch.setattr(theme_detect, "detect_desktop", lambda environ=None: "gnome")

		def fake_run(args, user=None, timeout=2.0):
			joined = " ".join(args)
			if "gtk-theme" in joined:
				return "Yaru"
			return ""

		monkeypatch.setattr(theme_detect, "_run_cmd", fake_run)
		assert theme_detect.get_theme_preference(user="alice") == "light"
