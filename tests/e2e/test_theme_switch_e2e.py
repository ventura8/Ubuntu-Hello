"""Real-GTK E2E: the elevated app follows a light/dark + accent switch live (no restart).

As root the app cannot use Gio.Settings on the user's dconf, so it reads the
desktop's change feed (``gsettings monitor`` as the user) through
``theme_detect.ThemeWatcher``. Here the feed is a real subprocess printing
change lines on a schedule; the callback re-applies the theme through the
same ``window.apply_gtk_theme_name`` the app uses, and the GTK settings are
observed to flip on the running main loop.
"""
from __future__ import annotations

import os
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

import theme_detect
import window


def _themes_dir(tmp_path):
	for name in ("Yaru", "Yaru-dark", "Yaru-blue", "Yaru-blue-dark"):
		(tmp_path / name / "gtk-4.0").mkdir(parents=True)
	return str(tmp_path)


class TestLiveThemeSwitch:
	def test_light_dark_and_accent_follow_the_desktop_feed(self, gtk_pump, tmp_path, monkeypatch):
		themes = _themes_dir(tmp_path)
		settings = Gtk.Settings.get_default()
		# Desktop state the user flips; the feed line only says "something changed".
		desktop = {"theme": "Yaru", "dark": False}
		applied = []

		def reapply():
			resolved = theme_detect.resolve_gtk4_theme(desktop["theme"], desktop["dark"], themes_dir=themes)
			settings.set_property("gtk-application-prefer-dark-theme", desktop["dark"])
			settings.set_property("gtk-theme-name", resolved)
			applied.append(resolved)

		# The change feed is a real pipe the test writes to, like `gsettings monitor` stdout.
		read_fd, write_fd = os.pipe()
		feed = os.fdopen(write_fd, "w", buffering=1)

		class PipeProc:
			def __init__(self):
				self.stdout = os.fdopen(read_fd, "r")
				self.terminated = False

			def poll(self):
				return 1 if self.terminated else None

			def terminate(self):
				self.terminated = True
				feed.close()

		w = theme_detect.ThemeWatcher("nobody", reapply, environ={"XDG_CURRENT_DESKTOP": "GNOME"},
		                              popen=lambda cmd, **kw: PipeProc(), file_monitor=False)
		monkeypatch.setattr(theme_detect.atexit, "register", lambda fn: None)
		w.start()

		def burst():
			feed.write("color-scheme: 'x'\n")
			feed.write("gtk-theme: 'x'\n")
			feed.flush()

		try:
			assert w.process is not None and w.process.poll() is None

			# 1st burst: user switched to dark
			desktop.update(theme="Yaru-dark", dark=True)
			burst()
			self._pump_until(gtk_pump, lambda: applied and applied[-1] == "Yaru-dark")
			assert settings.get_property("gtk-theme-name") == "Yaru-dark"
			assert settings.get_property("gtk-application-prefer-dark-theme") is True

			# 2nd burst: accent changed while dark
			desktop.update(theme="Yaru-blue-dark", dark=True)
			burst()
			self._pump_until(gtk_pump, lambda: applied and applied[-1] == "Yaru-blue-dark")
			assert settings.get_property("gtk-theme-name") == "Yaru-blue-dark"

			# 3rd burst: back to light, accent kept
			desktop.update(theme="Yaru-blue", dark=False)
			burst()
			self._pump_until(gtk_pump, lambda: applied and applied[-1] == "Yaru-blue")
			assert settings.get_property("gtk-theme-name") == "Yaru-blue"
			assert settings.get_property("gtk-application-prefer-dark-theme") is False

			# each burst of two lines produced exactly one re-apply (debounced)
			assert len(applied) == 3, applied
			assert w.events == 6
		finally:
			w.stop()
			for _ in range(50):
				if w.thread is None or not w.thread.is_alive():
					break
				time.sleep(0.02)
		# stop() terminated the feed so no `gsettings monitor` outlives the app
		assert w.process is None
		settings.set_property("gtk-application-prefer-dark-theme", False)

	@staticmethod
	def _pump_until(gtk_pump, cond, seconds=3.0):
		deadline = time.monotonic() + seconds
		while time.monotonic() < deadline:
			gtk_pump(10)
			if cond():
				return
			time.sleep(0.01)
		raise AssertionError("condition not met in %.1fs" % seconds)

	def test_root_setup_theme_wires_the_watcher_to_the_real_reapply(self, gtk_pump, tmp_path, monkeypatch):
		"""window.setup_theme() as root: startup apply + watcher whose callback re-reads the desktop."""
		themes = _themes_dir(tmp_path)
		state = {"pref": "light", "theme": "Yaru"}
		monkeypatch.setattr(window.os, "geteuid", lambda: 0)
		monkeypatch.setattr(window, "get_real_user", lambda: "alice")
		monkeypatch.setattr(window, "get_user_theme_preference", lambda user=None: state["pref"])
		monkeypatch.setattr(window, "get_user_animations_preference", lambda user=None: True)
		monkeypatch.setattr(theme_detect, "get_gtk_theme_name", lambda user=None: state["theme"])
		monkeypatch.setattr(theme_detect, "get_icon_theme_name", lambda user=None: "")
		real_resolve = theme_detect.resolve_gtk4_theme
		monkeypatch.setattr(theme_detect, "resolve_gtk4_theme",
		                    lambda name, dark, themes_dir="/usr/share/themes": real_resolve(name, dark, themes))
		monkeypatch.setattr(window, "_theme_watcher", None)
		monkeypatch.setattr(theme_detect.atexit, "register", lambda fn: None)
		captured = {}

		class Feed(theme_detect.ThemeWatcher):
			def start(self):
				captured["cb"] = self.on_change
				return self

		monkeypatch.setattr(theme_detect, "ThemeWatcher", Feed)
		settings = Gtk.Settings.get_default()
		window.setup_theme()
		gtk_pump(10)
		assert settings.get_property("gtk-application-prefer-dark-theme") is False
		assert "cb" in captured

		state.update(pref="dark", theme="Yaru-dark")
		captured["cb"]()                 # probes on a worker thread, applied via GLib.idle_add
		self._pump_until(gtk_pump, lambda: settings.get_property("gtk-application-prefer-dark-theme") is True)
		settings.set_property("gtk-application-prefer-dark-theme", False)
