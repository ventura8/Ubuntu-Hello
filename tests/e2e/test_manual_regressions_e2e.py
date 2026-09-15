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
			assert w >= 380 and h >= 280, f"preview rendered {w}x{h}"
			# Face-model page preview as well
			for i, s in enumerate(ob.slides):
				s.set_visible(i == 4)
			ob.preview_image = ob.slide4_preview_image
			ob.update_preview_image_widget("/dev/fake", _fake_frame())
			gtk_pump(40)
			_, _, w, h = _bounds(ob.slide4_preview_image, ob.window)
			assert w >= 500 and h >= 300, f"slide-4 preview rendered {w}x{h}"
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
			monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Please run this command as root"))
			alerts = []
			monkeypatch.setattr(gtk4compat, "alert", lambda parent, heading, body="", **k: alerts.append((heading, body)) or 0)
			monkeypatch.setattr(ob, "exit", lambda *a: True)
			assert ob.run_add() is False
			assert alerts and alerts[0][0] == "Can't save face model" and "as root" in alerts[0][1]
			assert ob.builder.get_object("scanbutton").get_sensitive()
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_ir_page_and_card_idiom(self, isolated_fs, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			yes = ob.builder.get_object("leieyesbutton")
			no = ob.builder.get_object("leienobutton")
			assert "flashes" in _first_label_text(yes) and "flashes" in _first_label_text(no)
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
			assert 380 <= w <= 480 and 280 <= h <= 360, f"video preview frame {w}x{h}"
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
			assert args[:3] == ["sudo", "-u", "alice"] and "org.freedesktop.portal.OpenURI.OpenURI" in args
			assert args[-2] == "https://github.com/ventura8/Ubuntu-Hello/issues" and "WAYLAND_DISPLAY=wayland-0" in args
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
		monkeypatch.setattr(window, "get_user_theme_preference", lambda: "dark")
		monkeypatch.setattr(window, "get_user_animations_preference", lambda: True)
		monkeypatch.setattr(theme_detect, "get_gtk_theme_name", lambda user=None, environ=None: "Adwaita")
		monkeypatch.setattr(theme_detect, "get_icon_theme_name", lambda user=None, environ=None, icons_dir="/usr/share/icons": "Adwaita")
		monkeypatch.setattr(theme_detect, "resolve_gtk4_theme", lambda name, dark, themes_dir="/usr/share/themes": "Adwaita-dark" if dark else "Adwaita")
		settings = Gtk.Settings.get_default()
		window.setup_theme()
		assert settings.get_property("gtk-application-prefer-dark-theme") is True
		assert settings.get_property("gtk-theme-name") == "Adwaita-dark"
		assert settings.get_property("gtk-icon-theme-name") == "Adwaita"
		settings.set_property("gtk-application-prefer-dark-theme", False)
		settings.set_property("gtk-theme-name", "Adwaita")

	def test_every_settings_page_has_title_glyph_and_no_nested_cards(self, isolated_fs, gtk_pump):
		win = window.MainWindow(run_main_loop=False)
		try:
			for i in range(win.notebook.get_n_pages()):
				page = win.notebook.get_nth_page(i)
				titles = [w for w in _walk(page) if "uh-page-title" in w.get_css_classes()]
				assert titles, f"page {i} has no title"
			carded = [w for w in _walk(win.window) if "uh-card" in w.get_css_classes()]
			assert all(isinstance(w, Gtk.Box) for w in carded) and len(carded) == 3
		finally:
			win.window.destroy()
			gtk_pump()
