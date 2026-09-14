"""Unit tests for window.py behaviours found in manual GTK 4 testing (gi mocked by conftest)."""
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ["BYPASS_ELEVATE"] = "1"
os.environ["UH_DONT_AUTO_LAUNCH"] = "1"
import window  # env above disables the module-level launch
import gtk4compat


def _win():
	w = window.MainWindow.__new__(window.MainWindow)
	MagicMock.__init__(w)          # MainWindow derives from the mocked Gtk.Window; skip its real __init__
	w.window = MagicMock()
	w.userlist = MagicMock(); w.cameraselect = MagicMock(); w.language_combo = MagicMock()
	w._rebuilding = False
	w._language_combo_ready = True
	return w


class SyncThread:
	def __init__(self, target, args=(), kwargs=None, daemon=True):
		self.target, self.args, self.kwargs = target, args, kwargs or {}

	def start(self):
		self.target(*self.args, **self.kwargs)


class TestAboutLinks:
	URI = "https://github.com/ventura8/Ubuntu-Hello/issues"

	def _env(self, monkeypatch):
		monkeypatch.setenv("DISPLAY", ":0"); monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
		monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
		monkeypatch.setenv("XDG_DATA_DIRS", "/usr/share:/var/lib/snapd/desktop")
		monkeypatch.delenv("XAUTHORITY", raising=False)

	def test_prefers_the_desktop_portal_on_the_users_session_bus(self, monkeypatch):
		w = _win()
		monkeypatch.setattr(window, "get_real_user", lambda: "alice")
		self._env(monkeypatch)
		with patch("window.threading.Thread", SyncThread), \
		     patch("window.subprocess.run", return_value=MagicMock(returncode=0)) as run, \
		     patch("window.subprocess.Popen") as popen:
			assert w.on_about_link(MagicMock(), self.URI) is True
		args = run.call_args.args[0]
		assert args[:4] == ["sudo", "-u", "alice", "-H"]
		assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in args and "XDG_DATA_DIRS=/usr/share:/var/lib/snapd/desktop" in args
		assert "org.freedesktop.portal.OpenURI.OpenURI" in args and args[-2] == self.URI
		popen.assert_not_called()

	def test_falls_back_to_xdg_open_with_session_env(self, monkeypatch):
		w = _win()
		monkeypatch.setattr(window, "get_real_user", lambda: "alice")
		self._env(monkeypatch)
		with patch("window.threading.Thread", SyncThread), \
		     patch("window.subprocess.run", side_effect=OSError("no gdbus")), \
		     patch("window.subprocess.Popen") as popen:
			assert w.on_about_link(MagicMock(), self.URI) is True
		args = popen.call_args.args[0]
		assert args[:4] == ["sudo", "-u", "alice", "-H"]
		assert "DISPLAY=:0" in args and "WAYLAND_DISPLAY=wayland-0" in args and "XDG_DATA_DIRS=/usr/share:/var/lib/snapd/desktop" in args
		assert args[-2:] == ["xdg-open", self.URI]
		assert popen.call_args.kwargs["start_new_session"] is True   # never blocks the UI

	def test_portal_failure_status_falls_back(self):
		with patch("window.subprocess.run", return_value=MagicMock(returncode=1)), patch("window.subprocess.Popen") as popen:
			assert window.open_uri_as_user("alice", self.URI, ["DISPLAY=:0"]) == "xdg-open"
		popen.assert_called_once()

	@pytest.mark.parametrize("user,uri", [("root", "https://x"), ("", "https://x"), ("alice", "file:///etc/passwd"), ("bad user", "https://x")])
	def test_refuses_root_or_non_http(self, monkeypatch, user, uri):
		w = _win()
		monkeypatch.setattr(window, "get_real_user", lambda: user)
		with patch("window.threading.Thread", SyncThread), patch("window.subprocess.run") as run, patch("window.subprocess.Popen") as popen:
			assert w.on_about_link(MagicMock(), uri) is True
		run.assert_not_called(); popen.assert_not_called()

	def test_popen_failure_is_reported_not_raised(self, capsys):
		with patch("window.subprocess.run", side_effect=OSError("no sudo")), patch("window.subprocess.Popen", side_effect=OSError("no sudo")):
			assert window.open_uri_as_user("alice", self.URI, []) is None
		assert "Could not open" in capsys.readouterr().err


