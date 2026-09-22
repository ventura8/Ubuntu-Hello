"""Real-GTK E2E: live light/dark + accent following as root, on every supported desktop.

Manual testing found the elevated Settings app needed a restart after a theme
switch. Root cannot use Gio.Settings on the user's dconf, so the app follows
the desktop's own change feed run *as the user*. A second manual round found
that feed silent: `gsettings monitor` gets its notifications over the user's
session bus, and `sudo -u` had no DBUS_SESSION_BUS_ADDRESS.

Here the whole chain runs for real, per desktop: the argv the app builds,
spawned through a fake `sudo` on PATH that records the environment it was
given and streams change lines, the watcher thread, the GLib debounce and
the GTK settings flipping on the running main loop. KDE / LXQt have no feed
tool; their GTK settings files are watched with Gio.FileMonitor instead.
"""
from __future__ import annotations

import os
import stat
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

import pytest

import theme_detect
import window

FEED_DESKTOPS = [
	("ubuntu:GNOME", "gsettings", "org.gnome.desktop.interface"),
	("Budgie:GNOME", "gsettings", "org.gnome.desktop.interface"),
	("X-Cinnamon", "gsettings", "org.cinnamon.desktop.interface"),
	("MATE", "gsettings", "org.mate.interface"),
	("XFCE", "xfconf-query", "xsettings"),
]
FILE_DESKTOPS = ["KDE", "LXQt"]


def _fake_sudo(tmp_path):
	"""A `sudo` that records its argv + env assignments, prints one change line, then waits."""
	bindir = tmp_path / "bin"
	bindir.mkdir()
	log = tmp_path / "sudo.log"
	script = bindir / "sudo"
	script.write_text(
		"#!/bin/sh\n"
		f"printf '%s\\n' \"$*\" > '{log}'\n"
		"echo \"color-scheme: 'prefer-dark'\"\n"
		"echo \"gtk-theme: 'Yaru-dark'\"\n"
		"exec sleep 1000\n"
	)
	script.chmod(script.stat().st_mode | stat.S_IEXEC)
	return bindir, log


def _themes(tmp_path):
	for name in ("Yaru", "Yaru-dark"):
		(tmp_path / "themes" / name / "gtk-4.0").mkdir(parents=True)
	return str(tmp_path / "themes")


def _pump_until(gtk_pump, cond, seconds=4.0):
	deadline = time.monotonic() + seconds
	while time.monotonic() < deadline:
		gtk_pump(10)
		if cond():
			return True
		time.sleep(0.01)
	return False


