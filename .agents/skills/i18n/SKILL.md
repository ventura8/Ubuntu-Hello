---
name: i18n
description: >-
  Maintain Ubuntu Hello gettext catalogs (ubuntu-hello / ubuntu-hello-gtk),
  LINGUAS ↔ Whisper language list, Automatic locale + optional Settings language
  preference with instant in-session apply, fuzzy Settings search, native
  multi-DE Settings, scripts/i18n-update.sh, and scripts/i18n-lint.py. Use when
  adding translatable strings, refreshing .pot/.po, linting JSON/.po catalogs,
  or changing locale / Glade / desktop i18n wiring.
---

# Internationalization (gettext)

## Domains and layout

| Domain | Sources | Catalogs |
|---|---|---|
| `ubuntu-hello` | PAM `S()`, core Python `_()` | `ubuntu-hello/po/` |
| `ubuntu-hello-gtk` | GTK Python `_()`, Glade, desktop | `ubuntu-hello-gtk/po/` |

- Runtime lookup: `$prefix/share/locale/<lang>/LC_MESSAGES/<domain>.mo` (meson `localedir`).
- Canonical language list (98 codes, omit `en`): [`po/whisper-languages.txt`](../../../po/whisper-languages.txt). Each `LINGUAS` must match it.
- Commit `.pot` + `.po` with **filled `msgstr`** for all Whisper languages; build `.mo` only (`*.mo` gitignored).
- Debian packages must ship the `.mo` files via:
  - `debian/ubuntu-hello.install` → `usr/share/locale/*/LC_MESSAGES/ubuntu-hello.mo`
  - `debian/ubuntu-hello-gtk.install` → `usr/share/locale/*/LC_MESSAGES/ubuntu-hello-gtk.mo`
  Missing these makes `dh_missing` fail the `packaging` matrix `deb` cell.

## Automatic default + Settings override

- **Default**: Automatic — follow `LANG` / `LANGUAGE` / `LC_*` via `setlocale(LC_ALL, "")`.
- **Override**: Settings → **Language** tab combo (Automatic always first + English + Whisper codes). Combo **display names** come from Babel/CLDR (`languages.language_combo_label`, packaging **`python3-babel`**), localized to the active UI language, plus the language’s **own (native) name in parentheses** when different (e.g. `Germană (Deutsch)`). Writes `~/.config/ubuntu-hello/preferences.ini`:

```ini
[ui]
language = auto
```

- Python `i18n.py` (CLI / compare / GTK) reads the preference **before** first `_()` / Glade load. Explicit code sets process `LANGUAGE` / `LANG`. Invalid codes → Automatic.
- **PAM does not read** `preferences.ini` (system/session locale only).
- **Instant Settings apply**: Language combo writes prefs, calls `i18n.reload_from_preferences()`, then rebuilds Settings from Glade with a fresh `Gtk.Builder` in-process (preserve user/page/search/geometry; Automatic remains first in the model). No restart. CLI/compare pick up preference on next process start.
- Override path: `UH_PREFERENCES_FILE` (tests) or real-user home when elevated (`SUDO_USER` / `PKEXEC_UID`).

Test:

```bash
# Automatic
LANG=ro_RO.UTF-8 LANGUAGE=ro ubuntu-hello list
# Explicit preference (without changing DE language)
printf '%s\n' '[ui]' 'language = de' > /tmp/uh-prefs.ini
UH_PREFERENCES_FILE=/tmp/uh-prefs.ini LANGUAGE=de ubuntu-hello-gtk
```

## Settings search (fuzzy)

Native `Gtk.SearchEntry` in the Settings header bar filters Models / Video / Keyring / About by **currently displayed (translated)** label text using `search_fuzzy.py` (stdlib `difflib.SequenceMatcher` + casefolded subsequence; no rapidfuzz). Hide non-matches; clear restores visibility; switch to the **best-scoring** tab. Session-only. After language switch, rebuild haystacks and re-apply the active query.