class TestPopoversNeverMappedByPageLogic:
	def test_reveal_search_page_skips_popovers(self):
		"""Showing a dropdown's popover maps a grabbing popup: froze the app on Wayland."""
		w = _win()
		gtk = window.gtk
		label = gtk.Label(); popover = gtk.Popover(); dropdown = gtk.DropDown(); page = gtk.Box()
		inner = gtk.Label()
		popover.get_first_child = lambda: inner; inner.get_next_sibling = lambda: None
		dropdown.get_first_child = lambda: popover; popover.get_next_sibling = lambda: None
		page.get_first_child = lambda: label; label.get_next_sibling = lambda: dropdown; dropdown.get_next_sibling = lambda: None
		label.get_first_child = lambda: None
		w.notebook = MagicMock(); w.notebook.get_nth_page.return_value = page
		w.reveal_search_page(1)
		label.set_visible.assert_called_with(True)
		dropdown.set_visible.assert_called_with(True)
		popover.set_visible.assert_not_called()
		inner.set_visible.assert_not_called()

	def test_settle_page_focus_closes_lists_and_clears_focus(self, monkeypatch):
		w = _win()
		closed = []
		monkeypatch.setattr(gtk4compat, "close_popover", lambda widget: closed.append(widget))
		assert w.settle_page_focus() is False
		assert closed == [w.userlist.widget, w.cameraselect.widget, w.language_combo.widget]
		w.window.set_focus.assert_called_once_with(None)

	def test_page_switch_schedules_focus_settling(self):
		import tab_video
		w = _win(); w.builder = MagicMock(); w.reveal_search_page = MagicMock()
		with patch("tab_video.GLib.idle_add") as idle_add:
			tab_video.on_page_switch(w, MagicMock(), MagicMock(), 0)
		idle_add.assert_called_once_with(w.settle_page_focus)


class TestLanguageSwitch:
	def test_change_is_deferred_and_closes_the_list_first(self, monkeypatch):
		"""Rebuilding inside the dropdown's own handler (popover grab live) froze GTK 4."""
		w = _win()
		w.language_combo.get_active_id.return_value = "de"
		monkeypatch.setattr(window.preferences, "read_language", lambda: "auto")
		monkeypatch.setattr(window.languages, "is_known_language", lambda code: True)
		w.settle_page_focus = MagicMock(return_value=False)
		with patch("window.GLib.timeout_add") as timeout_add, patch.object(w, "_apply_language_rebuild") as apply:
			w.on_language_changed(w.language_combo)
		apply.assert_not_called()
		w.settle_page_focus.assert_called_once()
		assert w._rebuilding is True
		timeout_add.assert_called_once_with(200, w._deferred_language_rebuild, "de")

	def test_same_language_is_a_no_op(self, monkeypatch):
		w = _win()
		w.language_combo.get_active_id.return_value = "de"
		monkeypatch.setattr(window.preferences, "read_language", lambda: "de")
		monkeypatch.setattr(window.languages, "is_known_language", lambda code: True)
		written = []
		monkeypatch.setattr(window.preferences, "write_language", written.append)
		with patch("window.GLib.timeout_add") as timeout_add:
			w.on_language_changed(w.language_combo)
		timeout_add.assert_not_called()
		assert written == ["de"] and w._rebuilding is False

	def test_deferred_rebuild_never_leaves_window_stuck(self, capsys):
		w = _win()
		w._rebuilding = True
		w.settle_page_focus = MagicMock(return_value=False)
		with patch.object(w, "_apply_language_rebuild", side_effect=RuntimeError("boom")):
			assert w._deferred_language_rebuild("ar") is False
		assert w._rebuilding is False
		assert "Language rebuild failed" in capsys.readouterr().err
		with patch.object(w, "_apply_language_rebuild") as apply:
			assert w._deferred_language_rebuild("ar") is False
		apply.assert_called_once_with("ar")


class TestThemeFollowing:
	def test_apply_gtk_theme_name_sets_theme_and_icons(self, monkeypatch):
		import theme_detect
		monkeypatch.setattr(theme_detect, "resolve_gtk4_theme", lambda name, dark: "Yaru-red-dark")
		settings = window.gtk.Settings.get_default.return_value
		assert window.apply_gtk_theme_name("Yaru-red", True, "Yaru") == "Yaru-red-dark"
		settings.set_property.assert_any_call("gtk-theme-name", "Yaru-red-dark")
		settings.set_property.assert_any_call("gtk-icon-theme-name", "Yaru")

	def test_apply_gtk_theme_name_without_gtk4_variant(self, monkeypatch):
		import theme_detect
		monkeypatch.setattr(theme_detect, "resolve_gtk4_theme", lambda name, dark: None)
		settings = window.gtk.Settings.get_default.return_value
		settings.reset_mock()
		assert window.apply_gtk_theme_name("Nope", False, "") is None
		settings.set_property.assert_not_called()


