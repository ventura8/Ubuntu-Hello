"""Tests for Settings language combo labels (UI name + native autonym)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GTK_SRC = ROOT / "ubuntu-hello-gtk" / "src"
sys.path.insert(0, str(GTK_SRC))

langs = importlib.import_module("languages")


def test_combo_label_english_ui_shows_native_in_parens():
    assert langs.language_combo_label("de", "en") == "German (Deutsch)"
    assert langs.language_combo_label("ja", "en") == "Japanese (日本語)"
    assert langs.language_combo_label("ro", "en") == "Romanian (Română)"


def test_combo_label_skips_duplicate_when_same_as_native():
    assert langs.language_combo_label("en", "en") == "English"
    assert langs.language_combo_label("de", "de") == "Deutsch"


def test_combo_label_non_english_ui_keeps_native():
    label = langs.language_combo_label("de", "ro")
    assert label.startswith("Germană")
    assert label.endswith("(Deutsch)")
    assert " (" in label


def test_combo_label_unknown_falls_back_to_code_or_english():
    assert langs.language_combo_label("xx", "en") == "xx"


def test_combo_label_falls_back_to_language_names_when_lookup_fails(monkeypatch):
    monkeypatch.setattr(langs, "_babel_display_name", lambda *_a, **_k: None)
    monkeypatch.setattr(langs, "_native_display_name", lambda *_a, **_k: None)
    assert langs.language_combo_label("de", "en") == langs.LANGUAGE_NAMES["de"]
    assert langs.language_combo_label("ja", "ro") == langs.LANGUAGE_NAMES["ja"]


def test_is_rtl_codes():
	import languages
	assert languages.is_rtl("ar") and languages.is_rtl("he") and languages.is_rtl("fa") and languages.is_rtl("ur")
	assert languages.is_rtl("ar_EG.UTF-8") and languages.is_rtl("ps") and languages.is_rtl("sd") and languages.is_rtl("yi")
	assert not languages.is_rtl("en") and not languages.is_rtl("de") and not languages.is_rtl("auto")
	assert not languages.is_rtl("") and not languages.is_rtl(None)
