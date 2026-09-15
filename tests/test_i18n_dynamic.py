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
