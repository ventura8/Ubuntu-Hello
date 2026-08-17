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


def test_repo_json_and_po_lint_clean():
    errors = i18n_lint.lint_tree(ROOT)
    assert errors == [], "\n".join(errors[:20])
