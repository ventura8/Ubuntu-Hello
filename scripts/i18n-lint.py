#!/usr/bin/env python3
"""Lint gettext catalogs (.po / .pot) and JSON translation packs.

Used by the CI lint stage (``python3 scripts/i18n-lint.py``). Checks:

* JSON under the source tree: UTF-8, valid JSON, string-only maps/lists
* Fill ``_keys.json`` files: unique non-empty strings
* Locale JSON / packs: no empty string values
* ``.po``: ``msgfmt --check --check-format``, no fuzzy, no empty ``msgstr``,
  matching printf / ``{}`` / ``{name}`` placeholders
* ``LINGUAS`` ↔ ``po/whisper-languages.txt`` and a ``.po`` per language
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIR_NAMES = {
    ".git",
    ".cache",
    ".pytest_cache",
    "__pycache__",
    "subprojects",
}
SKIP_DIR_PREFIXES = ("build", "obj-")

PRINTF_RE = re.compile(
    r"%(?:[0-9]+\$)?[#0\- +]*(?:[0-9]+|\*)?(?:\.(?:[0-9]+|\*))?[diouxXeEfFgGcrs%]"
)
NAMED_BRACE_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")
POS_BRACE_RE = re.compile(r"(?<!\{)\{\}(?!\})")
PO_ENTRY_RE = re.compile(
    r"(?P<flags>^#,[^\n]*\n)?"
    r"(?:^msgctxt\s+(?P<ctxt>(?:\"(?:[^\"\\]|\\.)*\"\s*)+)\s*)?"
    r"^msgid\s+(?P<id>(?:\"(?:[^\"\\]|\\.)*\"\s*)+)\s*"
    r"^msgstr\s+(?P<str>(?:\"(?:[^\"\\]|\\.)*\"\s*)+)",
    re.M,
)
QUOTED_RE = re.compile(r"\"((?:[^\"\\]|\\.)*)\"")


def skip_path(path: Path) -> bool:
    """Return True when *path* lives under a build/cache directory."""
    for part in path.parts:
        if part in SKIP_DIR_NAMES:
            return True
        if any(part.startswith(prefix) for prefix in SKIP_DIR_PREFIXES):
            return True
    return False


def iter_files(root: Path, suffix: str) -> Iterator[Path]:
    for path in sorted(root.rglob(f"*{suffix}")):
        if skip_path(path.relative_to(root) if path.is_relative_to(root) else path):
            continue
        if path.is_file():
            yield path


def join_quoted(blob: str) -> str:
    parts = QUOTED_RE.findall(blob)
    out: list[str] = []
    for part in parts:
        out.append(bytes(part, "utf-8").decode("unicode_escape"))
    return "".join(out)


def placeholder_signature(text: str) -> tuple[Counter[str], Counter[str], int]:
    return (
        Counter(PRINTF_RE.findall(text)),
        Counter(NAMED_BRACE_RE.findall(text)),
        len(POS_BRACE_RE.findall(text)),
    )


def _walk_strings(obj, loc: str) -> Iterable[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                yield f"{loc}: non-string object key {key!r}"
                continue
            yield from _walk_strings(value, f"{loc}[{key!r}]")
        return
    if isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk_strings(value, f"{loc}[{index}]")
        return
    if not isinstance(obj, str):
        yield f"{loc}: expected string, got {type(obj).__name__}"


def lint_json_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: not valid UTF-8 ({exc})"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON ({exc})"]

    if not isinstance(data, (dict, list)):
        return [f"{path}: JSON root must be an object or array"]

    for problem in _walk_strings(data, str(path)):
        errors.append(problem)

    if path.name == "_keys.json" and isinstance(data, list):
        if any(not item for item in data if isinstance(item, str)):
            errors.append(f"{path}: _keys.json contains an empty string")
        if len(data) != len(set(data)):
            errors.append(f"{path}: _keys.json contains duplicate keys")

    if isinstance(data, dict):
        empty = [key for key, value in data.items() if isinstance(value, str) and value == ""]
        if empty:
            errors.append(f"{path}: {len(empty)} empty string value(s)")
    elif isinstance(data, list) and not path.name.startswith("__"):
        empty_n = sum(1 for item in data if isinstance(item, str) and item == "")
        if empty_n:
            errors.append(f"{path}: {empty_n} empty string value(s)")
    return errors


def lint_po_file(path: Path, msgfmt: str | None) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: not valid UTF-8 ({exc})"]

    if msgfmt:
        result = subprocess.run(
            [msgfmt, "--check", "--check-format", "-o", os.devnull, str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "msgfmt failed").strip()
            errors.append(f"{path}: msgfmt --check failed: {detail}")

    if path.suffix == ".pot":
        return errors

    for match in PO_ENTRY_RE.finditer(text):
        flags = match.group("flags") or ""
        msgid = join_quoted(match.group("id"))
        msgstr = join_quoted(match.group("str"))
        if "fuzzy" in flags:
            errors.append(f"{path}: fuzzy entry for msgid {msgid[:60]!r}")
        if msgid == "":
            continue
        if msgstr == "":
            errors.append(f"{path}: empty msgstr for msgid {msgid[:60]!r}")
            continue
        if placeholder_signature(msgid) != placeholder_signature(msgstr):
            errors.append(
                f"{path}: placeholder mismatch for msgid {msgid[:60]!r}"
            )
    return errors


def lint_linguas(root: Path) -> list[str]:
    errors: list[str] = []
    whisper = root / "po" / "whisper-languages.txt"
    if not whisper.is_file():
        return [f"{whisper}: missing Whisper language list"]
    expected = [line for line in whisper.read_text(encoding="utf-8").splitlines() if line.strip()]
    for domain, podir in (
        ("ubuntu-hello", root / "ubuntu-hello" / "po"),
        ("ubuntu-hello-gtk", root / "ubuntu-hello-gtk" / "po"),
    ):
        linguas_path = podir / "LINGUAS"
        if not linguas_path.is_file():
            errors.append(f"{linguas_path}: missing LINGUAS")
            continue
        got = [line for line in linguas_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if got != expected:
            errors.append(f"{linguas_path}: out of sync with po/whisper-languages.txt")
        po_langs = sorted(p.stem for p in podir.glob("*.po"))
        missing = [lang for lang in expected if lang not in po_langs]
        extra = [lang for lang in po_langs if lang not in expected]
        if missing:
            errors.append(f"{podir}: missing .po for {', '.join(missing[:8])}")
        if extra:
            errors.append(f"{podir}: unexpected .po {', '.join(extra[:8])}")
        pot = podir / f"{domain}.pot"
        if not pot.is_file():
            errors.append(f"{pot}: missing template")
    return errors


def lint_tree(root: Path, msgfmt: str | None = None) -> list[str]:
    if msgfmt is None:
        msgfmt = shutil.which("msgfmt")
    errors: list[str] = []
    json_files = list(iter_files(root, ".json"))
    po_files = list(iter_files(root, ".po"))
    pot_files = list(iter_files(root, ".pot"))
    if not json_files:
        errors.append(f"{root}: no JSON files found")
    if not po_files:
        errors.append(f"{root}: no .po files found")
    for path in json_files:
        errors.extend(lint_json_file(path))
    if po_files and not msgfmt:
        errors.append("msgfmt not found on PATH (install gettext)")
    for path in po_files + pot_files:
        errors.extend(lint_po_file(path, msgfmt if path.suffix == ".po" else None))
    errors.extend(lint_linguas(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    del argv  # reserved for future flags
    errors = lint_tree(ROOT)
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        print(f"i18n-lint: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("i18n-lint: JSON + gettext catalogs OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
