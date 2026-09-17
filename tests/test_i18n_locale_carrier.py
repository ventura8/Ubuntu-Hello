"""Language switch must leave the C library in a locale it can actually enter.

GtkBuilder translates every `.ui` string through the C library, and glibc ignores
LANGUAGE when LC_MESSAGES is "C"/"POSIX"/"C.UTF-8". Setting LANG to "<code>.UTF-8"
for a locale the system never generated therefore rendered the whole Settings window
in English (Security and About pages) while Python-side strings stayed translated.
Plain unit tests: no GTK, no display, no compositor.
"""
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
	ROOT / "ubuntu-hello-gtk" / "src" / "i18n.py.in",
	ROOT / "ubuntu-hello" / "src" / "i18n.py.in",
)
GENERATED = ["C", "C.UTF-8", "POSIX", "en_US.utf8", "de_DE.utf8", "ro_RO.utf8"]


def _module(template, tmp_path, monkeypatch):
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(tmp_path / "preferences.ini"))
	source = template.read_text(encoding="utf-8")
	source = source.replace("@gettext_package@", "ubuntu-hello-gtk")
	source = source.replace("@localedir@", str(tmp_path / "locale"))
	mod = types.ModuleType("i18n_under_test")
	exec(compile(source, str(tmp_path / "i18n.py"), "exec"), mod.__dict__)
	mod.__dict__["_generated_locales"] = lambda: list(GENERATED)
	return mod


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[-3])
def test_never_sets_a_locale_the_system_cannot_enter(template, tmp_path, monkeypatch):
	"""The regression: LANG=ar.UTF-8 is not generated on a normal desktop."""
	mod = _module(template, tmp_path, monkeypatch)
	monkeypatch.setenv("LANG", "en_US.UTF-8")
	mod._apply_locale_env("ar")
	import os

	assert os.environ["LANGUAGE"] == "ar", "LANGUAGE selects the catalog"
	assert os.environ["LANG"] != "ar.UTF-8", "would drop the C library back to C"
	assert os.environ["LANG"] in GENERATED + ["en_US.UTF-8"]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[-3])
def test_prefers_a_generated_locale_for_that_language(template, tmp_path, monkeypatch):
	mod = _module(template, tmp_path, monkeypatch)
	monkeypatch.setenv("LANG", "en_US.UTF-8")
	assert mod._message_locale("de") == "de_DE.utf8"
	assert mod._message_locale("ro") == "ro_RO.utf8"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[-3])
def test_replaces_a_c_locale_because_glibc_ignores_language_there(
	template, tmp_path, monkeypatch
):
	mod = _module(template, tmp_path, monkeypatch)
	for value in ("C", "C.UTF-8", "POSIX"):
		monkeypatch.setenv("LANG", value)
		carrier = mod._message_locale("ar")
		assert carrier is not None, f"{value} leaves .ui strings untranslated"
		assert carrier not in ("C", "C.UTF-8", "POSIX")


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[-3])
def test_keeps_a_working_locale_when_the_language_has_none(template, tmp_path, monkeypatch):
	mod = _module(template, tmp_path, monkeypatch)
	monkeypatch.setenv("LANG", "en_US.UTF-8")
	assert mod._message_locale("ar") is None, "keep the working locale; LANGUAGE does the rest"
