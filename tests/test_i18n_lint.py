"""Tests for scripts/i18n-lint.py (JSON + gettext catalog lint)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("i18n_lint", ROOT / "scripts" / "i18n-lint.py")
assert _SPEC is not None and _SPEC.loader is not None
i18n_lint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(i18n_lint)


def test_skip_build_and_cache_dirs():
    assert i18n_lint.skip_path(Path("build-ci-lint/foo.json"))
    assert i18n_lint.skip_path(Path("builddir/meson-logs/testlog.json"))
    assert i18n_lint.skip_path(Path(".cache/docker-ci/x.json"))
    assert i18n_lint.skip_path(Path("obj-x86_64-linux-gnu/x.json"))
    assert i18n_lint.skip_path(Path("packaging/snap/parts/ubuntu-hello/install/x.json"))
    assert i18n_lint.skip_path(Path("packaging/snap/stage/usr/share/gdal/x.json"))
    assert i18n_lint.skip_path(Path("artifacts/ci-packaging/deb/x.json"))
    assert i18n_lint.skip_path(Path(".claude/worktrees/festive-meitner/ubuntu-hello/po/de.po"))
    assert not i18n_lint.skip_path(Path("scripts/i18n_fill_data/ubuntu-hello/de.json"))


def test_lint_json_ok_and_invalid(tmp_path):
    good = tmp_path / "ok.json"
    good.write_text('{"a": "b"}\n', encoding="utf-8")
    assert i18n_lint.lint_json_file(good) == []

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    errors = i18n_lint.lint_json_file(bad)
    assert errors and "invalid JSON" in errors[0]


def test_lint_json_rejects_empty_and_non_string(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text('{"a": ""}\n', encoding="utf-8")
    assert any("empty string" in e for e in i18n_lint.lint_json_file(empty))

    number = tmp_path / "num.json"
    number.write_text('{"a": 1}\n', encoding="utf-8")
    assert any("expected string" in e for e in i18n_lint.lint_json_file(number))


def test_lint_keys_json_unique(tmp_path):
    keys = tmp_path / "_keys.json"
    keys.write_text('["a", "a"]\n', encoding="utf-8")
    assert any("duplicate" in e for e in i18n_lint.lint_json_file(keys))


def test_placeholder_signature_ignores_named_order():
    left = i18n_lint.placeholder_signature("hi {user} {id}")
    right = i18n_lint.placeholder_signature("{id} for {user}")
    assert left == right
    assert i18n_lint.placeholder_signature("%s %d") != i18n_lint.placeholder_signature("%s")


def test_lint_po_fuzzy_empty_and_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n_lint.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())

    fuzzy = tmp_path / "fuzzy.po"
    fuzzy.write_text(
        'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        '#, fuzzy\nmsgid "Hello"\nmsgstr "Hallo"\n',
        encoding="utf-8",
    )
    assert any("fuzzy" in e for e in i18n_lint.lint_po_file(fuzzy, "/usr/bin/msgfmt"))

    empty = tmp_path / "empty.po"
    empty.write_text(
        'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr ""\n',
        encoding="utf-8",
    )
    assert any("empty msgstr" in e for e in i18n_lint.lint_po_file(empty, "/usr/bin/msgfmt"))

    mismatch = tmp_path / "mismatch.po"
    mismatch.write_text(
        'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hi %s"\nmsgstr "Hallo"\n',
        encoding="utf-8",
    )
    assert any("placeholder mismatch" in e for e in i18n_lint.lint_po_file(mismatch, "/usr/bin/msgfmt"))


def test_lint_linguas_detects_missing_po(tmp_path):
    (tmp_path / "po").mkdir()
    (tmp_path / "po" / "whisper-languages.txt").write_text("de\nfr\n", encoding="utf-8")
    for domain in ("ubuntu-hello", "ubuntu-hello-gtk"):
        podir = tmp_path / domain / "po"
        podir.mkdir(parents=True)
        (podir / "LINGUAS").write_text("de\n", encoding="utf-8")
        (podir / "de.po").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
        (podir / f"{domain}.pot").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
    errors = i18n_lint.lint_linguas(tmp_path)
    assert any("out of sync" in e for e in errors)
    assert any("missing .po" in e for e in errors)


def test_lint_fill_packs_detects_keys_drift(tmp_path):
    """Synthetic tree: _keys.json missing a pot msgid → completeness error."""
    (tmp_path / "po").mkdir()
    (tmp_path / "po" / "whisper-languages.txt").write_text("de\n", encoding="utf-8")
    for domain in ("ubuntu-hello", "ubuntu-hello-gtk"):
        podir = tmp_path / domain / "po"
        podir.mkdir(parents=True)
        (podir / "LINGUAS").write_text("de\n", encoding="utf-8")
        (podir / f"{domain}.pot").write_text(
            'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr ""\n',
            encoding="utf-8",
        )
        (podir / "de.po").write_text(
            'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            'msgid "Hello"\nmsgstr "Hallo"\n',
            encoding="utf-8",
        )
        pack_dir = tmp_path / "scripts" / "i18n_fill_data" / domain
        pack_dir.mkdir(parents=True)
        (pack_dir / "_keys.json").write_text('["Other"]\n', encoding="utf-8")
        (pack_dir / "de.json").write_text('{"Other": "x"}\n', encoding="utf-8")

    errors = i18n_lint.lint_fill_packs_complete(tmp_path)
    assert any("_keys.json" in e and "missing" in e for e in errors), errors
    assert any("untranslated" in e for e in errors), errors


def test_all_translations_filled():
    """Fail if any committed .po or Whisper fill pack is incomplete vs .pot."""
    errors = i18n_lint.lint_tree(ROOT)
    assert errors == [], "translations incomplete:\n" + "\n".join(errors[:40])


def test_po_parsing_keeps_non_ascii_text_intact():
	"""The entry parser must read a translation as its real characters.

	It used to unescape with the latin-1-based `unicode_escape` codec, so every
	non-ASCII translation came back as mojibake. Nothing complained, because the
	checks that survived only look at ASCII placeholders -- but any check that
	reads the characters of a translation silently saw nothing at all.
	"""
	assert i18n_lint.join_quoted('"Идентификовано лице"') == "Идентификовано лице"
	assert i18n_lint.join_quoted('"日本語"') == "日本語"
	assert i18n_lint.join_quoted(r'"a\nb"') == "a\nb"
	assert i18n_lint.join_quoted(r'"say \"hi\""') == 'say "hi"'
	assert i18n_lint.join_quoted('"one " "two"') == "one two"


def test_text_the_user_must_type_survives_translation(tmp_path):
	"""A localised flag or command name is an instruction nobody can follow.

	Machine translation localised "Usage: keyring [enable|disable|restore [--all]]"
	in 91 of 98 catalogs, so the Czech build told people to run "klicenka povolit",
	and it rewrote Pango markup attribute and all, which stops the tag parsing.
	"""
	assert i18n_lint._literal_tokens("run with --all") == {"--all"}
	# A path subsumes the file name inside it: requiring the whole path is stricter.
	assert i18n_lint._literal_tokens("edit /etc/ubuntu-hello/config.ini") == {
		"/etc/ubuntu-hello/config.ini"}
	assert i18n_lint._literal_tokens("Opening config.ini in {editor}") == {"config.ini"}
	assert i18n_lint._literal_tokens("install 'nano' or 'vi'") == {"'nano'", "'vi'"}
	assert i18n_lint._literal_tokens("no literals here") == set()

	good = tmp_path / "ok.po"
	good.write_text('msgid ""\nmsgstr ""\n\nmsgid "Use the --user flag"\n'
	                'msgstr "Utilisez l\'option --user"\n', encoding="utf-8")
	assert not [e for e in i18n_lint.lint_po_file(good, None) if "translated away" in e]

	bad = tmp_path / "bad.po"
	bad.write_text('msgid ""\nmsgstr ""\n\nmsgid "Use the --user flag"\n'
	               'msgstr "Utilisez l\'option --utilisateur"\n', encoding="utf-8")
	errors = [e for e in i18n_lint.lint_po_file(bad, None) if "translated away" in e]
	assert len(errors) == 1 and "--user" in errors[0], errors

	markup = tmp_path / "markup.po"
	markup.write_text(
		'msgid ""\nmsgstr ""\n\n'
		"msgid \"<span foreground='green'><b>Enabled</b></span>\"\n"
		"msgstr \"<span алгы план = 'яшел'><b>кушылган</b></span>\"\n", encoding="utf-8")
	assert [e for e in i18n_lint.lint_po_file(markup, None) if "translated away" in e], \
		"a rewritten Pango attribute must be caught"


def test_command_text_and_names_the_user_types_survive_translation():
	"""Every class of literal beyond flags and paths, each after a shipped defect.

	The Spanish catalog told people to run "sudo ubuntu-hola agregar" and the Turkish
	one "sudo ubuntu-merhaba ekle"; the CLI help enumerated translated subcommand
	names in 86 catalogs; Ctrl+C became Strg+C, [y/N] became [j/N]; dark_threshold,
	EDITOR and the tpm2-tools install line inside <code> were all localised; and
	"root" came back as a translated common noun.
	"""
	tokens = i18n_lint._literal_tokens
	assert tokens("enroll: sudo ubuntu-hello add") == {"sudo ubuntu-hello add"}
	assert tokens("try 'sudo -E ubuntu-hello config' to keep your EDITOR variable") == {
		"sudo -E ubuntu-hello config", "EDITOR"}
	assert tokens("Run <code>sudo apt install tpm2-tools</code> now") == {
		"<code>sudo apt install tpm2-tools</code>"}
	assert tokens("Press Ctrl+C to cancel") == {"Ctrl+C"}
	assert tokens("Do you want to continue [y/N]: ") == {"[y/N]"}
	assert tokens("check dark_threshold in config") == {"dark_threshold"}
	assert tokens("Face auth sets PAM_AUTHTOK for pam_kwallet5") == {"PAM_AUTHTOK", "pam_kwallet5"}
	# A brace placeholder is the placeholder check's business, not a config key.
	assert tokens("Press {abort_key} to abort") == set()
	assert tokens("Using default label \"%s\" because of -y flag") == {"-y"}
	assert tokens("Please run this command as root:") == {"root"}
	assert tokens("Could not identify non-root system user") == {"root"}
	assert tokens("Missing pyv4l2 module, please run:") == {"pyv4l2"}
	assert tokens("Facial authentication for Linux") == set(), "a display name may be transliterated"
	# Subcommand names count as literals only where the string enumerates them or
	# names one as a command; "add a model" is ordinary prose.
	assert tokens("Optional arguments for the add, disable, remove and set commands.") == {
		"add", "disable", "remove", "set"}
	assert tokens("a recorder which doesn't support the test command yet") == {"test"}
	assert tokens("With keyring restore: restore sealed login passwords") == {"keyring restore"}
	assert tokens("Please add a 0 (enable) or a 1 (disable) as an argument") == set()
	assert tokens("Add a second model") == set()


def test_stale_pack_keys_and_pack_drift_are_rejected(tmp_path):
	"""A key the .pot dropped must leave every language JSON, and the ordered pack
	must mirror the keyed JSON.

	The four br/fo/oc/ba catalogs kept French, Icelandic, Catalan and Tatar text in
	six retired keys nobody re-read; and generate_all_catalogs.py reads packs/ first,
	so a fix made only in the keyed JSON is silently undone by the next regeneration.
	"""
	(tmp_path / "po").mkdir()
	(tmp_path / "po" / "whisper-languages.txt").write_text("de\n", encoding="utf-8")
	for domain in ("ubuntu-hello", "ubuntu-hello-gtk"):
		podir = tmp_path / domain / "po"
		podir.mkdir(parents=True)
		(podir / "LINGUAS").write_text("de\n", encoding="utf-8")
		(podir / f"{domain}.pot").write_text(
			'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr ""\n', encoding="utf-8")
		fill = tmp_path / "scripts" / "i18n_fill_data" / domain
		fill.mkdir(parents=True)
		(fill / "_keys.json").write_text('["Hello"]\n', encoding="utf-8")
		(fill / "de.json").write_text('{"Hello": "Hallo", "Retired": "Alt"}\n', encoding="utf-8")
		pack = tmp_path / "scripts" / "i18n_fill_data" / "packs" / domain
		pack.mkdir(parents=True)
		(pack / "de.json").write_text('["Servus"]\n', encoding="utf-8")

	errors = i18n_lint.lint_fill_packs_complete(tmp_path)
	assert any("stale key" in e and "'Retired'" in e for e in errors), errors
	assert any("differs from the keyed JSON" in e for e in errors), errors

	for domain in ("ubuntu-hello", "ubuntu-hello-gtk"):
		fill = tmp_path / "scripts" / "i18n_fill_data" / domain
		(fill / "de.json").write_text('{"Hello": "Hallo"}\n', encoding="utf-8")
		(tmp_path / "scripts" / "i18n_fill_data" / "packs" / domain / "de.json").write_text(
			'["Hallo"]\n', encoding="utf-8")
	assert i18n_lint.lint_fill_packs_complete(tmp_path) == []


def test_mt_filler_protects_exactly_what_the_lint_requires():
	"""The filler hides every lint literal behind a sentinel and restores it byte for
	byte, longest token first, so the translator can neither localise nor split it.
	"""
	import importlib.util
	data = ROOT / "scripts" / "i18n_fill_data"
	spec = importlib.util.spec_from_file_location("mt_fill_all", data / "mt_fill_all.py")
	mt = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mt)
	for source in (
		"enroll: sudo ubuntu-hello add",
		"Optional arguments for the add, disable, remove and set commands.",
		"Run <code>sudo apt install tpm2-tools</code>. Falling back to caching.",
		"For support please visit\nhttps://github.com/ventura8/ubuntu-hello",
		"Press {abort_key} to abort, {confirm_key} to authorise",
	):
		protected, ph, urls = mt.protect(source)
		for token in i18n_lint._literal_tokens(source):
			if token in source.split("https://", 1)[-1] and "https://" in source:
				continue  # hidden with the URL
			assert token not in protected, (source, token, protected)
		assert mt.unprotect(protected, ph, urls) == source
	protected, ph, _ = mt.protect("enroll: sudo ubuntu-hello add")
	assert ph == ["sudo ubuntu-hello add"], "one marker, not a marker inside a marker"


def test_machine_translation_markers_never_ship(tmp_path):
	"""The MT filler brackets placeholders and product names, then unwraps them.

	A translator that transliterates the bracket text itself defeats the unwrap
	(⟦P0⟧ came back as ⟦П0⟧ in Serbian, ⟦UH⟧ as ⟦呃⟧ in Chinese), and the marker
	then renders literally in the user interface.
	"""
	good = tmp_path / "ok.po"
	good.write_text('msgid ""\nmsgstr ""\n\nmsgid "Identified face as %s"\n'
	                'msgstr "Идентификовано лице као %s"\n', encoding="utf-8")
	assert not [e for e in i18n_lint.lint_po_file(good, None) if "sentinel" in e]

	bad = tmp_path / "bad.po"
	bad.write_text('msgid ""\nmsgstr ""\n\nmsgid "Identified face as %s"\n'
	               'msgstr "Идентификовано лице као ⟦П0⟧ %s"\n', encoding="utf-8")
	errors = [e for e in i18n_lint.lint_po_file(bad, None) if "sentinel" in e]
	assert len(errors) == 1, i18n_lint.lint_po_file(bad, None)


def test_a_vetted_borrowed_word_is_not_reported_as_english(tmp_path, monkeypatch):
	"""Latin-script languages borrow words; identity alone is not proof of a gap."""
	assert i18n_lint._english_left_in("Video", "Video", False, frozenset())
	assert not i18n_lint._english_left_in("Video", "Video", False, frozenset({"Video"}))
	# A non-Latin-script catalog can never honestly leave Latin text in place.
	assert i18n_lint._english_left_in("Video", "Video", True, frozenset({"Video"}))


def test_no_english_left_in_any_catalog():
	"""Switching to a language must not leave English on screen, in any of them.

	Manual testing found the Arabic Settings window showing an English Models
	banner: the catalog had been filled with the English source, which passes the
	"no empty msgstr" check and still ships an English UI. This is the catalog-level
	guard, so it runs in the normal suite rather than needing a real GTK session --
	a rendered-window check would cost an X server per language for the same answer.

	Right-to-left and left-to-right languages alike: a machine translation can fail
	for any of them. Words a language genuinely borrows unchanged are vetted once in
	po/english-is-correct.json.
	"""
	import collections
	cognates = i18n_lint._load_cognates(ROOT)
	per_language = collections.Counter()
	examples = {}
	for path in sorted(ROOT.glob("ubuntu-hello*/po/*.po")):
		allowed = cognates.get("*", set()) | cognates.get(path.stem, set())
		for error in i18n_lint.lint_po_file(path, None, allowed):
			if "English left in" not in error:
				continue
			per_language[path.stem] += 1
			examples.setdefault(path.stem, error.split("msgid ", 1)[1].strip())
	assert not per_language, "English left in %d catalog(s):\n%s" % (
		len(per_language),
		"\n".join("  %s: %d string(s), e.g. %s" % (lang, count, examples[lang])
		           for lang, count in per_language.most_common(15)),
	)


def test_no_english_fallback_sentences_in_machine_translated_packs():
    """Every real sentence must actually be translated in every Google-translatable language.

    The MT filler keeps the English source when a request fails; that passed the
    "no empty msgstr" lint and shipped English on the Arabic Models banner and
    the wizard intro (manual test 2026-09-16). Short technical strings (`sudo`,
    `FPS: %d`, product names) are legitimately identical and are not sentences.
    """
    import json
    import importlib.util
    data = ROOT / "scripts" / "i18n_fill_data"
    spec = importlib.util.spec_from_file_location("mt_fill_all", data / "mt_fill_all.py")
    mt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mt)
    offenders = []
    for domain in ("ubuntu-hello", "ubuntu-hello-gtk"):
        for lang in mt.LINGUAS:
            if mt.GOOGLE.get(lang) is None:
                continue  # no Google target: English fallback is the documented convention
            pack = data / domain / f"{lang}.json"
            if not pack.is_file():
                offenders.append(f"{domain}/{lang}: pack missing")
                continue
            packed = json.loads(pack.read_text(encoding="utf-8"))
            for key, value in packed.items():
                english = key.split("\x04", 1)[-1]
                if len(english.split()) >= 4 and value.strip() == english.strip():
                    offenders.append(f"{domain}/{lang}: {english[:60]!r}")
    assert offenders == [], "English fallbacks in MT packs:\n" + "\n".join(offenders[:40]) + (f"\n… {len(offenders)} total" if len(offenders) > 40 else "")
