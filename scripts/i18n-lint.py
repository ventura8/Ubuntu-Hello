#!/usr/bin/env python3
"""Lint gettext catalogs (.po / .pot) and JSON translation packs.

Used by the CI lint stage (``python3 scripts/i18n-lint.py``). Checks:

* JSON under the source tree: UTF-8, valid JSON, string-only maps/lists
* Fill ``_keys.json`` files: unique non-empty strings
* Locale JSON / packs: no empty string values
* Fill packs ↔ ``.pot``: ``_keys.json`` matches pot keys; every Whisper
  language JSON resolves every pot msgid (msgctxt\\x04msgid or msgid fallback)
  and carries no stale key the ``.pot`` dropped (a retired msgid kept in the
  packs is dead text nobody re-reads, and it was where the wrong-language
  leftovers of the br/fo/oc/ba catalogs hid); ordered ``packs/`` mirror the
  keyed JSON, because ``generate_all_catalogs.py`` prefers the pack and would
  silently revert a hand-written fix made only in the keyed file
* ``.po``: ``msgfmt --check --check-format``, no fuzzy, no empty ``msgstr``,
  matching printf / ``{}`` / ``{name}`` placeholders
* ``.po``: markup tags (``<b>``, ``<span foreground='green'>``, ``<a href=...>``)
  are identical to the source -- attribute names are case-sensitive and a
  translated or transliterated tag does not parse at all
* ``.po``: text the user must type back, or a machine reads, survives
  translation: command-line flags, file names, absolute paths, quoted
  literals, ``sudo ...`` command lines, key chords, ``[y/N]``, config keys,
  environment variables, ``<code>`` content, the CLI's own subcommand names
  when the string enumerates them, and product / module names -- a localised
  ``--all``, ``config.ini`` or ``sudo ubuntu-hola agregar`` is an instruction
  the user cannot follow
* ``.po``: no machine-translation sentinel brackets left in a ``msgstr`` (a
  translator that transliterates the marker defeats the unwrap and the bracket
  text ships as visible garbage)
* ``.po``: no English left in as a "translation" (``msgstr`` identical to
  ``msgid``), for every language, right-to-left and left-to-right alike -- an
  empty-``msgstr`` check alone passes a catalog that was filled with the English
  source, which then ships as an English-only UI in that language. Words a
  language really does borrow are vetted once in
  ``po/english-is-correct.json``
* ``LINGUAS`` ↔ ``po/whisper-languages.txt`` and a ``.po`` per language
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
FILL_DOMAINS = ("ubuntu-hello", "ubuntu-hello-gtk")

SKIP_DIR_NAMES = {
    ".git",
    ".cache",
    # Claude Code keeps its git worktrees under .claude/worktrees/ inside the
    # repository, so a walk from the root would lint another branch's catalogs.
    ".claude",
    ".pytest_cache",
    "__pycache__",
    "subprojects",
    # Snapcraft / packaging build leftovers (bind-mounted into CI; gitignored).
    "parts",
    "stage",
    "prime",
    "overlay",
    ".craft",
    "artifacts",
    "logs",
}
SKIP_DIR_PREFIXES = ("build", "obj-")

# Strings that may legitimately read the same in every language: command names,
# product names and identifiers nobody translates. Keep this list short and exact --
# each entry is a string this lint will never again notice is untranslated.
UNTRANSLATED_ALLOWLIST = frozenset(
    {
        "su",
        "sudo",
        "KWallet",
        "GNOME Keyring",
        "Ubuntu Hello",
        "ID",
        # Frames-per-second overlay: the acronym is used as-is in camera UIs in
        # every language, and spelling it out would not read as a translation.
        "FPS: %d",
    }
)
# scripts/i18n_fill_data/mt_fill_all.py wraps placeholders and product names in
# these brackets before sending a string to the translator, then unwraps them. A
# translator that transliterates the marker ("\u27e6P0\u27e7" -> "\u27e6\u041f0\u27e7", "\u27e6UH\u27e7" -> "\u27e6\u5443\u27e7")
# defeats the unwrap, and the bracket text then ships as visible garbage in the UI.
PLACEHOLDER_SENTINEL_RE = re.compile("[\u27e6\u27e7]")

# Text a user has to type back verbatim: command-line flags, file names, absolute
# paths, and words the source itself quotes as literals. Translating these produces
# instructions that cannot be followed -- machine translation localised
# "Usage: keyring [enable|disable|restore [--all]]" in 91 of 98 catalogs, so the
# Czech build told people to run "klicenka povolit".
LITERAL_TOKEN_RE = re.compile(
    r"(?<![\w-])(--[a-zA-Z][\w-]*"          # --all, --user
    r"|-[a-zA-Z](?![\w-])"                   # -y, -E
    r"|/[a-zA-Z0-9_./-]{3,}"                 # /etc/ubuntu-hello
    r"|[a-zA-Z0-9_-]+\.(?:ini|py|sh|so|dat|conf|json|png|svg)"   # config.ini
    r")"
)
QUOTED_LITERAL_RE = re.compile(r"'([a-z][a-z0-9_-]{1,20})'")
# The same idea, one class at a time. Each pattern was added after machine
# translation shipped the localised form: "sudo ubuntu-hola agregar" (es),
# "sudo ubuntu-merhaba ekle" (tr), "Strg+C" for Ctrl+C, "[j/N]" for [y/N], a
# translated dark_threshold config key, and the tpm2-tools install line rewritten
# inside its <code> tag.
COMMAND_LINE_RE = re.compile(
    r"\bsudo(?: -[a-zA-Z])? (?:apt install [a-z0-9-]+|ubuntu-hello(?: [a-z]+)?)"
)
KEY_CHORD_RE = re.compile(r"\b[Cc]trl\+[A-Za-z]\b")
YES_NO_PROMPT_RE = re.compile(r"\[y/N\]")
CODE_TAG_RE = re.compile(r"<code>[^<]*</code>")
# dark_threshold, device_path, pam_kwallet5 / PAM_AUTHTOK -- but not the {abort_key}
# placeholder, which the placeholder check owns.
SNAKE_CASE_RE = re.compile(
    r"(?<![{\w])(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![\w}])"
)
ENV_VAR_RE = re.compile(r"\b[A-Z]{3,}\b(?= (?:environment )?variable\b)")
# Subcommands of the ubuntu-hello CLI. A help text that enumerates three or more
# of them is telling the user what to type, and so is "the test command" or the
# "keyring restore" action; a lone "add" in prose ("add a model") is not.
CLI_SUBCOMMANDS = (
    "add", "clear", "config", "disable", "keyring", "list", "remove", "snapshot",
    "set", "test", "version",
)
CLI_SUBCOMMAND_RE = re.compile(r"\b(?:%s)\b" % "|".join(CLI_SUBCOMMANDS))
NAMED_SUBCOMMAND_RE = re.compile(r"\b(%s) command\b" % "|".join(CLI_SUBCOMMANDS))
KEYRING_ACTION_RE = re.compile(r"\bkeyring (?:enable|disable|restore)\b")
# Names that stay in Latin script in every language: the product names the machine
# translation filler already protects, plus the modules, tools and account name the
# messages tell the user to install, run or become. Display names a language may
# transliterate (Linux, TPM) are deliberately not here.
VERBATIM_NAMES = (
    "Ubuntu Hello", "Windows Hello", "OpenCV2", "cv2", "KWallet", "GNOME Keyring",
    "tpm2-tools", "AES-256-GCM", "dlib", "ffmpeg", "pyv4l2", "Seahorse",
    "ubuntu-hello", "sudo", "root",
)
VERBATIM_NAME_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(n) for n in VERBATIM_NAMES)
)

# Pango and link markup. The tag itself is machine-read, so it has to survive
# translation byte for byte: attribute names are case-sensitive, and a translated
# or transliterated tag simply fails to parse. Real examples found in the shipped
# catalogs: `<span Foreground='green'>`, `<span voorgrond='green'>`,
# `<spanforeground='green'>` and the Serbian `<и>` for `<i>`.
MARKUP_TAG_RE = re.compile(r"</?[^<>]+>")

LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
NON_LATIN_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

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


def skip_dir_name(name: str) -> bool:
    """True when a directory name must not be descended into."""
    if name in SKIP_DIR_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in SKIP_DIR_PREFIXES)


def skip_path(path: Path) -> bool:
    """Return True when *path* lives under a build/cache directory."""
    return any(skip_dir_name(part) for part in path.parts)


def iter_files(root: Path, suffix: str) -> Iterator[Path]:
    # Prune build/snapcraft trees while walking — rglob+filter still descends
    # into packaging/snap/parts|stage and can stall CI for minutes.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not skip_dir_name(d)]
        for name in filenames:
            if name.endswith(suffix):
                path = Path(dirpath) / name
                if path.is_file():
                    yield path


PO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
PO_ESCAPE_RE = re.compile(r"\\(.)", re.S)


def join_quoted(blob: str) -> str:
    """Concatenate a PO entry's quoted chunks and undo its backslash escapes.

    Not `unicode_escape`: that codec is latin-1 based, so every non-ASCII
    translation came back as mojibake and any check looking at the characters of
    a translation -- rather than at its ASCII placeholders -- silently saw
    nothing. Only the escapes gettext actually writes are undone here.
    """
    parts = QUOTED_RE.findall(blob)
    return "".join(
        PO_ESCAPE_RE.sub(lambda m: PO_ESCAPES.get(m.group(1), m.group(1)), part)
        for part in parts
    )


def _literal_tokens(text: str) -> set[str]:
    """Substrings of *text* a translation must reproduce character for character."""
    tokens = set(LITERAL_TOKEN_RE.findall(text))
    tokens.update("'%s'" % word for word in QUOTED_LITERAL_RE.findall(text))
    for pattern in (COMMAND_LINE_RE, KEY_CHORD_RE, YES_NO_PROMPT_RE, CODE_TAG_RE,
                    SNAKE_CASE_RE, ENV_VAR_RE, KEYRING_ACTION_RE, VERBATIM_NAME_RE):
        tokens.update(pattern.findall(text))
    tokens.update(NAMED_SUBCOMMAND_RE.findall(text))
    enumerated = set(CLI_SUBCOMMAND_RE.findall(text))
    if len(enumerated) >= 3:
        tokens.update(enumerated)
    # A longer literal subsumes what it contains: requiring the whole path
    # /etc/ubuntu-hello/config.ini is stricter than its file name or program name.
    return {t for t in tokens if not any(t != other and t in other for other in tokens)}


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


def _is_non_latin_script(pairs: list[tuple[str, str]]) -> bool:
    """True when this catalog's own translations are mostly not Latin script.

    Derived from the file rather than a hard-coded language list, so a catalog added
    later is classified without touching this lint.
    """
    translated = [msgstr for msgid, msgstr in pairs if msgstr and msgstr != msgid]
    if not translated:
        return False
    non_latin = sum(1 for text in translated if not LATIN_LETTER_RE.search(text))
    return non_latin > len(translated) / 2


def _load_cognates(root: Path) -> dict[str, set[str]]:
    """Per-language strings a human confirmed really are identical in that language.

    Latin-script languages borrow words ("Video" is Video in German), so identity is
    not proof of a gap there. Rather than guess with a word-count heuristic -- which
    let single-word labels like "Security" ship untranslated -- each such case is
    vetted once and recorded in po/english-is-correct.json.
    """
    path = root / "po" / "english-is-correct.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {lang: set(values) for lang, values in data.items() if isinstance(values, list)}


def _english_left_in(msgid: str, msgstr: str, non_latin_script: bool,
                     cognates: set[str] = frozenset()) -> bool:
    """Whether msgstr is the untouched English source rather than a translation.

    For a non-Latin-script language any Latin text is the English source: Arabic does
    not render "Security" as "Security". For a Latin-script language the same is true
    unless the word really is borrowed, which is what *cognates* records.
    """
    if msgid != msgstr or msgid in UNTRANSLATED_ALLOWLIST:
        return False
    if not LATIN_LETTER_RE.search(msgid):
        return False
    if non_latin_script:
        return True
    return msgid.strip() not in cognates


def lint_po_file(path: Path, msgfmt: str | None, cognates: set[str] = frozenset()) -> list[str]:
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

    entries: list[tuple[str, str, str]] = []
    for match in PO_ENTRY_RE.finditer(text):
        flags = match.group("flags") or ""
        msgid = join_quoted(match.group("id"))
        msgstr = join_quoted(match.group("str"))
        entries.append((flags, msgid, msgstr))

    non_latin_script = _is_non_latin_script([(msgid, msgstr) for _, msgid, msgstr in entries])
    for flags, msgid, msgstr in entries:
        if "fuzzy" in flags:
            errors.append(f"{path}: fuzzy entry for msgid {msgid[:60]!r}")
        if msgid == "":
            continue
        if msgstr == "":
            errors.append(f"{path}: empty msgstr for msgid {msgid[:60]!r}")
            continue
        expected_tags = Counter(MARKUP_TAG_RE.findall(msgid))
        if expected_tags and Counter(MARKUP_TAG_RE.findall(msgstr)) != expected_tags:
            errors.append(
                f"{path}: markup tags differ from the source for "
                f"msgid {msgid[:46]!r} -> {msgstr[:46]!r}"
            )
        for token in _literal_tokens(msgid):
            if token not in msgstr:
                errors.append(
                    f"{path}: literal {token!r} was translated away for "
                    f"msgid {msgid[:50]!r} -> {msgstr[:50]!r}"
                )
        if PLACEHOLDER_SENTINEL_RE.search(msgstr):
            errors.append(
                f"{path}: machine-translation sentinel left in the translation for "
                f"msgid {msgid[:60]!r} -> {msgstr[:60]!r}"
            )
        if _english_left_in(msgid, msgstr, non_latin_script, cognates):
            errors.append(
                f"{path}: English left in as the translation for msgid {msgid[:60]!r}"
            )
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


def _load_fill_module() -> Any:
    """Load scripts/i18n-fill-translations.py for shared pot/key helpers."""
    path = ROOT / "scripts" / "i18n-fill-translations.py"
    spec = importlib.util.spec_from_file_location("i18n_fill_translations", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _whisper_languages(root: Path) -> list[str]:
    whisper = root / "po" / "whisper-languages.txt"
    if not whisper.is_file():
        return []
    return [line for line in whisper.read_text(encoding="utf-8").splitlines() if line.strip()]


def lint_fill_packs_complete(root: Path) -> list[str]:
    """Fail when fill packs / _keys.json drift from domain .pot templates.

    Lookup matches ``i18n-fill-translations.py``: try ``msgctxt\\x04msgid``,
    then plain ``msgid``.
    """
    errors: list[str] = []
    try:
        fill = _load_fill_module()
    except Exception as exc:
        return [f"scripts/i18n-fill-translations.py: cannot load ({exc})"]

    expected_langs = _whisper_languages(root)
    if not expected_langs:
        return [f"{root / 'po' / 'whisper-languages.txt'}: missing Whisper language list"]

    fill_root = root / "scripts" / "i18n_fill_data"
    for domain in FILL_DOMAINS:
        pot = root / domain / "po" / f"{domain}.pot"
        if not pot.is_file():
            errors.append(f"{pot}: missing template")
            continue
        entries = fill.parse_pot(pot)
        pot_keys = [fill.key_for(e["msgctxt"], e["msgid"]) for e in entries]
        pot_key_set = set(pot_keys)

        keys_path = fill_root / domain / "_keys.json"
        if not keys_path.is_file():
            errors.append(f"{keys_path}: missing _keys.json")
            continue
        try:
            keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{keys_path}: cannot read ({exc})")
            continue
        if not isinstance(keys_data, list) or not all(isinstance(k, str) for k in keys_data):
            errors.append(f"{keys_path}: must be a JSON array of strings")
            continue
        keys_set = set(keys_data)
        missing_keys = sorted(pot_key_set - keys_set)
        extra_keys = sorted(keys_set - pot_key_set)
        if missing_keys:
            sample = ", ".join(repr(k[:50]) for k in missing_keys[:5])
            errors.append(
                f"{keys_path}: missing {len(missing_keys)} pot key(s) vs {pot.name}: {sample}"
            )
        if extra_keys:
            sample = ", ".join(repr(k[:50]) for k in extra_keys[:5])
            errors.append(
                f"{keys_path}: {len(extra_keys)} key(s) not in {pot.name}: {sample}"
            )

        for lang in expected_langs:
            jpath = fill_root / domain / f"{lang}.json"
            if not jpath.is_file():
                errors.append(f"{jpath}: missing fill pack")
                continue
            try:
                trans = json.loads(jpath.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"{jpath}: cannot read ({exc})")
                continue
            if not isinstance(trans, dict):
                errors.append(f"{jpath}: fill pack must be a JSON object")
                continue
            unresolved = 0
            first_miss: str | None = None
            for entry in entries:
                key = fill.key_for(entry["msgctxt"], entry["msgid"])
                value = trans.get(key, trans.get(entry["msgid"], ""))
                if not isinstance(value, str) or not value:
                    unresolved += 1
                    if first_miss is None:
                        first_miss = key
            if unresolved:
                errors.append(
                    f"{jpath}: {unresolved} untranslated pot msgid(s)"
                    + (f" (e.g. {first_miss[:60]!r})" if first_miss else "")
                )
            stale = sorted(key for key in trans if key not in keys_set)
            if stale:
                sample = ", ".join(repr(k[:50]) for k in stale[:3])
                errors.append(
                    f"{jpath}: {len(stale)} stale key(s) not in _keys.json: {sample}"
                )
            errors.extend(_lint_ordered_pack(fill_root, domain, lang, keys_data, trans))
    return errors


def _lint_ordered_pack(fill_root: Path, domain: str, lang: str,
                       keys: list[str], trans: dict[str, Any]) -> list[str]:
    """The ordered pack must equal the keyed JSON, position for position.

    ``generate_all_catalogs.py`` reads ``packs/<domain>/<lang>.json`` first and
    only falls back to the keyed file when no pack exists, so a fix made in the
    keyed JSON alone is silently reverted on the next regeneration.
    """
    pack_path = fill_root / "packs" / domain / f"{lang}.json"
    if not pack_path.is_file():
        return []
    try:
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{pack_path}: cannot read ({exc})"]
    if not isinstance(pack, list):
        return [f"{pack_path}: ordered pack must be a JSON array"]
    if len(pack) != len(keys):
        return [f"{pack_path}: {len(pack)} entries, _keys.json has {len(keys)}"]
    drift = [i for i, key in enumerate(keys) if pack[i] != trans.get(key)]
    if drift:
        return [
            f"{pack_path}: differs from the keyed JSON at {len(drift)} position(s)"
            f" (first: {keys[drift[0]][:50]!r})"
        ]
    return []


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
    all_cognates = _load_cognates(root)
    for path in po_files + pot_files:
        cognates = all_cognates.get("*", set()) | all_cognates.get(path.stem, set())
        errors.extend(lint_po_file(path, msgfmt if path.suffix == ".po" else None, cognates))
    errors.extend(lint_linguas(root))
    errors.extend(lint_fill_packs_complete(root))
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
