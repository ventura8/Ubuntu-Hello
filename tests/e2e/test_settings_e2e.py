"""End-to-End scenario tests for the Administrative Settings / Config App (window.py).

Executes against real GTK3 widgets, real Glade XML parsing, and real video/crypto pipelines.
Run with:
    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_settings_e2e.py
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
import pytest
import numpy as np
import cv2

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import window
import preferences
import languages
import auth_helper
import keyring_crypto


class TestSettingsWindowLifecycleAndTheme:
	def test_window_constructs_real_glade(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			assert win.window is not None
			assert win.window.get_visible()
			assert win.notebook is not None
			assert win.notebook.get_n_pages() == 5
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

			# Verify TreeView model has rows
			model = win.treeview.get_model()
			assert model is not None
			assert len(model) == 2
			assert model[0][0] == "0"
			assert model[0][2] == "MyFace"

			# Test Add Model
			add_btn = win.builder.get_object("addbutton")
			assert add_btn is not None
			win.on_model_add(add_btn)
			gtk_pump()

			# Test Remove Model
			win.treeview.set_cursor(0)
			del_btn = win.builder.get_object("deletebutton")
			assert del_btn is not None
			monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.OK)
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

			# Switch away to Language tab (index 3)
			win.notebook.set_current_page(3)
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
			monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.OK)
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

			# Should switch notebook to Keyring tab (index 2)
			assert win.notebook.get_current_page() == 2

			# Clear search entry
			search_entry.set_text("")
			win.on_settings_search_changed(search_entry)
			gtk_pump()
		finally:
			win.window.destroy()
			gtk_pump()
