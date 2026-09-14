"""The GTK i18n module: `_` imported once must follow a later language switch (Video tab showed 'Unknown')."""
import shutil
import subprocess
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
def _module(tmp_path, monkeypatch):
	if shutil.which("msgfmt") is None:
		pytest.skip("gettext tools missing")
	localedir = tmp_path / "locale"
	for code in ("ar", "de"):
		(localedir / code / "LC_MESSAGES").mkdir(parents=True)
		subprocess.run(["msgfmt", "-o", str(localedir / code / "LC_MESSAGES" / "ubuntu-hello-gtk.mo"),
		                str(ROOT / "ubuntu-hello-gtk" / "po" / f"{code}.po")], check=True)
	prefs = tmp_path / "preferences.ini"
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(prefs))
	monkeypatch.setenv("LANG", "C.UTF-8")
	monkeypatch.delenv("LANGUAGE", raising=False)
	source = (ROOT / "ubuntu-hello-gtk" / "src" / "i18n.py.in").read_text(encoding="utf-8")
	source = source.replace("@gettext_package@", "ubuntu-hello-gtk").replace("@localedir@", str(localedir))
	mod = types.ModuleType("i18n_under_test")
	exec(compile(source, str(tmp_path / "i18n.py"), "exec"), mod.__dict__)
	return mod, prefs


def test_underscore_follows_reload(tmp_path, monkeypatch):
	mod, prefs = _module(tmp_path, monkeypatch)
	_ = mod._                       # bound once, like `from i18n import _` in tab_video.py
	ngettext = mod.ngettext
	assert _("Models") == "Models"
	prefs.write_text("[ui]\nlanguage = ar\n", encoding="utf-8")
	mod.reload_from_preferences()
	assert _("Models") == "نماذج"
	assert ngettext("Models", "Models", 2) == "Models"   # no plural entry: falls back to the given plural
	prefs.write_text("[ui]\nlanguage = de\n", encoding="utf-8")
	mod.reload_from_preferences()
	assert _("Models") == "Modelle"
	assert mod.pgettext("Window title", "Ubuntu Hello Configuration")
	prefs.write_text("[ui]\nlanguage = auto\n", encoding="utf-8")
	mod.reload_from_preferences()
	assert _("Models") == "Models"


def test_unknown_language_falls_back_to_auto(tmp_path, monkeypatch):
	mod, prefs = _module(tmp_path, monkeypatch)
	prefs.write_text("[ui]\nlanguage = xx\n", encoding="utf-8")
	assert mod.reload_from_preferences() is not None
	assert mod._("Models") == "Models"


def test_automatic_restores_desktop_locale_even_when_preference_was_set_at_import(tmp_path, monkeypatch):
	"""Manual test 2026-09-16: Arabic → Automatic stayed Arabic (but LTR).

	The launcher imports i18n with the saved preference already "ar", so LANG /
	LANGUAGE are rewritten to Arabic before pkexec; the elevated app then treated
	those as the desktop locale. The originals must be captured before any
	override and Automatic must restore them, all four variables included.
	"""
	localedir = tmp_path / "locale"
	if shutil.which("msgfmt") is None:
		pytest.skip("gettext tools missing")
	(localedir / "ar" / "LC_MESSAGES").mkdir(parents=True)
	subprocess.run(["msgfmt", "-o", str(localedir / "ar" / "LC_MESSAGES" / "ubuntu-hello-gtk.mo"),
	                str(ROOT / "ubuntu-hello-gtk" / "po" / "ar.po")], check=True)
	prefs = tmp_path / "preferences.ini"
	prefs.write_text("[ui]\nlanguage = ar\n", encoding="utf-8")   # saved before the app starts
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(prefs))
	monkeypatch.setenv("LANG", "en_US.UTF-8")
	monkeypatch.setenv("LANGUAGE", "en")
	monkeypatch.setenv("LC_MESSAGES", "en_US.UTF-8")
	monkeypatch.delenv("LC_ALL", raising=False)
	source = (ROOT / "ubuntu-hello-gtk" / "src" / "i18n.py.in").read_text(encoding="utf-8")
	source = source.replace("@gettext_package@", "ubuntu-hello-gtk").replace("@localedir@", str(localedir))
	mod = types.ModuleType("i18n_under_test")
	exec(compile(source, str(tmp_path / "i18n.py"), "exec"), mod.__dict__)
	import os
	# import applied the saved override …
	assert mod._("Models") == "نماذج" and os.environ["LANGUAGE"] == "ar"
	assert mod.effective_language() == "ar"
	# … but the originals are the desktop's, and that is what elevate() must forward
	assert mod.original_locale_env() == {"LANGUAGE": "en", "LC_MESSAGES": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
	prefs.write_text("[ui]\nlanguage = auto\n", encoding="utf-8")
	mod.reload_from_preferences()
	assert mod._("Models") == "Models"
	assert os.environ["LANGUAGE"] == "en" and os.environ["LANG"] == "en_US.UTF-8"
	assert mod.effective_language() == "en"


def test_effective_language_follows_desktop_locale_for_automatic(tmp_path, monkeypatch):
	mod, prefs = _module(tmp_path, monkeypatch)
	assert mod.effective_language() == "en"          # C.UTF-8, no LANGUAGE → English
	# An Arabic desktop: the originals captured at import are Arabic, so Automatic → "ar" (RTL)
	localedir = tmp_path / "locale"
	monkeypatch.setenv("LANGUAGE", "ar")
	monkeypatch.setenv("LANG", "ar_EG.UTF-8")
	source = (ROOT / "ubuntu-hello-gtk" / "src" / "i18n.py.in").read_text(encoding="utf-8")
	source = source.replace("@gettext_package@", "ubuntu-hello-gtk").replace("@localedir@", str(localedir))
	mod2 = types.ModuleType("i18n_arabic_desktop")
	exec(compile(source, str(tmp_path / "i18n2.py"), "exec"), mod2.__dict__)
	assert mod2.effective_language() == "ar"
	prefs.write_text("[ui]\nlanguage = de\n", encoding="utf-8")
	mod2.reload_from_preferences()
	assert mod2.effective_language() == "de"
	prefs.write_text("[ui]\nlanguage = auto\n", encoding="utf-8")
	mod2.reload_from_preferences()
	assert mod2.effective_language() == "ar"          # back to the desktop's Arabic, not English