def _as_root_with_desktop(monkeypatch, tmp_path, desktop, state):
	"""Patch the root code path: real user alice, detected prefs from `state`, themes under tmp."""
	themes = _themes(tmp_path)
	monkeypatch.setattr(window.os, "geteuid", lambda: 0)
	monkeypatch.setattr(theme_detect.os, "geteuid", lambda: 0)
	monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
	monkeypatch.setattr(window, "get_real_user", lambda: "alice")
	monkeypatch.setattr(window, "get_user_theme_preference", lambda user=None: state["pref"])
	monkeypatch.setattr(window, "get_user_animations_preference", lambda user=None: True)
	monkeypatch.setattr(theme_detect, "get_gtk_theme_name", lambda user=None, environ=None: state["theme"])
	monkeypatch.setattr(theme_detect, "get_icon_theme_name", lambda user=None, environ=None, icons_dir="": "")
	monkeypatch.setattr(theme_detect, "_user_home", lambda u: str(tmp_path / "home"))
	monkeypatch.setattr(theme_detect, "_user_bus_env",
	                    lambda u: ["XDG_RUNTIME_DIR=/run/user/1000", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"])
	real_resolve = theme_detect.resolve_gtk4_theme
	monkeypatch.setattr(theme_detect, "resolve_gtk4_theme",
	                    lambda name, dark, themes_dir="/usr/share/themes": real_resolve(name, dark, themes))
	monkeypatch.setattr(window, "_theme_watcher", None)
	monkeypatch.setattr(theme_detect.atexit, "register", lambda fn: None)
	(tmp_path / "home" / ".config" / "gtk-4.0").mkdir(parents=True)


class TestLiveThemeOnEveryDesktop:
	@pytest.mark.parametrize("desktop,tool,schema", FEED_DESKTOPS)
	def test_feed_desktops_switch_light_to_dark_without_restart(self, gtk_pump, tmp_path, monkeypatch, desktop, tool, schema):
		state = {"pref": "light", "theme": "Yaru"}
		_as_root_with_desktop(monkeypatch, tmp_path, desktop, state)
		bindir, log = _fake_sudo(tmp_path)
		monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
		settings = Gtk.Settings.get_default()
		settings.set_property("gtk-application-prefer-dark-theme", False)

		window.setup_theme()          # the real entry point used by Settings and the wizard
		try:
			watcher = window._theme_watcher
			assert watcher is not None, "no feed started for " + desktop
			assert watcher.process is not None, "no feed started for " + desktop
			assert settings.get_property("gtk-theme-name") == "Yaru"

			# the desktop switches to dark: the feed (fake sudo) prints two change lines
			state.update(pref="dark", theme="Yaru-dark")
			assert _pump_until(gtk_pump, lambda: settings.get_property("gtk-theme-name") == "Yaru-dark"), desktop
			assert settings.get_property("gtk-application-prefer-dark-theme") is True

			# the feed was spawned as the user with the session bus (the silent-feed bug)
			argv = log.read_text().split()
			assert argv[:4] == ["-u", "alice", "-H", "env"], argv
			assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in argv
			assert "XDG_RUNTIME_DIR=/run/user/1000" in argv
			assert tool in argv, argv
			assert schema in argv, argv
		finally:
			if window._theme_watcher is not None:
				window._theme_watcher.stop()
			settings.set_property("gtk-application-prefer-dark-theme", False)
			settings.set_property("gtk-theme-name", "Default")

	@pytest.mark.parametrize("desktop", FILE_DESKTOPS)
	def test_file_desktops_switch_when_settings_ini_changes(self, gtk_pump, tmp_path, monkeypatch, desktop):
		state = {"pref": "light", "theme": "Yaru"}
		_as_root_with_desktop(monkeypatch, tmp_path, desktop, state)
		ini = tmp_path / "home" / ".config" / "gtk-4.0" / "settings.ini"
		ini.write_text("[Settings]\ngtk-theme-name=Yaru\n")
		settings = Gtk.Settings.get_default()
		settings.set_property("gtk-application-prefer-dark-theme", False)

		window.setup_theme()
		try:
			watcher = window._theme_watcher
			assert watcher is not None
			assert watcher.process is None                 # no feed tool on KDE / LXQt …
			assert watcher.monitors, "settings.ini must be watched on " + desktop   # … files are
			gtk_pump(50)

			# kde-gtk-config / lxqt-config rewrite settings.ini on a theme change
			state.update(pref="dark", theme="Yaru-dark")
			ini.write_text("[Settings]\ngtk-theme-name=Yaru-dark\ngtk-application-prefer-dark-theme=1\n")
			assert _pump_until(gtk_pump, lambda: settings.get_property("gtk-theme-name") == "Yaru-dark"), desktop
			assert settings.get_property("gtk-application-prefer-dark-theme") is True
		finally:
			if window._theme_watcher is not None:
				window._theme_watcher.stop()
			settings.set_property("gtk-application-prefer-dark-theme", False)
			settings.set_property("gtk-theme-name", "Default")

	def test_feed_without_session_bus_is_the_bug_we_fixed(self, monkeypatch, tmp_path):
		"""Regression guard: the root argv must always carry the user's bus when one exists."""
		monkeypatch.setattr(theme_detect.os, "geteuid", lambda: 0)
		monkeypatch.setattr(theme_detect, "_user_home", lambda u: str(tmp_path))
		monkeypatch.setattr(theme_detect, "_user_bus_env", lambda u: ["DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"])
		for desktop, tool, schema in FEED_DESKTOPS:
			argv = theme_detect.theme_monitor_command("alice", {"XDG_CURRENT_DESKTOP": desktop})
			assert argv[0] == "sudo", desktop
			assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in argv, desktop
			assert argv.index("env") < argv.index(tool)
		for desktop in FILE_DESKTOPS:
			assert theme_detect.theme_monitor_command("alice", {"XDG_CURRENT_DESKTOP": desktop}) is None