class TestSettingsSearchControls:
	def test_escape_clears_query(self):
		w = _win()
		entry = MagicMock(); entry.get_text.return_value = "cam"
		assert w.on_settings_search_stop(entry) is True
		entry.set_text.assert_called_once_with("")
		entry.reset_mock(); entry.get_text.return_value = ""
		w.on_settings_search_stop(entry)
		entry.set_text.assert_not_called()

	def test_ctrl_f_focuses_search(self):
		w = _win(); w.settings_search = MagicMock()
		assert w.focus_settings_search() is True
		w.settings_search.grab_focus.assert_called_once()
		w.settings_search = None
		assert w.focus_settings_search() is True


class TestElevationAndUser:
	def test_elevate_forwards_session_env_via_pkexec(self, monkeypatch):
		monkeypatch.setattr(window.os, "geteuid", lambda: 1000)
		monkeypatch.delenv("BYPASS_ELEVATE", raising=False)
		monkeypatch.setenv("XDG_DATA_DIRS", "/usr/share:/var/lib/snapd/desktop")
		monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
		monkeypatch.delenv("XAUTHORITY", raising=False)
		monkeypatch.setattr(window.sys, "argv", ["ubuntu-hello-gtk"])
		calls = []
		monkeypatch.setattr(window.os, "execvp", lambda prog, args: calls.append((prog, args)))
		window.elevate()
		prog, args = calls[0]
		assert prog == "pkexec" and args[0] == "pkexec" and args[1] == window.sys.executable
		assert "--env-XDG_DATA_DIRS=/usr/share:/var/lib/snapd/desktop" in args and "--env-WAYLAND_DISPLAY=wayland-0" in args
		assert not any(a.startswith("--env-XAUTHORITY") for a in args)

	def test_elevate_falls_back_to_sudo(self, monkeypatch):
		monkeypatch.setattr(window.os, "geteuid", lambda: 1000)
		monkeypatch.delenv("BYPASS_ELEVATE", raising=False)
		calls = []

		def execvp(prog, args):
			calls.append(prog)
			if prog == "pkexec":
				raise OSError("no pkexec")
		monkeypatch.setattr(window.os, "execvp", execvp)
		window.elevate()
		assert calls == ["pkexec", "sudo"]

	def test_elevate_noop_as_root(self, monkeypatch):
		monkeypatch.setattr(window.os, "geteuid", lambda: 0)
		with patch("window.os.execvp") as execvp:
			window.elevate()
		execvp.assert_not_called()

	def test_env_args_are_imported_into_environ(self, monkeypatch, tmp_path):
		"""`--env-VAR=value` argv entries (from elevate) become environment variables."""
		import runpy
		monkeypatch.setattr(sys, "argv", ["x", "--env-DISPLAY=:9", "--env-broken", "--force-onboarding"])
		monkeypatch.delenv("DISPLAY", raising=False)
		src = open(window.__file__, encoding="utf-8").read().split("\nclass MainWindow")[0]
		ns = {"__name__": "window_env_probe", "__file__": window.__file__}
		exec(compile(src, window.__file__, "exec"), ns)
		assert os.environ.get("DISPLAY") == ":9"
		assert sys.argv == ["x", "--force-onboarding"]   # every --env-* arg is consumed, even malformed ones

	def test_get_real_user_prefers_pkexec_uid(self, monkeypatch):
		monkeypatch.setenv("SUDO_USER", "root"); monkeypatch.setenv("PKEXEC_UID", "1000")
		import pwd
		monkeypatch.setattr(pwd, "getpwuid", lambda uid: MagicMock(pw_name="alice") if uid == 1000 else (_ for _ in ()).throw(KeyError(uid)))
		assert window.get_real_user() == "alice"

	def test_get_real_user_fallbacks(self, monkeypatch):
		monkeypatch.delenv("SUDO_USER", raising=False); monkeypatch.delenv("PKEXEC_UID", raising=False)
		monkeypatch.setattr(window.os, "getlogin", lambda: (_ for _ in ()).throw(OSError("no tty")))
		monkeypatch.setenv("USER", "root")
		monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "1 1000 bob seat0 tty2\n")
		assert window.get_real_user() == "bob"
		monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError("no loginctl")))
		assert window.get_real_user() == "root"
		monkeypatch.setenv("USER", "bad name!")
		assert window.get_real_user() == "root"

	def test_user_theme_and_animation_preferences(self, monkeypatch):
		import theme_detect
		monkeypatch.setattr(window, "get_real_user", lambda: "root")
		assert window.get_user_theme_preference() == "light"
		assert window.get_user_animations_preference() is True
		monkeypatch.setattr(window, "get_real_user", lambda: "alice")
		monkeypatch.setattr(theme_detect, "get_theme_preference", lambda user=None, default="light": "dark")
		assert window.get_user_theme_preference() == "dark"
		monkeypatch.setattr(subprocess, "check_output", lambda cmd, **k: "false\n")
		assert window.get_user_animations_preference() is False
		calls = []

		def flaky(cmd, **k):
			calls.append(cmd[-1])
			if "gsettings" in cmd:
				raise OSError("no gsettings")
			return "true"
		monkeypatch.setattr(subprocess, "check_output", flaky)
		assert window.get_user_animations_preference() is True
		monkeypatch.setattr(subprocess, "check_output", lambda cmd, **k: (_ for _ in ()).throw(OSError("none")))
		assert window.get_user_animations_preference() is True