## Native multi-DE Settings (mandatory)

Keep **GTK3 + Glade** stock widgets (`HeaderBar`, `Notebook`, `SearchEntry`, `ComboBoxText`, dialogs). Theme via [`theme_detect.py`](../gtk-ui/SKILL.md). Supported DEs: GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, LXQt (Ubuntu 26.04). No web/Electron rewrite. Polkit elevation must keep forwarding display + locale env. CI compat = build **and** Settings E2E (`UH_REAL_GTK=1` + xvfb) per DE; manual checklist = theme, layout, fuzzy search, instant language, keyboard focus.

## Best practices (mandatory)

- Whole extractable sentences; avoid concatenating translated fragments.
- Prefer printf-style `%s` / `%d` for PAM / shared C gettext strings; translators must reorder.
- Use `ngettext` / `pgettext` when touching strings that need plurals or context (no mass rewrite required).
- Do not mark syslog / debug-only strings.
- Glade: `translatable="yes"`; `builder.set_translation_domain("ubuntu-hello-gtk")` **before** `add_from_file`.
- Import configured `i18n` early in entrypoints (`cli.py`, `compare.py`, GTK `init.py`). GTK exposes `reload_from_preferences()` for mid-session language switch.
- UTF-8: `bind_textdomain_codeset` / Python equivalent.
- Preserve placeholders, markup, and accelerators when filling `msgstr`. Brand **Ubuntu Hello** where branding fits.
- `msgmerge` must not wipe filled `msgstr`. Polkit XML gettext remains out of scope.

## Always update all languages (mandatory)

> [!IMPORTANT]
> Any change to a translatable string is incomplete until **every** Whisper language catalog is updated.

When you add, change, or remove a msgid (Python `_()`, PAM `S()`, Glade, desktop):

1. Run `./scripts/i18n-update.sh` so both domains’ `.pot` refresh and every `.po` is `msgmerge`d; `LINGUAS` must match [`po/whisper-languages.txt`](../../../po/whisper-languages.txt).
2. Fill **all** languages for new/changed strings via `scripts/i18n_fill_data/` + `python3 scripts/i18n-fill-translations.py` (or regenerate packs, then apply).
3. Before finishing: assert **no** empty `msgstr` and **no** leftover `fuzzy` entries in `ubuntu-hello/po/*.po` and `ubuntu-hello-gtk/po/*.po` (except intentional English source). Also keep `scripts/i18n_fill_data/<domain>/_keys.json` and every Whisper language JSON in sync with the domain `.pot` (msgctxt keys use `msgctxt\x04msgid`).
4. Keep this in the **same change set** as the string edit — do not defer “translations later”.

### What `scripts/i18n-lint.py` rejects, and why each rule exists

Every rule below was added after a real defect shipped, so do not relax one to make a
build pass:

- **English left in as a "translation"** (`msgstr` == `msgid`). An empty-`msgstr` check
  alone passes a catalog filled with the English source, which then ships an
  English-only UI. Covers every language and single words too; words a language
  genuinely borrows are vetted once in `po/english-is-correct.json`.
- **Machine-translation sentinel brackets** (`⟦` / `⟧`). `mt_fill_all.py` wraps
  placeholders and product names before translating and unwraps afterwards; a
  translator that transliterates the marker defeats the unwrap and the bracket text
  ships as visible garbage.
