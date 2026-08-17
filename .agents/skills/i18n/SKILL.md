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
  Missing these makes `dh_missing` fail the `deb-package` CI job.

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
3. Before finishing: assert **no** empty `msgstr` and **no** leftover `fuzzy` entries in `ubuntu-hello/po/*.po` and `ubuntu-hello-gtk/po/*.po` (except intentional English source).
4. Keep this in the **same change set** as the string edit — do not defer “translations later”.

Audit helper:

```bash
python3 scripts/i18n-lint.py   # JSON packs + .po (msgfmt, empty/fuzzy msgstr, placeholders)
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
# After editing scripts/i18n_fill_data/<domain>/<lang>.json:
python3 scripts/i18n-fill-translations.py
# Or generate packs then apply:
python3 scripts/i18n_fill_data/generate_all_catalogs.py
```

## Runtime wiring

- Templates: `ubuntu-hello/src/i18n.py.in`, `ubuntu-hello-gtk/src/i18n.py.in` (meson `configure_file`).
- GTK helpers: `preferences.py`, `languages.py`, `search_fuzzy.py`.
- Desktop: `ubuntu-hello-gtk.desktop.in` via `i18n.merge_file`.
- PAM: `setlocale` + `bindtextdomain` + `bind_textdomain_codeset` + `textdomain` in `pam/main.cc`.
