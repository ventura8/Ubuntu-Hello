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
| Onboarding wizard | `ubuntu-hello-gtk/src/onboarding.py` (face scan: `ubuntu-hello -y add`; never interactive `input()`) |
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

## Onboarding camera scan (watchdog + resource ownership)

`onboarding.py`'s `scan_cameras_thread()` probes each `/dev/v4l/by-path` device on its own daemon watchdog thread, bounded by module-level `CAMERA_PROBE_TIMEOUT` (default 5s) — `cv2.VideoCapture().read()` has no timeout of its own and cannot be cancelled once called, so a misbehaving driver must not be allowed to block the whole sequential scan.

* Each loop iteration owns a private `state`/`lock` pair, bound as default arguments on the worker closure (not captured by reference) — Python closures over loop variables rebind on every iteration, so without this a worker that finishes late could read or write into a **later** device's state instead of its own.
* `lock` makes the timeout/completion handoff atomic: whichever side — the worker finishing its read, or the main thread's post-join check — first sees `state["status"] == "pending"` owns the capture (consumes or releases it); the other side is guaranteed to see it already changed. This prevents a capture from being silently discarded unreleased (leaking the handle and potentially leaving the real device busy for a later open of the same node) and prevents a late-finishing worker from corrupting a subsequent device's result.
* Tests must exercise the **real** timeout path (a genuinely blocked read via `threading.Event`, with `CAMERA_PROBE_TIMEOUT` patched small) rather than faking `Thread.is_alive()`/`Thread.join()` — mocking `time.sleep` does not stop an `Event.wait()`-based block, but it silently no-ops a `time.sleep(N)`-based fake block since both refer to the same module object. See `tests/test_onboarding.py::test_scan_cameras_thread_hanging_device_times_out` and `::test_scan_cameras_thread_late_timeout_does_not_leak_into_next_device`.

## i18n

* Domain: `ubuntu-hello-gtk`. Call `builder.set_translation_domain("ubuntu-hello-gtk")` **before** `add_from_file`.
* Keep Glade `translatable="yes"`.
* **Automatic** locale by default; optional **Language** tab combo → `~/.config/ubuntu-hello/preferences.ini` (see [i18n skill](../i18n/SKILL.md)). Language applies **instantly** (gettext reload + Glade rebuild; no restart). Fuzzy header search via `search_fuzzy.py`.
* **Search**: `Gtk.SearchEntry` on the **left** of the Settings header bar (`pack_type=start`) filters notebook content by displayed translated labels. **About**, **Language**, and **Video** tabs are atomic (a match shows the whole page). Switching tabs calls `reveal_search_page` so a prior search cannot leave Video blank while the camera still opens.
* Stay **native GTK3** on GNOME/KDE/XFCE/Cinnamon/MATE/Budgie/LXQt — stock widgets, `theme_detect`, no web chrome. Forward display + locale + `XDG_CURRENT_DESKTOP` / `DESKTOP_SESSION` through polkit elevation (`elevate()` in `window.py`).
* Manual UX checklist (beyond CI E2E): see [docs/INSTRUCTIONS.md §2.3](../../../docs/INSTRUCTIONS.md) Native Settings UX checklist.

## Tests

Relevant pytest modules: `tests/test_gtk_tabs.py`, `tests/test_onboarding.py`, `tests/test_authsticky_window.py`, `tests/test_theme_detect.py`, `tests/test_version_display.py`, `tests/test_languages.py`, `tests/e2e/test_settings_smoke.py` (`UH_REAL_GTK=1` + xvfb; every compat DE).