- **Literal text the user must type, or a machine reads** — `--flags` and `-y`, paths,
  file names, quoted literals such as `'nano'` or `'device_path'`, `sudo …` command
  lines, `Ctrl+C`, `[y/N]`, `<code>` content, `snake_case` config keys, `EDITOR`-style
  environment variables, the CLI's subcommand names where a string enumerates them
  or names "the `test` command" / `keyring restore`, and the product and module
  names in `VERBATIM_NAMES` (`Ubuntu Hello`, `KWallet`, `dlib`, `ubuntu-hello`,
  `root`, …). A localised command is an instruction nobody can follow — the Spanish
  catalog shipped `sudo ubuntu-hola agregar`, the Turkish `sudo ubuntu-merhaba ekle`,
  86 catalogs enumerated translated subcommand names — and a rewritten Pango
  attribute (`<span алгы план = 'яшел'>`) does not parse at all. Display names a
  language legitimately transliterates (`Linux`, `TPM`) are deliberately not
  literals. `_literal_tokens()` is the single list: `mt_fill_all.py` protects exactly
  those tokens with sentinels, so a string the filler protects is a string the lint
  accepts.
- **Stale pack keys and pack drift.** A language JSON must not carry a key the `.pot`
  dropped (six retired keys were where the br/fo/oc/ba wrong-language text hid,
  unread by anyone), and the ordered `packs/<domain>/<lang>.json` must equal the keyed
  JSON position for position: `generate_all_catalogs.py` reads the pack first, so a fix
  made only in the keyed file is silently undone by the next regeneration.
  `mt_fill_all.py` writes both; a hand edit must too.
- Placeholders, `msgfmt --check`, no fuzzy entries, LINGUAS ↔ whisper list.

`join_quoted` must not use the `unicode_escape` codec: it is latin-1 based, so every
non-ASCII translation comes back as mojibake and any check that looks at the
characters of a translation silently sees nothing.

Machine translation is the default for new msgids, but **verify the result**:
`deep_translator` returns the English source on failure, so a rate-limited run reports
success while filling catalogs with English. Google's free endpoint answers 429 after a
burst of a few hundred requests and stays closed for a while; `translate_batch` is a
per-item loop, not one request. Google has no model at all for `ba`, `bo`, `br`, `fo`,
`oc`; those are hand-written. When the lint reports a literal "translated away",
restore it by hand inside the existing translation (keep the prose, put the token
back where the translated word was) rather than re-running machine translation on
the string — the 2026-09-16 repair of 660 such entries across all 98 catalogs was
done that way.

Audit helper (CI lint + pytest `test_all_translations_filled`):

```bash
python3 scripts/i18n-lint.py   # JSON packs + .po + pack↔pot completeness
python3 scripts/i18n-fill-translations.py --check
pytest tests/test_i18n_lint.py::test_all_translations_filled -q
# Empty msgstr (excluding header) or fuzzy entries → must be zero after a string change
for d in ubuntu-hello ubuntu-hello-gtk; do
  echo "== $d =="
  msgattrib --untranslated --no-obsolete "$d"/po/*.po 2>/dev/null | grep -c '^msgid ' || true
  msgattrib --only-fuzzy --no-obsolete "$d"/po/*.po 2>/dev/null | grep -c '^msgid ' || true
done
```

## Maintainer workflow

```bash
./scripts/i18n-update.sh              # assert LINGUAS, refresh pots, msgmerge (keeps msgstr)
./scripts/i18n-update.sh --sync-linguas  # copy whisper-languages.txt → both LINGUAS
# After editing scripts/i18n_fill_data/<domain>/<lang>.json (mirror the value into
# packs/<domain>/<lang>.json too, or the lint's pack-drift check fails):
python3 scripts/i18n-fill-translations.py
# Or generate packs then apply:
python3 scripts/i18n_fill_data/generate_all_catalogs.py
```

## Runtime wiring

- Templates: `ubuntu-hello/src/i18n.py.in`, `ubuntu-hello-gtk/src/i18n.py.in` (meson `configure_file`).
- GTK helpers: `preferences.py`, `languages.py`, `search_fuzzy.py`.
- Desktop: `ubuntu-hello-gtk.desktop.in` via `i18n.merge_file`.
- PAM: `setlocale` + `bindtextdomain` + `bind_textdomain_codeset` + `textdomain` in `pam/main.cc`.