class TestSetupThemeNonRoot:
	def test_tracks_gnome_interface_settings(self, monkeypatch):
		monkeypatch.setattr(window.os, "geteuid", lambda: 1000)
		gio = window.Gio
		gio.SettingsSchemaSource.get_default.return_value.list_schemas.return_value = (["org.gnome.desktop.interface"], [])
		settings = MagicMock()
		settings.get_string.side_effect = lambda key: {"color-scheme": "prefer-dark", "gtk-theme": "Yaru-red"}[key]
		settings.get_boolean.return_value = False
		gio.Settings.new.return_value = settings
		gtk_settings = window.gtk.Settings.get_default.return_value
		gtk_settings.reset_mock()
		with patch("window.apply_gtk_theme_name") as apply:
			window.setup_theme()
		gtk_settings.set_property.assert_any_call("gtk-application-prefer-dark-theme", True)
		gtk_settings.set_property.assert_any_call("gtk-enable-animations", False)
		apply.assert_called_once_with("Yaru-red", True)
		settings.connect.assert_called_once()
		# dark inferred from the theme name when no colour scheme
		settings.get_string.side_effect = lambda key: {"color-scheme": "", "gtk-theme": "Yaru-dark"}[key]
		with patch("window.apply_gtk_theme_name") as apply:
			settings.connect.call_args.args[1](settings, "gtk-theme")
		apply.assert_called_once_with("Yaru-dark", True)

	def test_missing_schema_is_skipped(self, monkeypatch):
		monkeypatch.setattr(window.os, "geteuid", lambda: 1000)
		gio = window.Gio
		gio.SettingsSchemaSource.get_default.return_value.list_schemas.return_value = ([], [])
		gio.Settings.new.reset_mock()
		window.setup_theme()
		gio.Settings.new.assert_not_called()

	def test_errors_are_reported_not_raised(self, monkeypatch, capsys):
		monkeypatch.setattr(window.os, "geteuid", lambda: 0)
		monkeypatch.setattr(window, "get_user_theme_preference", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
		window.setup_theme()
		assert "Error setting up theme tracking" in capsys.readouterr().err


class TestLaunchAndExit:
	def test_launch_runs_wizard_when_no_models(self, monkeypatch, tmp_path):
		monkeypatch.setattr(window, "setup_theme", lambda: None)
		monkeypatch.setattr(window.paths_factory, "user_models_dir_path", lambda: str(tmp_path / "none"))
		fake_onboarding = MagicMock()
		fake_onboarding.OnboardingWindow.return_value = MagicMock(completed=False)
		monkeypatch.setitem(sys.modules, "onboarding", fake_onboarding)
		with pytest.raises(SystemExit):
			window._launch()

	def test_launch_opens_settings_when_models_exist(self, monkeypatch, tmp_path):
		monkeypatch.setattr(window, "setup_theme", lambda: None)
		(tmp_path / "alice.dat").write_text("[]")
		monkeypatch.setattr(window.paths_factory, "user_models_dir_path", lambda: str(tmp_path))
		monkeypatch.setattr(sys, "argv", ["ubuntu-hello-gtk"])
		with patch("window.MainWindow") as main:
			assert window._launch() is main.return_value

	def test_launch_forced_wizard_then_settings(self, monkeypatch, tmp_path):
		monkeypatch.setattr(window, "setup_theme", lambda: None)
		monkeypatch.setattr(window.paths_factory, "user_models_dir_path", lambda: str(tmp_path))
		monkeypatch.setattr(sys, "argv", ["ubuntu-hello-gtk", "--force-onboarding"])
		fake_onboarding = MagicMock()
		fake_onboarding.OnboardingWindow.return_value = MagicMock(completed=True)
		monkeypatch.setitem(sys.modules, "onboarding", fake_onboarding)
		with patch("window.MainWindow") as main:
			assert window._launch() is main.return_value

	def test_exit_releases_camera_and_quits(self):
		w = _win()
		w.capture = MagicMock(); w.run_main_loop = True
		with patch("window.gtk4compat.quit_main") as quit_main, pytest.raises(SystemExit):
			w.exit()
		w.capture.release.assert_called_once(); quit_main.assert_called_once()
		w._rebuilding = True
		assert w.exit() is True     # never quit while rebuilding

	def test_session_snapshot_and_rebuild(self, monkeypatch, tmp_path):
		w = _win()
		w.notebook = MagicMock(); w.notebook.get_current_page.return_value = 3
		w.settings_search = MagicMock(); w.settings_search.get_text.return_value = "cam"
		w.active_user = "alice"; w.capture = MagicMock()
		w.window.get_width.return_value = 800; w.window.get_height.return_value = 600
		w.language_combo.get_active_id.return_value = "de"
		snap = w._session_snapshot()
		assert snap["page"] == 3 and snap["search"] == "cam" and snap["width"] == 800 and snap["language"] == "de"
		# write failure: nothing torn down
		monkeypatch.setattr(window.preferences, "write_language", lambda code: (_ for _ in ()).throw(OSError("ro")))
		with patch.object(w, "_build_ui") as build:
			w._apply_language_rebuild("de")
		build.assert_not_called(); assert w._rebuilding is False
		# full rebuild: camera released, catalog reloaded, old window destroyed, UI rebuilt with the snapshot
		monkeypatch.setattr(window.preferences, "write_language", lambda code: None)
		old = w.window
		with patch.object(w, "_build_ui") as build, patch.object(window.i18n, "reload_from_preferences", create=True) as reload:
			w._apply_language_rebuild("de")
		w.capture is None or pytest.fail("camera must be released")
		reload.assert_called_once(); old.destroy.assert_not_called()   # toplevel is kept (mutter crash)
		assert build.call_args.kwargs["restore"]["language"] == "de" and w._rebuilding is False

	def test_language_index_fallbacks(self, monkeypatch):
		w = _win()
		combo = MagicMock(spec=["get_active"]); combo.get_active.return_value = 0
		monkeypatch.setattr(window.preferences, "read_language", lambda: "de")
		monkeypatch.setattr(window.languages, "is_known_language", lambda code: True)
		with patch("window.GLib.timeout_add") as timeout_add:
			w.settle_page_focus = MagicMock(return_value=False)
			w.on_language_changed(combo)                     # index 0 -> auto
		assert timeout_add.call_args.args[2] == window.preferences.AUTO
		combo.get_active.return_value = 1
		with patch("window.GLib.timeout_add") as timeout_add:
			w._rebuilding = False
			w.on_language_changed(combo)                     # index 1 -> first combo code
		assert timeout_add.call_args.args[2] == window.languages.COMBO_CODES[0]
		combo.get_active.return_value = 9999
		w._rebuilding = False
		with patch("window.GLib.timeout_add") as timeout_add:
			w.on_language_changed(combo)                     # out of range -> auto
		assert timeout_add.call_args.args[2] == window.preferences.AUTO
		w._rebuilding = False; w._language_combo_ready = False
		with patch("window.GLib.timeout_add") as timeout_add:
			w.on_language_changed(combo)
		timeout_add.assert_not_called()

	def test_populate_users_prefers_real_user_then_models(self, monkeypatch, tmp_path):
		import pwd
		entries = [MagicMock(pw_uid=1000, pw_name="alice", pw_shell="/bin/bash"),
		           MagicMock(pw_uid=1001, pw_name="bob", pw_shell="/bin/bash"),
		           MagicMock(pw_uid=1002, pw_name="svc", pw_shell="/usr/sbin/nologin"),
		           MagicMock(pw_uid=0, pw_name="root", pw_shell="/bin/bash")]
		monkeypatch.setattr(pwd, "getpwall", lambda: entries)
		(tmp_path / "carol.dat").write_text("[]")
		monkeypatch.setattr(window.paths_factory, "user_models_dir_path", lambda: str(tmp_path))
		w = _win()
		monkeypatch.setattr(window, "get_real_user", lambda: "zed")   # not a listed user
		w._populate_users()
		appended = [c.args[0] for c in w.userlist.append_text.call_args_list]
		assert appended == ["alice", "bob", "carol", "root"] and "svc" not in appended
		w.userlist.set_active.assert_called_with(appended.index("carol"))   # only carol has a model
		w.userlist.reset_mock()
		w._populate_users(preferred_user="bob")
		w.userlist.set_active.assert_called_with(1)
