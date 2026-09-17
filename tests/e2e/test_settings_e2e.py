"""End-to-End scenario tests for the Administrative Settings / Config App (window.py).

Executes against real GTK 4 widgets, real Builder .ui XML parsing, and real video/crypto pipelines.
Run with:
    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_settings_e2e.py
"""
from __future__ import annotations

import os
import sys
import subprocess
from unittest.mock import patch
from pathlib import Path
import pytest
import numpy as np
import cv2

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk
from gi.repository import Pango, Gdk, GLib

import window
import gtk4compat
import preferences
import languages
import auth_helper
import keyring_crypto



def _page_index(win, tab_id):
	"""Notebook index of the page whose tab label has *tab_id*.

	Page numbers shift whenever a tab is added (Security landed between
	Notifications and Keyring), so never hard-code them.
	"""
	label = win.builder.get_object(tab_id)
	assert label is not None, tab_id
	for index in range(win.notebook.get_n_pages()):
		if win.notebook.get_tab_label(win.notebook.get_nth_page(index)) is label:
			return index
	raise AssertionError("no notebook page for " + tab_id)


class TestSettingsWindowLifecycleAndTheme:
	def test_window_constructs_real_ui(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			assert win.window is not None
			assert win.window.get_visible()
			assert win.notebook is not None
			assert win.notebook.get_n_pages() == 7   # Models, Video, Notifications, Security, Keyring, Language, About
			assert win.settings_search is not None
			assert win.language_combo is not None
			assert win.version_label is not None
			assert win.version_label.get_text()
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsModelsTab:
	def test_models_tab_interactions(self, isolated_fs, monkeypatch, gtk_pump):
		def fake_run(cmd, *a, **k):
			if "list" in cmd:
				return subprocess.CompletedProcess(cmd, 0, stdout="0,2026-08-21 12:00:00,MyFace\n1,2026-08-21 12:05:00,Backup\n", stderr="")
			return subprocess.CompletedProcess(cmd, 0, stdout="Success\n", stderr="")

		monkeypatch.setattr(subprocess, "run", fake_run)

		win = window.MainWindow(run_main_loop=False)
		try:
			assert win.userlist is not None
			win.active_user = "alice"
			win.load_model_list()
			gtk_pump()

			# Verify the model table has rows
			model = win.models
			assert model is not None
			assert len(model) == 2
			assert model[0][0] == "0"
			assert model[0][2] == "MyFace"
			# Two models -> no second-model reminder
			infobar = win.builder.get_object("single_model_infobar")
			assert infobar is not None
			assert not infobar.get_reveal_child()

			# Exactly one model -> reminder revealed with an "Add a second model" action
			monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(
				cmd, 0, stdout="0,2026-08-21 12:00:00,MyFace\n" if "list" in cmd else "Success\n", stderr=""))
			win.load_model_list()
			gtk_pump()
			assert len(win.models) == 1
			assert infobar.get_reveal_child()
			assert "second model" in win.builder.get_object("single_model_label").get_text()
			with patch.object(win, "on_model_add") as add:
				win.builder.get_object("single_model_button").emit("clicked")
				add.assert_called_once()
			monkeypatch.setattr(subprocess, "run", fake_run)
			win.load_model_list()
			gtk_pump()
			assert not infobar.get_reveal_child()

			# Test Add Model
			add_btn = win.builder.get_object("addbutton")
			assert add_btn is not None
			win.on_model_add(add_btn)
			gtk_pump()

			# Test Remove Model
			win.models.select(0)
			del_btn = win.builder.get_object("deletebutton")
			assert del_btn is not None
			monkeypatch.setattr(gtk4compat, "run_dialog", lambda dialog: Gtk.ResponseType.OK)
			win.on_model_delete(del_btn)
			gtk_pump()
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsVideoTab:
	def test_video_tab_preview_lifecycle(self, isolated_fs, real_video_frames, monkeypatch, gtk_pump):
		created_captures = []

		class FakeVideoCapture:
			def __init__(self, *a, **k):
				self.opened = True
				self.released = False
				created_captures.append(self)

			def isOpened(self):
				return self.opened and not self.released

			def read(self):
				return True, real_video_frames["color"].copy()

			def release(self):
				self.released = True

			def get(self, prop):
				return 480 if prop == cv2.CAP_PROP_FRAME_HEIGHT else 640

		monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: FakeVideoCapture())

		win = window.MainWindow(run_main_loop=False)
		try:
			# Switch to Video tab (index 1)
			win.notebook.set_current_page(1)
			gtk_pump(20)

			assert len(created_captures) >= 1
			active_cap = created_captures[-1]
			assert active_cap.isOpened()

			# Switch away to the Language tab
			win.notebook.set_current_page(_page_index(win, "languagetab"))
			gtk_pump(20)

			assert active_cap.released or not getattr(win, "video_loop_active", False)
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsKeyringTab:
	def test_keyring_status_and_enable_disable(self, isolated_fs, monkeypatch, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			win.active_user = "alice"
			win.update_keyring_status()
			gtk_pump()

			# Enable button click
			monkeypatch.setattr(auth_helper, "verify_user_password", lambda u, p: True)

			class FakeDialog:
				def __init__(self, *a, **k):
					self.entry1 = Gtk.Entry()
					self.entry1.set_text("mypassword")
				def run(self):
					return Gtk.ResponseType.OK
				def destroy(self):
					pass

			import tab_keyring
			monkeypatch.setattr(tab_keyring, "KeyringPasswordDialog", FakeDialog)
			monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(["ubuntu-hello"], 0))

			enable_btn = win.builder.get_object("keyring_enable_button")
			assert enable_btn is not None
			win.on_keyring_enable(enable_btn)
			gtk_pump()

			# Disable button click
			monkeypatch.setattr(gtk4compat, "run_dialog", lambda dialog: Gtk.ResponseType.OK)
			disable_btn = win.builder.get_object("keyring_disable_button")
			assert disable_btn is not None
			win.on_keyring_disable(disable_btn)
			gtk_pump()
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsLanguageTabAndInstantRebuild:
	def test_language_switch_and_in_process_rebuild(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			combo = win.language_combo
			assert combo is not None

			# Select German (de)
			combo.set_active_id("de")
			preferences.write_language("de")
			assert preferences.read_language() == "de"

			# Trigger rebuild
			win._build_ui(initial=False, restore={"active_user": "alice", "language": "de", "page": 3})
			gtk_pump()

			# Verify active language in rebuilt combo
			assert win.language_combo.get_active_id() == "de"
			assert win.notebook.get_current_page() == 3

			# Reset to Auto
			preferences.write_language(preferences.AUTO)
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsFuzzySearch:
	def test_search_filters_and_switches_tab(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			search_entry = win.settings_search
			assert search_entry is not None

			# Search for 'Keyring'
			search_entry.set_text("Keyring")
			win.on_settings_search_changed(search_entry)
			gtk_pump()

			# Should switch the notebook to the Keyring tab
			assert win.notebook.get_current_page() == _page_index(win, "keyringtab")

			# Clear search entry
			search_entry.set_text("")
			win.on_settings_search_changed(search_entry)
			gtk_pump()
		finally:
			win.window.destroy()
			gtk_pump()


class TestNotificationsTab:
	def test_switches_reflect_and_write_config(self, isolated_fs, monkeypatch, gtk_pump):
		import configparser
		import paths_factory
		cfg = paths_factory.config_file_path()
		monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
		# Start from: enabled, no sound, no details (with a comment that must survive)
		with open(cfg, "a", encoding="utf-8") as fh:
			fh.write("\n[notifications]\n# keep me\nenabled = true\nsound = false\ndetails = false\n")

		win = window.MainWindow(run_main_loop=False)
		try:
			tab = win.builder.get_object("notificationstab")
			assert tab is not None and tab.get_text() == "Notifications"
			en = win.builder.get_object("notifications_enabled_switch")
			snd = win.builder.get_object("notifications_sound_switch")
			det = win.builder.get_object("notifications_details_switch")
			assert en.get_active() and not snd.get_active() and not det.get_active()
			assert snd.get_sensitive() and det.get_sensitive()

			# Toggle sound on -> written to [notifications] sound, comment preserved
			snd.set_active(True)
			gtk_pump()
			text = open(cfg, encoding="utf-8").read()
			assert "# keep me" in text
			parser = configparser.ConfigParser()
			parser.read(cfg)
			assert parser.getboolean("notifications", "sound") is True
			assert parser.getboolean("notifications", "enabled") is True
			# Other sections untouched
			assert parser.get("core", "certainty") == "3.5"

			# Disabling notifications greys out the dependent switches
			en.set_active(False)
			gtk_pump()
			assert not snd.get_sensitive() and not det.get_sensitive()
			parser.read(cfg)
			assert parser.getboolean("notifications", "enabled") is False
		finally:
			win.window.destroy()
			gtk_pump()

	def test_search_finds_notifications_tab(self, isolated_fs, monkeypatch, gtk_pump):
		monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
		win = window.MainWindow(run_main_loop=False)
		try:
			entry = win.builder.get_object("settings_search")
			entry.set_text("sounds")
			win.on_settings_search_changed(entry)
			gtk_pump()
			assert win.notebook.get_current_page() == 2
			assert win.builder.get_object("notifications_sound_switch_row").get_visible()
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsPageSwitchFocus:
	def test_switching_tabs_does_not_pop_open_dropdowns(self, isolated_fs, gtk_pump):
		"""GtkNotebook focuses the new page's first widget; a GtkDropDown there used to pop its list open."""
		win = window.MainWindow(run_main_loop=False)
		try:
			def popovers_open(combo):
				return [c for c in gtk4compat.iter_children(combo.widget) if isinstance(c, Gtk.Popover) and c.get_visible()]

			# Root cause must be fixed, not masked: no popover may be shown even
			# without the post-switch focus settling (reveal_search_page used to
			# set_visible(True) on the dropdowns' popovers = map a grabbing popup).
			win.settle_page_focus = lambda: False
			for page in (1, 4, 0, 4, 3):
				win.notebook.set_current_page(page)
				for combo in (win.userlist, win.cameraselect, win.language_combo):
					assert popovers_open(combo) == [], f"popover mapped synchronously by switching to page {page}"
				gtk_pump(40)
				for combo in (win.userlist, win.cameraselect, win.language_combo):
					assert popovers_open(combo) == [], f"popover opened by switching to page {page}"
			del win.settle_page_focus
			for page in (1, 4, 0, 4, 3):
				win.notebook.set_current_page(page)
				gtk_pump(40)
				for combo in (win.userlist, win.cameraselect, win.language_combo):
					assert popovers_open(combo) == [], f"dropdown list opened by switching to page {page}"
				assert win.window.get_focus() is None
		finally:
			win.window.destroy()
			gtk_pump()


class TestLanguageDropdownRebuild:
	def test_selecting_language_in_dropdown_rebuilds_without_hanging(self, isolated_fs, gtk_pump):
		"""The rebuild must not run inside the DropDown's own notify::selected handler (GTK 4 deadlock)."""
		win = window.MainWindow(run_main_loop=False)
		try:
			old_window = win.window
			old_child = win.window.get_child()
			dropdown = win.language_combo.widget

			def open_popovers():
				return [c for c in Gtk.Window.list_toplevels() if isinstance(c, Gtk.Popover) and c.get_visible()] + \
				       [c for c in gtk4compat.iter_children(dropdown) if isinstance(c, Gtk.Popover) and c.get_visible()]

			# Like a user: open the list (pointer grab), then pick German
			def open_list(widget):
				# The DropDown's list is its Gtk.Popover child (activate() needs a real pointer under Xvfb)
				for child in gtk4compat.iter_children(widget):
					if isinstance(child, Gtk.Popover):
						child.popup()
						return
				widget.activate()

			# Go to the Language tab so the dropdown is mapped (a popover needs a mapped parent)
			win.notebook.set_current_page(_page_index(win, "languagetab"))
			gtk_pump(40)
			assert dropdown.get_mapped()
			open_list(dropdown)
			gtk_pump(30)
			assert open_popovers(), "opening the dropdown list must show its popover"
			assert win.language_combo.set_active_id("de")   # notify::selected fires with the popover still up
			assert win._rebuilding is True                   # deferred to the main loop
			assert win.window is old_window                  # nothing torn down synchronously
			import time
			for _ in range(40):
				gtk_pump(20)
				if win.window.get_child() is not old_child and not win._rebuilding:
					break
				time.sleep(0.02)
			assert win._rebuilding is False
			assert win.window is old_window                  # SAME toplevel: content rebuilt in place
			assert win.window.get_visible() and win.window.get_child() is not old_child
			assert open_popovers() == [], "no stale (grabbing) popover may survive the rebuild"
			assert win.window.get_focus() is None
			assert win.language_combo.get_active_id() == "de"
			assert preferences.read_language() == "de"
			# The rebuilt window is interactive: switching tabs and opening the list again works
			win.notebook.set_current_page(0)
			gtk_pump(30)
			win.notebook.set_current_page(_page_index(win, "languagetab"))
			gtk_pump(30)
			assert win.language_combo.widget.get_mapped()
			open_list(win.language_combo.widget)
			gtk_pump(60)
			win.settle_page_focus()
			gtk_pump(30)
			assert open_popovers() == []
		finally:
			preferences.write_language(preferences.AUTO)
			win.window.destroy()
			gtk_pump()


def _walk_widgets(widget):
	yield widget
	for child in gtk4compat.iter_children(widget):
		yield from _walk_widgets(child)


class TestDropdownSearch:
	def test_language_list_search_matches_substrings(self, isolated_fs, gtk_pump):
		"""Typing part of a name (not only its first letters) narrows the list."""
		win = window.MainWindow(run_main_loop=False)
		try:
			for combo in (win.userlist, win.cameraselect, win.language_combo):
				assert combo.widget.get_search_match_mode() == Gtk.StringFilterMatchMode.SUBSTRING
			win.notebook.set_current_page(_page_index(win, "languagetab"))
			gtk_pump(40)
			dropdown = win.language_combo.widget
			popover = next(c for c in gtk4compat.iter_children(dropdown) if isinstance(c, Gtk.Popover))
			popover.popup()
			import time
			# The popup surface maps asynchronously, and under a loaded machine the
			# first non-zero width can still be a pre-layout one. Wait for the size
			# the assertions below need rather than for merely "not zero", which is
			# what made this test flaky in the full run but never on its own.
			deadline = time.monotonic() + 15
			while time.monotonic() < deadline:
				gtk_pump(20)
				if popover.get_width() >= 300 and dropdown.get_width() >= 320:
					break
				time.sleep(0.02)

			def find(widget, cls):
				if isinstance(widget, cls):
					return widget
				for child in gtk4compat.iter_children(widget):
					hit = find(child, cls)
					if hit is not None:
						return hit
				return None

			entry = find(popover, Gtk.SearchEntry) or find(popover, Gtk.Text)
			listview = find(popover, Gtk.ListView)
			assert entry is not None and listview is not None
			# The list is wide enough for "Language (Native)" names, rows are single-line (no clipping)
			assert dropdown.get_width() >= 320
			assert popover.get_width() >= 300
			# Rows show the plain label, never the search haystack ("name name-folded id")
			row_widgets = [w for w in _walk_widgets(listview) if isinstance(w, Gtk.Label)]
			row_labels = [w.get_text() for w in row_widgets]
			assert row_labels, "list rows must be rendered"
			assert all(w.get_single_line_mode() and w.get_ellipsize() == Pango.EllipsizeMode.NONE for w in row_widgets)
			# The popup is at least as wide as its widest row: full names visible
			widest = max(w.get_preferred_size()[1].width for w in row_widgets)
			assert popover.get_width() >= widest, (popover.get_width(), widest)
			for text in row_labels[:10]:
				assert text == text.strip() and text.count("(") <= 1 and "  " not in text, text
			# The button shows the selected row through the same factory (GTK keeps a hidden "(None)" placeholder)
			button_labels = [w.get_text() for w in _walk_widgets(dropdown)
			                 if isinstance(w, Gtk.Label) and w.get_ancestor(Gtk.Popover) is None and w.get_mapped()]
			assert win.language_combo.get_active_text() in button_labels, button_labels
			total = listview.get_model().get_n_items()
			assert total > 50
			entry.set_text("eutsch")          # middle of "German (Deutsch)"
			gtk_pump(40)
			shown = listview.get_model().get_n_items()
			assert 0 < shown < total
			labels = [listview.get_model().get_item(i).get_string() for i in range(shown)]
			assert any("Deutsch" in text for text in labels), labels
			# Diacritics-tolerant both ways: "romana" finds "Română", "Română" finds it too
			for query in ("romana", "Română", "ROMÂNĂ"):
				entry.set_text(query)
				gtk_pump(40)
				shown = listview.get_model().get_n_items()
				labels = [listview.get_model().get_item(i).get_string() for i in range(shown)]
				assert any("Română" in text for text in labels), (query, labels)
			entry.set_text("")
			gtk_pump(20)
			assert listview.get_model().get_n_items() == total
			popover.popdown()
			gtk_pump(20)
		finally:
			win.window.destroy()
			gtk_pump()


class TestSettingsSearchBestPractices:
	"""Header search: live, case/accent-insensitive, matches descriptions, Escape clears, Ctrl+F focuses."""

	def _visible_pages(self, win):
		return [i for i in range(win.notebook.get_n_pages()) if win.notebook.get_nth_page(i).get_visible()]

	def test_live_case_and_accent_insensitive_search(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			search = win.settings_search
			assert search.get_placeholder_text()
			# Live: no Enter needed; upper case; lands on the Notifications page
			search.set_text("NOTIFIC")
			gtk_pump(30)
			assert win.notebook.get_nth_page(win.notebook.get_current_page()) is win.builder.get_object("notificationsbox")
			# Matches descriptive text, not only titles (Notifications switch description mentions sounds)
			search.set_text("sound")
			gtk_pump(30)
			assert win.notebook.get_nth_page(win.notebook.get_current_page()) is win.builder.get_object("notificationsbox")
			assert win.builder.get_object("notifications_sound_switch_row").get_visible()
			# Accents/diacritics in the query are ignored
			search.set_text("kéyring")
			gtk_pump(30)
			assert win.notebook.get_nth_page(win.notebook.get_current_page()) is win.builder.get_object("keyringbox")
			# Typo tolerant (fuzzy)
			search.set_text("langauge")
			gtk_pump(30)
			assert win.notebook.get_nth_page(win.notebook.get_current_page()) is win.builder.get_object("language_page")
			# Nonsense: nothing crashes, then clearing restores every page and row
			search.set_text("zzzzqqq")
			gtk_pump(30)
			search.set_text("")
			gtk_pump(30)
			assert self._visible_pages(win) == list(range(win.notebook.get_n_pages()))
			for info in win._search_row_baselines:
				for row in info["rows"]:
					assert row.get_visible()
		finally:
			win.window.destroy()
			gtk_pump()

	def test_escape_clears_and_ctrl_f_focuses(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			search = win.settings_search
			search.set_text("video")
			gtk_pump(30)
			assert search.get_text() == "video"
			search.emit("stop-search")          # Escape in the entry
			gtk_pump(30)
			assert search.get_text() == ""
			assert self._visible_pages(win) == list(range(win.notebook.get_n_pages()))
			win.notebook.set_current_page(2)
			gtk_pump(30)
			assert win.window.get_focus() is None
			assert win.focus_settings_search() is True
			gtk_pump(20)
			focus = win.window.get_focus()
			assert focus is not None and (focus is search or focus.get_ancestor(Gtk.SearchEntry) is search)
		finally:
			win.window.destroy()
			gtk_pump()


class TestSecurityTab:
	"""Settings -> Security: strictness radios and the liveness switch, written to config.ini."""

	def _config(self, tmp_path, monkeypatch, certainty="2.2", liveness="false"):
		path = tmp_path / "config.ini"
		path.write_text(
			"[video]\ncertainty = %s\n\n[rubberstamps]\nenabled = %s\n"
			"stamp_rules =\n\tnod\t5s\tfaildeadly     min_distance=12\n" % (certainty, liveness),
			encoding="utf-8")
		monkeypatch.setattr(window.paths_factory, "config_file_path", lambda: str(path))
		import tab_security
		monkeypatch.setattr(tab_security.paths_factory, "config_file_path", lambda: str(path))
		return path

	def test_tab_reflects_the_config(self, isolated_fs, gtk_pump, tmp_path, monkeypatch):
		self._config(tmp_path, monkeypatch, certainty=str(__import__("tab_security").PRESETS["security_secure"]), liveness="true")
		win = window.MainWindow(run_main_loop=False)
		try:
			assert win.builder.get_object("security_secure").get_active()
			assert win.builder.get_object("security_liveness_switch").get_active()
			# the tab is reachable and titled
			index = _page_index(win, "securitytab")
			win.notebook.set_current_page(index)
			gtk_pump(30)
			assert win.notebook.get_current_page() == index
			assert win.builder.get_object("security_title").get_text()
		finally:
			win.window.destroy()
			gtk_pump()

	def test_choosing_a_preset_writes_its_certainty(self, isolated_fs, gtk_pump, tmp_path, monkeypatch):
		path = self._config(tmp_path, monkeypatch, certainty="3.5")
		win = window.MainWindow(run_main_loop=False)
		try:
			assert win.builder.get_object("security_fast").get_active()
			win.builder.get_object("security_secure").set_active(True)
			gtk_pump(20)
			import tab_security
			text = path.read_text(encoding="utf-8")
			assert "certainty = %s" % tab_security.PRESETS["security_secure"] in text
			# A level is tightened by demanding agreeing frames, not by a lower bar.
			assert "confirmations = %d" % tab_security.CONFIRMATIONS["security_secure"] in text
			win.builder.get_object("security_balanced").set_active(True)
			gtk_pump(20)
			text = path.read_text(encoding="utf-8")
			assert "certainty = %s" % tab_security.PRESETS["security_balanced"] in text
			assert "confirmations = %d" % tab_security.CONFIRMATIONS["security_balanced"] in text
		finally:
			win.window.destroy()
			gtk_pump()

	def test_opening_settings_never_changes_what_is_already_configured(self, isolated_fs, gtk_pump, tmp_path, monkeypatch):
		"""Manual testing found the nod switched off without anyone touching it.

		Populating the tab calls set_active on the radios and the switch, and GTK
		emits the same signals for that as for a real click. If the guard around
		the load ever slips, merely opening Settings rewrites the user's config --
		silently turning the nod off, which is the one setting they opted into.
		"""
		import tab_security
		path = self._config(tmp_path, monkeypatch,
		                    certainty=str(tab_security.PRESETS["security_balanced"]), liveness="true")
		before = path.read_text(encoding="utf-8")
		win = window.MainWindow(run_main_loop=False)
		try:
			gtk_pump(40)
			win.notebook.set_current_page(_page_index(win, "securitytab"))
			gtk_pump(40)
			assert path.read_text(encoding="utf-8") == before
			# and the tab shows what the file actually says
			assert win.builder.get_object("security_liveness_switch").get_active()
			assert win.builder.get_object("security_balanced").get_active()
		finally:
			win.window.destroy()
			gtk_pump()

	def test_the_tab_does_not_disturb_a_hand_edited_certainty(self, isolated_fs, gtk_pump, tmp_path, monkeypatch):
		"""A value between two levels rounds to the nearest radio, but stays in the file."""
		path = self._config(tmp_path, monkeypatch, certainty="4.2", liveness="false")
		win = window.MainWindow(run_main_loop=False)
		try:
			gtk_pump(40)
			assert "certainty = 4.2" in path.read_text(encoding="utf-8")
		finally:
			win.window.destroy()
			gtk_pump()

	def test_liveness_switch_writes_a_fail_closed_rule(self, isolated_fs, gtk_pump, tmp_path, monkeypatch):
		"""Turning the nod on must also replace the shipped fail-open rule, or a user who
		never nods is authenticated anyway."""
		path = self._config(tmp_path, monkeypatch, liveness="false")
		win = window.MainWindow(run_main_loop=False)
		try:
			switch = win.builder.get_object("security_liveness_switch")
			switch.set_active(True)
			gtk_pump(20)
			text = path.read_text(encoding="utf-8")
			assert "enabled = true" in text
			assert "faildeadly" not in text and "failsafe" in text
			assert text.count("nod") == 1
			switch.set_active(False)
			gtk_pump(20)
			assert "enabled = false" in path.read_text(encoding="utf-8")
		finally:
			win.window.destroy()
			gtk_pump()
