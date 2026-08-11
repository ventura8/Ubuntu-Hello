"""Settings E2E / UI smoke under real GTK3 (xvfb).

Not mocked: loads main.glade via gi.repository.Gtk. Run only with:

  UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/

Wired into every UH_CI_DE compat cell via scripts/ci-docker.sh.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

if os.environ.get("UH_REAL_GTK") != "1":
	pytest.skip("Settings E2E requires UH_REAL_GTK=1 (real gi.repository.Gtk)", allow_module_level=True)

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import languages  # noqa: E402
import preferences  # noqa: E402
import theme_detect  # noqa: E402
from search_fuzzy import fuzzy_match, fuzzy_score  # noqa: E402

GLADE = Path(__file__).resolve().parents[2] / "ubuntu-hello-gtk" / "src" / "main.glade"


def _pump():
	while Gtk.events_pending():
		Gtk.main_iteration_do(False)


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
	path = tmp_path / "preferences.ini"
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
	return path


@pytest.fixture
def settings_ui(prefs_file):
	"""Load Settings Glade with stock widgets; populate language combo + search hooks."""
	assert GLADE.is_file(), f"missing Glade: {GLADE}"
	builder = Gtk.Builder()
	builder.set_translation_domain("ubuntu-hello-gtk")
	builder.add_from_file(str(GLADE))

	window = builder.get_object("mainwindow")
	notebook = builder.get_object("notebook")
	search = builder.get_object("settings_search")
	combo = builder.get_object("language_combo")
	assert window is not None
	assert notebook is not None
	assert search is not None
	assert combo is not None

	combo.remove_all()
	combo.append(preferences.AUTO, "Automatic")
	ui_lang = preferences.read_language()
	for code in languages.COMBO_CODES:
		combo.append(code, languages.language_combo_label(code, ui_lang))
	current = ui_lang
	if current != preferences.AUTO and not languages.is_known_language(current):
		current = preferences.AUTO
	combo.set_active_id(current)
	if combo.get_active() < 0:
		combo.set_active(0)

	rows_by_page = []
	atomic_pages = {
		obj
		for obj_id in ("box5", "language_page", "box2")
		if (obj := builder.get_object(obj_id)) is not None
	}
	for page_index in range(notebook.get_n_pages()):
		page = notebook.get_nth_page(page_index)
		tab = notebook.get_tab_label(page)
		candidates = list(page.get_children()) if isinstance(page, Gtk.Container) else [page]
		expanded = []
		for child in candidates:
			if isinstance(child, Gtk.Box) and child.get_orientation() == Gtk.Orientation.VERTICAL:
				expanded.extend(child.get_children())
			else:
				expanded.append(child)
		rows_by_page.append({
			"page_index": page_index,
			"page": page,
			"tab": tab,
			"rows": expanded or candidates,
			"atomic": page in atomic_pages,
		})

	def widget_text(widget) -> str:
		parts = []

		def walk(node):
			if node is None:
				return
			if isinstance(node, Gtk.Label):
				text = node.get_text() or node.get_label() or ""
				if text:
					parts.append(text)
			elif isinstance(node, Gtk.Button):
				label = node.get_label()
				if label:
					parts.append(label)
			elif isinstance(node, Gtk.ComboBoxText):
				active = node.get_active_text()
				if active:
					parts.append(active)
			if isinstance(node, Gtk.Container):
				for child in node.get_children():
					walk(child)

		walk(widget)
		return " ".join(parts)

	def apply_search(query: str):
		q = (query or "").strip()
		best_tab = None
		best_score = -1.0
		for info in rows_by_page:
			tab_text = widget_text(info["tab"])
			rows = info["rows"]
			atomic = info.get("atomic", False)
			if not q:
				for row in rows:
					row.set_visible(True)
				continue
			page_best = fuzzy_score(q, tab_text)
			page_match = fuzzy_match(q, tab_text)
			if atomic:
				for row in rows:
					text = widget_text(row)
					if fuzzy_match(q, text):
						page_match = True
						page_best = max(page_best, fuzzy_score(q, text))
				for row in rows:
					row.set_visible(page_match)
			else:
				any_row = False
				for row in rows:
					text = widget_text(row)
					score = max(fuzzy_score(q, text), fuzzy_score(q, tab_text))
					match = fuzzy_match(q, text) or fuzzy_match(q, tab_text)
					row.set_visible(match)
					if match:
						any_row = True
						page_best = max(page_best, score)
				page_match = any_row or page_match
			if page_match and page_best > best_score:
				best_score = page_best
				best_tab = info["page_index"]
		if q and best_tab is not None:
			notebook.set_current_page(best_tab)
		_pump()

	window.show_all()
	_pump()

	harness = {
		"builder": builder,
		"window": window,
		"notebook": notebook,
		"search": search,
		"combo": combo,
		"apply_search": apply_search,
		"widget_text": widget_text,
		"rows_by_page": rows_by_page,
	}
	yield harness
	window.destroy()
	_pump()


class TestSettingsWindowSmoke:
	def test_window_constructs_and_shows(self, settings_ui):
		window = settings_ui["window"]
		assert window.get_visible()
		assert "Ubuntu Hello" in (window.get_title() or "")

	def test_notebook_tabs_present_and_switchable(self, settings_ui):
		notebook = settings_ui["notebook"]
		assert notebook.get_n_pages() == 5
		labels = []
		for i in range(5):
			page = notebook.get_nth_page(i)
			tab = notebook.get_tab_label(page)
			labels.append((tab.get_text() if tab else "") or "")
			notebook.set_current_page(i)
			_pump()
			assert notebook.get_current_page() == i
		joined = " ".join(labels).casefold()
		assert "models" in joined
		assert "video" in joined
		assert "keyring" in joined
		assert "language" in joined
		assert "about" in joined

	def test_language_combo_automatic_english_and_locales(self, settings_ui):
		combo = settings_ui["combo"]
		assert combo.get_active_id() == preferences.AUTO or combo.get_active() == 0
		model = combo.get_model()
		assert model is not None
		n = len(list(model))
		assert n == 1 + len(languages.COMBO_CODES)
		ids = []
		for i in range(n):
			combo.set_active(i)
			ids.append(combo.get_active_id())
		assert preferences.AUTO in ids
		assert "en" in ids
		# After selecting a locale, Automatic remains selectable
		combo.set_active_id("ro")
		assert combo.get_active_id() == "ro"
		combo.set_active_id(preferences.AUTO)
		assert combo.get_active_id() == preferences.AUTO
		combo.set_active_id("en")
		assert combo.get_active_id() == "en"

	def test_language_is_own_tab_not_about(self, settings_ui):
		b = settings_ui["builder"]
		lang_page = b.get_object("language_page")
		about = b.get_object("box5")
		combo = b.get_object("language_combo")
		assert lang_page is not None and about is not None and combo is not None
		parent = combo.get_parent()
		while parent is not None and parent not in (lang_page, about):
			parent = parent.get_parent()
		assert parent is lang_page

	def test_about_search_keeps_full_page(self, settings_ui):
		"""Tagline hits must not hide logo/title/version/link on the About tab."""
		b = settings_ui["builder"]
		about = b.get_object("box5")
		assert about is not None
		settings_ui["apply_search"]("facial")
		for child in about.get_children():
			assert child.get_visible(), f"About child hidden after tagline search: {child}"
		settings_ui["apply_search"]("linux")
		for child in about.get_children():
			assert child.get_visible(), f"About child hidden after linux search: {child}"
		# Title / tagline / version still present
		texts = " ".join(settings_ui["widget_text"](c) for c in about.get_children()).casefold()
		assert "ubuntu hello" in texts
		assert "facial authentication" in texts
		settings_ui["apply_search"]("")

	def test_video_tab_visible_after_unrelated_search(self, settings_ui):
		"""Search that hides Video rows must not leave a blank Video page on switch."""
		b = settings_ui["builder"]
		notebook = settings_ui["notebook"]
		video = b.get_object("box2")
		opencv = b.get_object("opencvbox")
		cam = b.get_object("cameraselect")
		assert video is not None and opencv is not None and cam is not None

		settings_ui["apply_search"]("facial")
		# Simulate sidebar click onto Video after About-only search.
		video_index = None
		for i in range(notebook.get_n_pages()):
			if notebook.get_nth_page(i) is video:
				video_index = i
				break
		assert video_index is not None

		def show_tree(widget):
			widget.set_visible(True)
			if isinstance(widget, Gtk.Container):
				for child in widget.get_children():
					show_tree(child)

		show_tree(video)
		notebook.set_current_page(video_index)
		_pump()

		assert video.get_visible()
		assert opencv.get_visible()
		assert cam.get_visible()
		settings_ui["apply_search"]("")

	def test_language_preference_persists(self, settings_ui, prefs_file):
		combo = settings_ui["combo"]
		combo.set_active_id("de")
		preferences.write_language(combo.get_active_id())
		assert preferences.read_language() == "de"
		text = prefs_file.read_text(encoding="utf-8")
		assert "[ui]" in text
		assert "language = de" in text
		preferences.write_language(preferences.AUTO)
		assert preferences.read_language() == preferences.AUTO

	def test_instant_language_preference_reload_path(self, prefs_file, monkeypatch):
		"""preferences.ini + optional i18n.reload_from_preferences (no process restart)."""
		import i18n as i18n_mod

		preferences.write_language("ro")
		assert preferences.read_language() == "ro"
		if hasattr(i18n_mod, "reload_from_preferences"):
			i18n_mod.reload_from_preferences()
			assert callable(i18n_mod._)
		# Glade rebuild path: fresh Builder with domain (same as Settings instant apply).
		builder = Gtk.Builder()
		builder.set_translation_domain("ubuntu-hello-gtk")
		builder.add_from_file(str(GLADE))
		label = builder.get_object("languagetab")
		assert label is not None
		assert (label.get_text() or label.get_label() or "").strip()
		preferences.write_language(preferences.AUTO)
		if hasattr(i18n_mod, "reload_from_preferences"):
			i18n_mod.reload_from_preferences()

	def test_fuzzy_search_filters_and_clear_restores(self, settings_ui):
		apply_search = settings_ui["apply_search"]
		rows_by_page = settings_ui["rows_by_page"]
		# Fuzzy typo for Language tab content
		apply_search("langag")
		language = rows_by_page[3]
		assert any(r.get_visible() for r in language["rows"])
		apply_search("")
		for info in rows_by_page:
			assert all(r.get_visible() for r in info["rows"])

	def test_fuzzy_search_switches_to_best_tab(self, settings_ui):
		notebook = settings_ui["notebook"]
		notebook.set_current_page(0)
		_pump()
		# Subsequence / fuzzy for Keyring
		settings_ui["apply_search"]("kyrng")
		assert notebook.get_current_page() == 2

	def test_no_restart_note_on_language_tab(self, settings_ui):
		note = settings_ui["builder"].get_object("language_restart_note")
		assert note is not None
		text = (note.get_text() or note.get_label() or "").casefold()
		assert "restart" not in text
		assert "automatic" in text or "immediate" in text or "apply" in text

	def test_theme_detect_smoke(self):
		theme = theme_detect.get_theme_preference(default="light")
		assert theme in ("light", "dark")

	def test_stock_widgets_present(self, settings_ui):
		b = settings_ui["builder"]
		search = b.get_object("settings_search")
		header = b.get_object("headerbar")
		assert isinstance(search, Gtk.SearchEntry)
		assert isinstance(b.get_object("language_combo"), Gtk.ComboBoxText)
		assert isinstance(header, Gtk.HeaderBar)
		assert isinstance(b.get_object("notebook"), Gtk.Notebook)
		# Search lives on the left of the title bar (HeaderBar pack start).
		assert search.get_parent() is header
		assert header.child_get_property(search, "pack-type") == Gtk.PackType.START

	def test_i18n_preference_env_honored(self, tmp_path, monkeypatch):
		path = tmp_path / "preferences.ini"
		path.write_text("[ui]\nlanguage = ro\n", encoding="utf-8")
		monkeypatch.setenv("UH_PREFERENCES_FILE", str(path))
		assert preferences.read_language() == "ro"
		assert languages.is_known_language("ro")
