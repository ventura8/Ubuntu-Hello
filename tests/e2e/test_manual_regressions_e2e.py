"""End-to-end regressions for every issue found while manually testing the GTK 4 port.

Real GTK 4 under Xvfb (UH_REAL_GTK=1). Each test names the bug it guards.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

if os.environ.get("UH_REAL_GTK") != "1":
	pytest.skip("requires UH_REAL_GTK=1", allow_module_level=True)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf

import gtk4compat
import onboarding
import window


def _bounds(widget, relative_to):
	ok, rect = widget.compute_bounds(relative_to)
	assert ok, f"{widget} not laid out"
	return rect.origin.x, rect.origin.y, rect.size.width, rect.size.height


def _fake_frame(w=640, h=480):
	pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
	pb.fill(0x3366aaff)
	return pb


def _walk(widget):
	yield widget
	for child in gtk4compat.iter_children(widget):
		yield from _walk(child)


class TestWizardLayoutRegressions:
	def test_camera_preview_is_full_size_not_a_thumbnail(self, isolated_fs, gtk_pump):
		"""GtkImage shrank the live preview to icon size; GtkPicture must fill the frame."""
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			for i, s in enumerate(ob.slides):
				s.set_visible(i == 2)
			ob.preview_image = ob.builder.get_object("preview_image")
			ob.current_preview_path = "/dev/fake"
			ob.update_preview_image_widget("/dev/fake", _fake_frame())
			gtk_pump(40)
			assert isinstance(ob.preview_image, Gtk.Picture)
			assert ob.preview_image.get_paintable() is not None
			_, _, w, h = _bounds(ob.preview_image, ob.window)
			assert w >= 380, f"preview rendered {w}x{h}"
			assert h >= 280, f"preview rendered {w}x{h}"
			# Face-model page preview as well
			for i, s in enumerate(ob.slides):
				s.set_visible(i == 4)
			ob.preview_image = ob.slide4_preview_image
			ob.update_preview_image_widget("/dev/fake", _fake_frame())
			gtk_pump(40)
			_, _, w, h = _bounds(ob.slide4_preview_image, ob.window)
			assert w >= 500, f"slide-4 preview rendered {w}x{h}"
			assert h >= 300, f"slide-4 preview rendered {w}x{h}"
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_download_status_text_is_centered(self, isolated_fs, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			for i, s in enumerate(ob.slides):
				s.set_visible(i == 1)
			gtk_pump(40)
			label = ob.builder.get_object("downloadoutputlabel")
			box = ob.builder.get_object("downloadeventbox")
			lx, ly, lw, lh = _bounds(label, ob.window)
			bx, by, bw, bh = _bounds(box, ob.window)
			assert abs((lx + lw / 2) - (bx + bw / 2)) < 4, "download text must be horizontally centered"
			assert abs((ly + lh / 2) - (by + bh / 2)) < bh * 0.2, "download text must be vertically centered"
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_window_does_not_grow_with_long_texts(self, isolated_fs, gtk_pump):
		"""Wrapping labels once requested their full-text width: the last page filled the screen."""
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			gtk_pump(40)
			w0, h0 = ob.window.get_width(), ob.window.get_height()
			for i in range(len(ob.slides)):
				for j, s in enumerate(ob.slides):
					s.set_visible(i == j)
				gtk_pump(40)
				assert ob.window.get_width() <= w0 + 2, f"slide {i} widened the window to {ob.window.get_width()}"
				assert ob.window.get_height() <= h0 + 2, f"slide {i} grew the window to {ob.window.get_height()}"
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_camera_list_selected_row_is_visible_and_list_has_room(self, isolated_fs, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.window.current_slide = 2
			for i, s in enumerate(ob.slides):
				s.set_visible(i == 2)
			rows = [[f"Camera {n}", f"/dev/v4l/by-path/cam-{n}", 5, "yes"] for n in range(8)]
			ob.slide4_device_path = "/dev/v4l/by-path/cam-3"
			ob.loadinglabel = ob.builder.get_object("loadinglabel")
			ob.devicelistbox = ob.builder.get_object("devicelistbox")
			with patch("threading.Thread"):
				ob.update_camera_list_gui(rows)
			gtk_pump(60)
			assert ob.cameras.selected_index() == 3
			_, sy, _, sh = _bounds(ob.scrolled_window, ob.window)
			assert sh >= 150, "device list must keep its minimum height next to the preview"
			listview = ob.cameras.widget
			_, ly, _, lh = _bounds(listview, ob.window)
			assert ly >= sy - 1, "list must not be scrolled under the header"
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_failed_scan_reports_instead_of_freezing(self, isolated_fs, gtk_pump, monkeypatch):
		"""format_secondary_text (gone in GTK 4) raised inside the scan callback: buttons stayed disabled."""
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			class FailingAdd:
				stdout = iter(["Please run this command as root\n"])

				def wait(self):
					return 1
			monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **k: FailingAdd())
			alerts = []
			monkeypatch.setattr(gtk4compat, "alert", lambda parent, heading, body="", **k: alerts.append((heading, body)) or 0)
			monkeypatch.setattr(ob, "exit", lambda *a: True)
			assert ob.run_add() is False
			for _ in range(100):  # add runs on a worker thread; its result lands on the main loop
				gtk_pump(10)
				if alerts:
					break
				time.sleep(0.01)
			assert alerts
			assert alerts[0][0] == "Can't save face model"
			assert "as root" in alerts[0][1]
			assert ob.builder.get_object("scanbutton").get_sensitive()
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_ir_page_and_card_idiom(self, isolated_fs, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			yes = ob.builder.get_object("leieyesbutton")
			no = ob.builder.get_object("leienobutton")
			assert "flashes" in _first_label_text(yes)
			assert "flashes" in _first_label_text(no)
			assert ob.builder.get_object("leiehint") is not None
			# .uh-card only on group containers, never nested on labels/check buttons
			carded = [w for w in _walk(ob.window) if "uh-card" in w.get_css_classes()]
			assert {type(w).__name__ for w in carded} == {"Box"}
			assert len(carded) == 2
		finally:
			ob.window.destroy()
			gtk_pump()


def _first_label_text(widget):
	for w in _walk(widget):
		if isinstance(w, Gtk.Label):
			return w.get_text()
	return widget.get_label() or ""


class TestSettingsRegressions:
	def test_default_size_and_video_preview_fixed(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			gtk_pump(40)
			assert win.window.get_default_size() == (900, 620)
			win.notebook.set_current_page(1)
			gtk_pump(40)
			# tab_video scales frames to MAX_WIDTH/MAX_HEIGHT (300) before showing them
			win.opencvimage.set_paintable(Gdk.Texture.new_for_pixbuf(_fake_frame(300, 225)))
			gtk_pump(40)
			_, _, w, h = _bounds(win.builder.get_object("opencvbox"), win.window)
			# fixed frame (400x300 request; fonts/scale may pad a little) — never a full-height column
			assert 380 <= w <= 480, f"video preview frame {w}x{h}"
			assert 280 <= h <= 360, f"video preview frame {w}x{h}"
			assert h < win.window.get_height() * 0.7
		finally:
			win.window.destroy()
			gtk_pump()

	def test_about_links_open_in_users_browser(self, isolated_fs, gtk_pump, monkeypatch):
		win = window.MainWindow(run_main_loop=False)
		try:
			monkeypatch.setattr(window, "get_real_user", lambda: "alice")
			monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
			link = win.builder.get_object("about_issue_link")
			assert "Ubuntu-Hello/issues" in link.get_label()
			class SyncThread:
				def __init__(self, target, args=(), kwargs=None, daemon=True):
					self.target, self.args = target, args

				def start(self):
					self.target(*self.args)

			with patch("window.threading.Thread", SyncThread), \
			     patch("window.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, \
			     patch("window.subprocess.Popen") as popen:
				assert link.emit("activate-link", "https://github.com/ventura8/Ubuntu-Hello/issues") is True
			args = run.call_args.args[0]     # desktop portal on the user's session bus, as the user
			assert args[:3] == ["sudo", "-u", "alice"]
			assert "org.freedesktop.portal.OpenURI.OpenURI" in args
			assert args[-2] == "https://github.com/ventura8/Ubuntu-Hello/issues"
			assert "WAYLAND_DISPLAY=wayland-0" in args
			popen.assert_not_called()
		finally:
			win.window.destroy()
			gtk_pump()

	def test_all_icons_resolve_in_current_icon_theme(self, isolated_fs, gtk_pump):
		"""Yaru-only icon names showed a broken glyph when running with GTK's default icons."""
		theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
		for make in (lambda: window.MainWindow(run_main_loop=False), lambda: onboarding.OnboardingWindow(run_main_loop=False)):
			ui = make()
			try:
				missing = {w.get_icon_name() for w in _walk(ui.window) if isinstance(w, Gtk.Image) and w.get_icon_name()
				           and w.get_icon_name() != "ubuntu-hello-gtk" and not theme.has_icon(w.get_icon_name())}
				assert missing == set(), missing
			finally:
				ui.window.destroy()
				gtk_pump()

	def test_theme_follows_user_when_elevated(self, isolated_fs, gtk_pump, monkeypatch):
		import theme_detect
		monkeypatch.setattr(window.os, "geteuid", lambda: 0)
		monkeypatch.setattr(window, "get_real_user", lambda: "alice")
		monkeypatch.setattr(window, "get_user_theme_preference", lambda user=None: "dark")
		monkeypatch.setattr(window, "get_user_animations_preference", lambda user=None: True)
		monkeypatch.setattr(theme_detect, "get_gtk_theme_name", lambda user=None, environ=None: "Adwaita")
		monkeypatch.setattr(theme_detect, "get_icon_theme_name", lambda user=None, environ=None, icons_dir="/usr/share/icons": "Adwaita")
		monkeypatch.setattr(theme_detect, "resolve_gtk4_theme", lambda name, dark, themes_dir="/usr/share/themes": "Adwaita-dark" if dark else "Adwaita")
		monkeypatch.setattr(window, "_theme_watcher", None)
		monkeypatch.setattr(theme_detect.ThemeWatcher, "start", lambda self: self)  # no live feed in this test
		settings = Gtk.Settings.get_default()
		props = ("gtk-application-prefer-dark-theme", "gtk-theme-name", "gtk-icon-theme-name")
		original = {p: settings.get_property(p) for p in props}
		try:
			window.setup_theme()
			assert settings.get_property("gtk-application-prefer-dark-theme") is True
			assert settings.get_property("gtk-theme-name") == "Adwaita-dark"
			assert settings.get_property("gtk-icon-theme-name") == "Adwaita"
		finally:
			for p, value in original.items():
				settings.set_property(p, value)

	def test_every_settings_page_has_title_glyph_and_no_nested_cards(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			for i in range(win.notebook.get_n_pages()):
				page = win.notebook.get_nth_page(i)
				titles = [w for w in _walk(page) if "uh-page-title" in w.get_css_classes()]
				assert titles, f"page {i} has no title"
			carded = [w for w in _walk(win.window) if "uh-card" in w.get_css_classes()]
			assert all(isinstance(w, Gtk.Box) for w in carded)
			assert len(carded) == 4
		finally:
			win.window.destroy()
			gtk_pump()


class TestGuidedEnrollmentThroughRealSubprocess:
	"""Manual round 2026-09-16: the wizard must show the guided prompts of a *real* `ubuntu-hello add`
	process (worker thread + pipe + GLib), not a monkeypatched Popen."""

	def test_wizard_follows_prompts_of_a_real_add_process(self, isolated_fs, gtk_pump, monkeypatch, tmp_path):
		bindir = tmp_path / "bin"
		bindir.mkdir()
		fake = bindir / "ubuntu-hello"
		fake.write_text(
			"#!/bin/sh\n"
			"case \"$*\" in *add*) ;; *) exit 0;; esac\n"
			"echo '@guide center'; echo 'Please look straight into the camera'; sleep 0.05\n"
			"echo '@progress 1/13'; echo '@guide left'; sleep 0.05; echo '@progress 3/13'\n"
			"echo '@guide right'; sleep 0.05; echo '@progress 5/13'\n"
			"echo '@guide up'; sleep 0.05; echo '@progress 7/13'\n"
			"echo '@guide down'; sleep 0.05; echo '@progress 9/13'\n"
			"echo 'Captured 9 face samples'; echo 'Scan complete'; exit 0\n"
		)
		fake.chmod(0o755)
		monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))

		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.cameras = gtk4compat.ColumnList(["Camera", "Recommended"])
			ob.cameras.append(["IR Camera", "Yes", "/dev/video0", True])
			ob.cameras.select(0)
			ob.window.current_slide = 3
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 4

			instruction = ob.builder.get_object("slide4_instruction_label")
			scan_btn = ob.builder.get_object("scanbutton")
			prompts, buttons = [], []
			real_guide = ob.on_scan_guide

			def record(key):
				r = real_guide(key)
				prompts.append(instruction.get_text())
				return r
			monkeypatch.setattr(ob, "on_scan_guide", record)
			real_progress = ob.on_scan_progress

			def record_progress(count, total):
				r = real_progress(count, total)
				buttons.append(scan_btn.get_label())
				return r
			monkeypatch.setattr(ob, "on_scan_progress", record_progress)

			ob.on_scanbutton_click(scan_btn)
			for _ in range(400):
				gtk_pump(10)
				if ob.scan_pass == 2:
					break
				time.sleep(0.01)
			assert ob.scan_pass == 2
			assert ob.models_enrolled == 1
			# run_add() shows "Look straight" before the process echoes its own @guide center
			deduped = [p for i, p in enumerate(prompts) if i == 0 or p != prompts[i - 1]]
			assert deduped == ["Look straight at the camera", "Turn your head slightly to the left",
			                   "Turn your head slightly to the right", "Tilt your chin up a little",
			                   "Tilt your chin down a little"]
			assert "Recording… 9 of 13" in buttons
			assert not instruction.get_visible()
			assert scan_btn.get_sensitive()
			assert scan_btn.get_label() == "Scan second model"
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()
