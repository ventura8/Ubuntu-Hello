---
name: gtk-ui
description: >-
  Edit Ubuntu Hello GTK UI: Glade layouts, window/onboarding/authsticky, and
  multi-DE theme_detect.py. Use when changing overlays, settings, or onboarding.
---

# GTK GUI & Glade Layouts

PyGObject (GTK 3) + Glade:

| Area | Path |
|---|---|
| Settings tabs | Models, Video, Keyring, **Language**, About (`main.glade`) |
| Language combo | Automatic (always) + English + Whisper locales; Babel/CLDR UI name + native name in `()`; instant apply via rebuild |
| Main settings window | `ubuntu-hello-gtk/src/window.py` (+ `tab_models.py`, `tab_video.py`, `tab_keyring.py`) |
| Onboarding wizard | `ubuntu-hello-gtk/src/onboarding.py` |
| Version label | `ubuntu-hello-gtk/src/version_display.py` (Settings + wizard; `v`+VERSION, optional `-dev`; never older `git describe` tags) |
| GTK entrypoint | `ubuntu-hello-gtk/src/init.py` (adds `/usr/lib/ubuntu-hello` to `sys.path` so `wallet_backend` imports work) |
| Auth overlay | `ubuntu-hello-gtk/src/authsticky.py` |
| Theme helper | `ubuntu-hello-gtk/src/theme_detect.py` |

## Theme support

Overlays and settings must honor system dark/light preferences across Ubuntu-family desktops:

* Shared helper: `theme_detect.py` (used by `window.py` and `authsticky.py`)
* Detects via `XDG_CURRENT_DESKTOP` / `DESKTOP_SESSION` for **GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, LXQt**
* DE-specific probes (`gsettings`/`dconf`, `kreadconfig6`/`kreadconfig5`/`kdeglobals`, `xfconf-query`, LXQt config); missing tools/schemas fall back to light
* Prefer `theme_detect` over reading only `Gtk.Settings` `gtk-theme-name` when adding theme-aware UI

## IPC (overlay)

`compare.py` writes `M=<text>` / `S=<text>` lines to GTK stdin. Preserve that protocol when changing `authsticky.py`.

## i18n

* Domain: `ubuntu-hello-gtk`. Call `builder.set_translation_domain("ubuntu-hello-gtk")` **before** `add_from_file`.
* Keep Glade `translatable="yes"`.
* **Automatic** locale by default; optional **Language** tab combo → `~/.config/ubuntu-hello/preferences.ini` (see [i18n skill](../i18n/SKILL.md)). Language applies **instantly** (gettext reload + Glade rebuild; no restart). Fuzzy header search via `search_fuzzy.py`.
* **Search**: `Gtk.SearchEntry` on the **left** of the Settings header bar (`pack_type=start`) filters notebook content by displayed translated labels. **About**, **Language**, and **Video** tabs are atomic (a match shows the whole page). Switching tabs calls `reveal_search_page` so a prior search cannot leave Video blank while the camera still opens.
* Stay **native GTK3** on GNOME/KDE/XFCE/Cinnamon/MATE/Budgie/LXQt — stock widgets, `theme_detect`, no web chrome. Forward display + locale + `XDG_CURRENT_DESKTOP` / `DESKTOP_SESSION` through polkit elevation (`elevate()` in `window.py`).
* Manual UX checklist (beyond CI E2E): see [docs/INSTRUCTIONS.md §2.3](../../../docs/INSTRUCTIONS.md) Native Settings UX checklist.

## Tests

Relevant pytest modules: `tests/test_gtk_tabs.py`, `tests/test_onboarding.py`, `tests/test_authsticky_window.py`, `tests/test_theme_detect.py`, `tests/test_version_display.py`, `tests/test_languages.py`, `tests/e2e/test_settings_smoke.py` (`UH_REAL_GTK=1` + xvfb; every compat DE).
