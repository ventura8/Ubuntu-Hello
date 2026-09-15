"""Instant language switch end-to-end (real gettext catalogs) and right-to-left layout audit.

Run with: UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_language_rtl_e2e.py
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

if os.environ.get("UH_REAL_GTK") != "1":
	pytest.skip("requires UH_REAL_GTK=1", allow_module_level=True)

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

import gtk4compat
import languages
import preferences
import window
import onboarding

ROOT = Path(__file__).resolve().parents[2]
GTK_PO = ROOT / "ubuntu-hello-gtk" / "po"
DOMAIN = "ubuntu-hello-gtk"


def _po_map(code):
	"""msgid -> msgstr (single-line entries are enough for tab labels/titles)."""
	text = (GTK_PO / f"{code}.po").read_text(encoding="utf-8")
	out = {}
	for m in re.finditer(r'^msgid "((?:[^"\\]|\\.)*)"\nmsgstr "((?:[^"\\]|\\.)*)"$', text, re.M):
		if m.group(1) and m.group(2):
			out[m.group(1)] = m.group(2)
	return out


@pytest.fixture
def real_i18n(tmp_path, monkeypatch, isolated_fs):
	"""Real i18n.py (from i18n.py.in) with compiled de/ar catalogs; restores everything after."""
	localedir = tmp_path / "locale"
	for code in ("de", "ar"):
		mo_dir = localedir / code / "LC_MESSAGES"
		mo_dir.mkdir(parents=True)
		subprocess.run(["msgfmt", "-o", str(mo_dir / f"{DOMAIN}.mo"), str(GTK_PO / f"{code}.po")], check=True)
	source = (ROOT / "ubuntu-hello-gtk" / "src" / "i18n.py.in").read_text(encoding="utf-8")
	source = source.replace("@gettext_package@", DOMAIN).replace("@localedir@", str(localedir))
	module = types.ModuleType("i18n")
	module.__file__ = str(tmp_path / "i18n.py")
	monkeypatch.setenv("LANGUAGE", "en")
	monkeypatch.setenv("LANG", "C.UTF-8")
	exec(compile(source, module.__file__, "exec"), module.__dict__)
	monkeypatch.setitem(sys.modules, "i18n", module)
	monkeypatch.setattr(window, "i18n", module)
	# The app modules bound `_` from the test stub at import; point them at the real catalog
	# (in production every module shares the one real i18n module).
	for name in ("window", "onboarding", "tab_video", "tab_keyring", "tab_models", "tab_notifications", "authsticky"):
		mod = sys.modules.get(name)
		if mod is not None and hasattr(mod, "_"):
			monkeypatch.setattr(mod, "_", module._)
	yield module
	preferences.write_language(preferences.AUTO)
	module.reload_from_preferences()
	Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)


def _labels(widget):
	"""All visible Gtk.Label descendants with non-empty text."""
	found = []

	def walk(node):
		if isinstance(node, Gtk.Label) and node.get_visible() and node.get_text().strip():
			found.append(node)
		for child in gtk4compat.iter_children(node):
			walk(child)

	walk(widget)
	return found


def _bounds(widget, relative_to):
	ok, rect = widget.compute_bounds(relative_to)
	assert ok, f"{widget} has no bounds relative to {relative_to}"
	return rect.origin.x, rect.origin.y, rect.size.width, rect.size.height


def _switch(win, code, gtk_pump):
	old_child = win.window.get_child()
	old = win.window
	assert win.language_combo.set_active_id(code)
	gtk_pump(80)
	assert win.window is old and win.window.get_child() is not old_child, "content rebuilt in the same toplevel"
	assert not win._rebuilding
	assert preferences.read_language() == code
	gtk_pump(20)


class TestLanguageSwitchNavigation:
	def test_switch_to_german_then_navigate_everything(self, real_i18n, gtk_pump):
		de = _po_map("de")
		win = window.MainWindow(run_main_loop=False)
		try:
			assert win.builder.get_object("modelstab").get_text() == "Models"
			_switch(win, "de", gtk_pump)

			# Builder-defined labels (tabs, headings) follow the Python gettext catalog
			for tab_id, msgid in (("modelstab", "Models"), ("videotab", "Video"), ("keyringtab", "Keyring"),
			                      ("languagetab", "Language"), ("abouttab", "About")):
				assert win.builder.get_object(tab_id).get_text() == de[msgid], tab_id
			assert win.window.get_title() == de["Ubuntu Hello Configuration"]

			# Navigate every tab: widgets alive, no dropdown popped open, focus settled
			for page in range(win.notebook.get_n_pages()):
				win.notebook.set_current_page(page)
				gtk_pump(30)
				assert win.notebook.get_current_page() == page
				for combo in (win.userlist, win.cameraselect, win.language_combo):
					assert not [c for c in gtk4compat.iter_children(combo.widget) if isinstance(c, Gtk.Popover) and c.get_visible()]
			# Search works on the translated labels
			win.settings_search.set_text(de["Language"][:5])
			gtk_pump(30)
			assert win.notebook.get_nth_page(win.notebook.get_current_page()) is win.builder.get_object("language_page")
			win.settings_search.set_text("")
			gtk_pump(20)

			# And back to Automatic: English again
			_switch(win, preferences.AUTO, gtk_pump)
			assert win.builder.get_object("modelstab").get_text() == "Models"
		finally:
			win.window.destroy()
			gtk_pump()


class TestArabicRightToLeft:
	def _assert_translated(self, root, catalog, where):
		"""Every visible label that has an Arabic translation must show it (no stray English)."""
		for label in _labels(root):
			text = label.get_text().strip()
			assert text not in catalog or catalog[text] == text, f"{where}: untranslated label {text!r}"

	def test_settings_mirrors_and_translates(self, real_i18n, gtk_pump, monkeypatch):
		ar = _po_map("ar")
		win = window.MainWindow(run_main_loop=False)
		try:
			_switch(win, "ar", gtk_pump)
			assert Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL
			assert win.window.get_direction() == Gtk.TextDirection.RTL
			W = win.window.get_width()
			assert W > 0

			# Header bar: the search entry packed at "start" now sits on the right of the title
			search = win.settings_search
			title = win.builder.get_object("headerbar").get_title_widget() or win.builder.get_object("headerbar")
			sx, _, sw, _ = _bounds(search, win.window)
			assert sx + sw / 2 > W / 2, "start-packed search entry must mirror to the right edge"
			assert search.get_direction() == Gtk.TextDirection.RTL

			# Sidebar tabs (tab-pos start) are on the right, page content on the left
			tab = win.builder.get_object("modelstab")
			page = win.builder.get_object("box3")
			tx, _, tw, _ = _bounds(tab, win.window)
			px, _, pw, _ = _bounds(page, win.window)
			assert tx > px + pw / 2, "notebook tabs must mirror to the right side"

			# Translated text everywhere (tabs, headings, hints, buttons)
			for tab_id, msgid in (("modelstab", "Models"), ("videotab", "Video"), ("keyringtab", "Keyring"),
			                      ("languagetab", "Language"), ("abouttab", "About")):
				assert win.builder.get_object(tab_id).get_text() == ar[msgid]
			for page_index in range(win.notebook.get_n_pages()):
				win.notebook.set_current_page(page_index)
				gtk_pump(30)
				self._assert_translated(win.notebook.get_nth_page(page_index), ar, f"settings page {page_index}")

			# Models tab: start-aligned Add/Delete buttons mirror to the right half; in the
			# "Showing saved models for" row the label now sits to the RIGHT of the dropdown
			win.notebook.set_current_page(0)
			gtk_pump(30)
			x, _, w, _ = _bounds(win.builder.get_object("box1"), win.window)
			assert x + w / 2 > W / 2, "box1 (halign start) must sit on the right in RTL"
			lx, _, _, _ = _bounds(win.builder.get_object("userlabel"), win.window)
			dx, _, _, _ = _bounds(win.userlist.widget, win.window)
			assert lx > dx, "row order must be mirrored (label right of its dropdown)"
			# Single-model banner: icon on the right of the text, action button on the left
			monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(
				cmd, 0, stdout="0,2026-08-21 12:00:00,MyFace\n" if "list" in cmd else "ok\n", stderr=""))
			win.load_model_list()
			gtk_pump(40)
			assert win.single_model_infobar.get_reveal_child()
			label = win.builder.get_object("single_model_label")
			button = win.builder.get_object("single_model_button")
			lx, _, lw, _ = _bounds(label, win.window)
			bx, _, bw, _ = _bounds(button, win.window)
			assert bx + bw <= lx + 1, "banner action button must be on the left of its text in RTL"
			assert label.get_xalign() == 0.0  # GTK mirrors xalign 0 to the right edge in RTL
			assert button.get_label() == ar["Add a second model"]

			# Centered content stays centered within its page (the page itself sits left of the mirrored sidebar)
			win.notebook.set_current_page(5)
			gtk_pump(30)
			page = win.builder.get_object("box5").get_parent() or win.builder.get_object("box5")
			px, _, pw, _ = _bounds(page, win.window)
			ax, _, aw, _ = _bounds(win.builder.get_object("label2"), win.window)
			assert abs((ax + aw / 2) - (px + pw / 2)) < pw * 0.15
		finally:
			win.window.destroy()
			gtk_pump()

	def test_wizard_mirrors_and_translates(self, real_i18n, gtk_pump):
		ar = _po_map("ar")
		preferences.write_language("ar")
		real_i18n.reload_from_preferences()
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			gtk_pump(40)
			assert ob.window.get_direction() == Gtk.TextDirection.RTL
			W = ob.window.get_width()
			assert ob.window.get_title() == ar["Welcome to Ubuntu Hello"]
			# Navigation bar: Cancel/Back (start) on the right, Next (end) on the left
			cancel = ob.builder.get_object("cancelbutton")
			nxt = ob.nextbutton
			cx, _, cw, _ = _bounds(cancel, ob.window)
			nx, _, nw, _ = _bounds(nxt, ob.window)
			assert cx > nx + nw, "start/end navigation buttons must swap sides in RTL"
			assert cx + cw / 2 > W / 2 and nx + nw / 2 < W / 2
			assert nxt.get_direction() == Gtk.TextDirection.RTL  # go-next-symbolic flips via the icon theme
			# Every slide's visible text is Arabic where a translation exists
			for i, slide in enumerate(ob.slides):
				for j, s in enumerate(ob.slides):
					s.set_visible(i == j)
				gtk_pump(20)
				self._assert_translated(slide, ar, f"wizard slide {i}")
				for label in _labels(slide):
					assert label.get_direction() == Gtk.TextDirection.RTL
		finally:
			ob.window.destroy()
			gtk_pump()
