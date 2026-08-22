# Ubuntu Hello — AI Agent Workspace Guidelines

System-level guidelines, architectural principles, coding standards, and safety rules for AI coding agents working in the **Ubuntu Hello** codebase.

Related docs:

- [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) — setup, build, extend, debug
- [docs/architecture/README.md](docs/architecture/README.md) — component deep dive
- [docs/SECURITY.md](docs/SECURITY.md) — threat model and mitigations
- [`.agents/skills/`](.agents/skills/) — focused runbooks (`SKILL.md` per workflow)

Thin tool adapters (do not duplicate this file): [CLAUDE.md](CLAUDE.md), [GEMINI.md](GEMINI.md), [`.github/copilot-instructions.md`](.github/copilot-instructions.md), [`.cursor/rules/ubuntu-hello-agents.mdc`](.cursor/rules/ubuntu-hello-agents.mdc).

---

## 1. Project DNA & Context

* **What is Ubuntu Hello?**
  Ubuntu Hello is a Windows Hello™-style facial authentication system for Linux. It integrates with PAM (Pluggable Authentication Module) to authorize users for `sudo`, screen unlocks, login managers (e.g., GDM), `su`, and graphical authentication requests (via Polkit).
* **Historical Context**: The project was rebranded from *Howdy*. Ensure all references, variables, packages, and system files use the prefix/name `ubuntu-hello` or `ubuntu_hello`. Do **not** use the old name.
* **Target OS**: Fixed current version is **Ubuntu 26.04 (resolute)**. Compatible with other modern Debian-based and Arch-based Linux distributions, but agents must treat 26.04/resolute as the project baseline (do not mix older codenames such as Plucky into docs or CI).
* **Supported desktops**: GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, LXQt. Face auth via `common-auth` is DE-agnostic; login wallet auto-unlock depends on PAM consumers of `PAM_AUTHTOK` (primarily `pam_gnome_keyring` and `pam_kwallet5`).
* **Core Goal**: Secure, reliable, and smooth facial authentication.

---

## 2. High-Level Component Architecture

AI agents must understand the relationships and communication channels between the project's core modules:

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant PAM as PAM Service (sudo/gdm)
    participant C++ as pam_ubuntu_hello.so (C++)
    participant Python as compare.py (Python Engine)
    participant GTK as ubuntu-hello-gtk (Overlay UI)
    participant Cam as Video Capture Device

    User->>PAM: Invokes Auth Command (e.g., sudo)
    PAM->>C++: pam_sm_authenticate()
    activate C++
        Note over C++: Check lid state, SSH,<br/>config, and face-skip marker
        C++->>Python: Spawn compare.py in new process group (posix_spawnp)
        activate Python
        Python->>GTK: Spawn ubuntu-hello-gtk --start-auth-ui
        activate GTK
        GTK->>User: Display "Starting up..." overlay
        Python->>Cam: Open camera (OpenCV / FFmpeg / pyv4l2)
        activate Cam
        Cam-->>Python: Grab video frames
        deactivate Cam
        Note over Python: Face Detection &<br/>ResNet Recognition
        Python->>GTK: Write IPC status via stdin (e.g., "M=Identifying...")
        GTK->>User: Display "Identifying you..."
        
        rect rgb(240, 240, 240)
        Note over Python: If Match Succeeded & Rubberstamps Enabled
        Python->>Python: Run Rubberstamps (e.g., nod liveness check)
        Python->>GTK: Write rubberstamp prompt (e.g., "M=Nod to confirm")
        GTK->>User: Show rubberstamp confirmation request
        end

        Python-->>C++: Exit status (0 = success, other = error)
        Note over Python: cleanup() releases camera<br/>and terminates GTK (also on SIGTERM)
        deactivate Python
        deactivate GTK
        
        alt Match Succeeded (Exit 0)
        C++-->>PAM: Return PAM_SUCCESS (clear face-skip)
        PAM-->>User: Grant authentication
        else Match Failed or Timed Out
        Note over C++: Lock/screensaver: set face-skip marker;<br/>password workaround if enabled
        C++-->>PAM: Return PAM_AUTH_ERR / IGNORE
        PAM-->>User: Fallback to Password Prompt
        else Password wins first (cancel; workaround only)
        C++->>Python: SIGTERM process group (then SIGKILL if needed)
        end
        deactivate C++
```

### Lifecycle notes (agents must preserve)

* **compare.py owns GTK**: The overlay is a child of `compare.py`, not of the PAM module. On cancel, PAM signals the **compare process group** (`kill(-pid, SIGTERM)` → wait → `SIGKILL`) so the overlay is not orphaned.
* **SIGTERM cleanup**: `compare.py` registers `SIGTERM`/`SIGINT` handlers that `release()` the camera and terminate/wait GTK, then `os._exit(12)`. Default SIGTERM does **not** run `atexit`. `compare.py` also sets `PR_SET_PDEATHSIG` so orphans die if the PAM worker exits.
* **Skip-after-failure**: On legacy lock PAM services matching `*screensaver*`, a completed non-zero compare exit sets `/run/ubuntu-hello/face-skip/<user>`; the next identify returns `PAM_AUTHINFO_UNAVAIL` (no camera). Cleared on face success and in `pam_sm_setcred`. **GNOME lock uses `gdm-password` (same as login) — skip is not applied there**, so Esc→Enter can retry face. Config: `core.skip_face_after_failure` (default `true`). Does not apply to `sudo`/polkit.

> [!CAUTION]
> **HARD RULE — greeter `pam_get_authtok` / `workaround=off` (do not regress)**
>
> Concurrent password watching is **only** allowed when `core.workaround` is `input` or `native`:
>
> ```text
> ask_pass = ask_auth_tok && (workaround != Workaround::Off);
> ```
>
> **Forbidden** (locks users out of GDM):
> - `ask_pass = … || is_greeter_service(…)` when `workaround=off`
> - Installing a custom `PAM_CONV` wrapper on greeters to “defer camera until password prompt”
> - Calling `pam_get_authtok` on `gdm-password` / login greeters solely to detect Esc
>
> **Symptom if violated:** user picks an account on the GDM user-selection screen and is immediately thrown back (login screen never sticks).
>
> Esc→lock-shield camera leftovers are **not** an excuse to break greeter login. Keep `workaround=off` sequential on greeters; use process-group SIGTERM + `PR_SET_PDEATHSIG` for cleanup when a workaround *does* enable cancel.

### IPC Protocol (Python Engine to GTK UI)

The python matching engine (`compare.py`) writes commands to the GTK overlay's standard input:

* `M=<text>`: Updates main prompt text (e.g. `M=Identifying...`).
* `S=<text>`: Updates subtext.

### Localization (gettext)

* **Default Automatic**: UI language follows `LANG` / `LANGUAGE` / `LC_*` (via `setlocale` + gettext) unless the user picks an explicit language in Settings.
* **Optional Settings override**: Settings → **Language** tab combo (Automatic always first + English + Whisper codes). Combo labels use **Babel/CLDR** names in the active UI language (`python3-babel`), with each language’s **native name in parentheses** when it differs (e.g. `German (Deutsch)`). Persists to `~/.config/ubuntu-hello/preferences.ini` (`[ui] language=auto|<code>`). Python `i18n` (CLI / GTK / compare) reads this before UI load; **PAM stays on the system/session locale** and does not read the preference file. Language changes apply **instantly** (reinstall gettext + rebuild Glade UI in-process; no restart). CLI/compare pick up the preference on next process start. No `--lang` and no `config.ini` language key.
* **Settings search**: native `Gtk.SearchEntry` on the **left** of the header bar with **fuzzy** match (`difflib.SequenceMatcher` + casefolded subsequence via `search_fuzzy.py`; no extra deps) over currently displayed (translated) labels; hide non-matches; clear restores all; switches to the best-scoring tab. **About** / **Language** / **Video** are whole-page matches; tab switches reveal the page so Video cannot stay blank after an unrelated search. Rebuild haystacks after language switch.
* **Native multi-DE Settings**: `ubuntu-hello-gtk` stays **GTK3 + Glade** with stock widgets and `theme_detect.py` on **GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, LXQt** (Ubuntu 26.04). Do not rewrite as web/Electron. CI compat = package/build **and** Settings E2E (`UH_REAL_GTK=1` + xvfb) in every `UH_CI_DE` cell; also smoke-check theme/layout/search/language/polkit on those DEs.
* Domains: `ubuntu-hello` (PAM + core Python), `ubuntu-hello-gtk` (GTK Python + Glade + desktop). Catalogs install under `$prefix/share/locale/<lang>/LC_MESSAGES/`.
* Language set: [`po/whisper-languages.txt`](po/whisper-languages.txt) (Whisper codes, omit `en`); keep each `LINGUAS` in sync via [`scripts/i18n-update.sh`](scripts/i18n-update.sh).
* **Always update translations (mandatory)**: whenever you add, change, or remove a **translatable** string (`_()`, `S()`, Glade `translatable="yes"`, desktop `Name`/`Comment`/`Keywords`), you must refresh **both** domains and fill **every** Whisper language in the **same change set**. Do not leave empty `msgstr`, fuzzy leftovers, or English-only gaps. Workflow: `./scripts/i18n-update.sh` → update/fill packs under `scripts/i18n_fill_data/` as needed → `python3 scripts/i18n-fill-translations.py` → confirm no missing translations (see [`.agents/skills/i18n/SKILL.md`](.agents/skills/i18n/SKILL.md)).
* Catalogs ship with filled `msgstr` for all Whisper languages (apply via `scripts/i18n-fill-translations.py` / packs under `scripts/i18n_fill_data/`); empty `msgstr` only as a brief in-progress state while filling in that same change — never commit or ship with missing translations. Polkit XML gettext is a follow-up.
* Best practices: whole sentences; printf `%s`/`%d` for PAM/shared C strings; `set_translation_domain` before Glade load; UTF-8 codeset; do not mark syslog/debug-only strings. See [`.agents/skills/i18n/SKILL.md`](.agents/skills/i18n/SKILL.md).
* Test: Settings Language **or** (with Automatic) `LANG=ro_RO.UTF-8 LANGUAGE=ro ubuntu-hello …` / DE system language — spot-check non-English UI.

---

## 3. Directory Layout Reference

Ensure files are modified or added in their appropriate structural directories:

| Path | Purpose / Description |
|---|---|
| `AGENTS.md` | Agent workspace guidelines (this file). |
| `VERSION` | **Single source of truth** for the project semver (`N.N.N`). Meson, PKGBUILD, i18n, tests, and CLI/GTK fallbacks read this file — do not duplicate the number in those consumers. |
| `.agents/skills/` | Focused skill runbooks (`*/SKILL.md`). |
| `docs/INSTRUCTIONS.md` | Contributor setup, build, extend, debug. |
| `docs/architecture/README.md` | Architecture deep dive. |
| `docs/SECURITY.md` | Security architecture and threat model. |
| `docs/releases/` | Per-version release notes + GitHub descriptions (`vX.Y.Z.md`, `vX.Y.Z_github_description.md`). Authored via `.agents/skills/release`. |
| `ubuntu-hello/src/pam/` | C++ PAM module (`main.cc`, `enter_device.cc`, `face_skip.*`, AES-GCM helpers). Spawns compare in a process group; after face success, sets `PAM_AUTHTOK` for downstream keyring/wallet modules. |
| `ubuntu-hello/src/cli/` | Command line subcommands (`add`, `clear`, `config`, `disable`, `list`, `remove`, `test`, `snapshot`, `keyring`). |
| `ubuntu-hello/src/wallet_backend.py` | Labels session wallet backend (`gnome-keyring`, `kwallet`, or `none`) from desktop env; does not change sealed credential format. |
| `ubuntu-hello/src/keyring_restore.py` | Unseals SUW login passwords and restores the OS wallet master password on uninstall / `ubuntu-hello keyring restore`. |
| `ubuntu-hello/src/install_config.py` | Meson install script: copy default `config.ini` into `/etc/ubuntu-hello` when missing (source installs). |
| `ubuntu-hello/src/config_ensure.py` | Recreate live `config.ini` from `/usr/share/ubuntu-hello/config.ini` when the file is missing (dpkg will not restore a deleted conffile after `apt remove`). |
| `ubuntu-hello/po/` | Core gettext domain `ubuntu-hello` (PAM `S()` + Python `_()`); `.pot`/`.po` committed, `.mo` built. |
| `ubuntu-hello/src/recorders/` | Camera capturing plugins (wrapper, ffmpeg, pyv4l2, cv2). |
| `ubuntu-hello/src/rubberstamps/` | Post-auth liveness check plugins (e.g., nose-tracking nod check). |
| `ubuntu-hello-gtk/src/` | Administrative settings panel (`window.py`), setup wizard (`onboarding.py`), and overlay window (`authsticky.py`). Instant language rebuild + fuzzy search; `preferences.py` / `languages.py` / `search_fuzzy.py`. |
| `scripts/uh-apt-deps.sh` | Shared apt build/runtime deps for `install.sh` / `uninstall.sh` (GTK/Babel, multi-DE theme tools, wallet PAM, polkit). Records newly added packages under `/var/lib/ubuntu-hello/apt-packages-added.list` for clean removal (auto transitive deps allowed on uninstall; untracked manual packages refused; auto checks avoid `apt-mark \| grep -q` under pipefail and use `</dev/null` inside `while read` loops). |
| `ubuntu-hello-gtk/bin/run_after_install.py` | Post-install setup-wizard launcher (installed under `/usr/share/ubuntu-hello-gtk/`). Invoked by `install.sh` / `ubuntu-hello-gtk` dpkg postinst **only when no face models are enrolled** (not by meson). Forwards Wayland/X11 session env; single-flight lock; log/lock under `/run/ubuntu-hello/` (`O_NOFOLLOW`, not `/tmp`). |
| `ubuntu-hello-gtk/src/theme_detect.py` | Multi-DE dark/light theme detection (GNOME/KDE/XFCE/Cinnamon/MATE/Budgie/LXQt); used by `window.py` and `authsticky.py`. |
| `ubuntu-hello-gtk/po/` | GTK gettext domain `ubuntu-hello-gtk` (Python `_()`, Glade, desktop `merge_file`). |
| `po/whisper-languages.txt` | Canonical Whisper language codes (98, omit `en`); both `LINGUAS` files must match. |
| `scripts/i18n-update.sh` | Refresh `.pot`, `msgmerge` `.po`, assert `LINGUAS` ↔ Whisper list. |
| `scripts/i18n-lint.py` | Lint JSON packs + gettext `.po` (UTF-8/JSON, `msgfmt --check`, no empty/fuzzy `msgstr`, placeholder parity, fill-pack ↔ `.pot` / `_keys.json` completeness). CI lint stage; pytest `test_all_translations_filled`. |
| `scripts/no-suppressions-lint.py` | Fail if production/tests contain `NOLINT`, `# shellcheck disable`, `# noqa`, `# type: ignore`, `@pytest.mark.skip`/`xfail`, etc. CI lint stage; pytest `test_repo_has_no_suppressions`. |
| `scripts/i18n-fill-translations.py` | Apply filled JSON maps under `scripts/i18n_fill_data/` onto committed `.po` files. |
| `scripts/read-version.py` | Print semver from repo-root `VERSION` (used by Meson, PKGBUILD, i18n). |
| `scripts/package-configure.sh` | Shared post-install configure (models, permissions, config restore, PAM, polkit); installs pinned `dlib` via pip when missing on the live host (sets `CMAKE_POLICY_VERSION_MINIMUM=3.5` for modern CMake; marker for prerm). Sourced by deb/rpm/snap/AppImage/Flatpak hooks. |
| `scripts/package-gtk-onboard.sh` | Shared setup-wizard launcher when no face models enrolled. |
| `scripts/package-prerm.sh` | Shared removal steps (PAM, keyring restore, cleanup). |
| `scripts/release-{deb,rpm,arch,portable,common}.sh` | Local/tag-release packaging drivers; output under `artifacts/`. Arch/RPM source tarball packs the **working tree** (excludes build/cache), not `git archive HEAD`, so packaging CI sees bind-mount fixes. |
| `packaging/` | Non-deb release metadata: Arch PKGBUILD, Fedora/openSUSE RPM specs, Snap, AppImage, Flatpak manifests, file lists. |
| `debian/` | Debian packaging control, installation, and post-installation scripts. `ubuntu-hello.postinst` restores `/etc/ubuntu-hello/config.ini` from `/usr/share/ubuntu-hello/config.ini` when the conffile is missing after `apt remove` / reinstall. Build trees (`tmp/`, `.debhelper/`, staged `ubuntu-hello*/`, `*.substvars`) are gitignored. |
| `artifacts/` | Gitignored release build output (`.deb`, `.rpm`, `.pkg.tar.zst`, `.snap`, `.AppImage`, `.flatpak`, `SHA256SUMS`). Local Meson/CI/packaging staging is also gitignored: `build/`, `build-*/` (AppImage, Flatpak, `build-ci-*`, …), `builddir/`, `builddir-*/`. |
| `docker/` | CI, PPA, and release Dockerfiles (`Dockerfile.ci*`, `Dockerfile.ppa`, `Dockerfile.rpm.*`, `Dockerfile.arch`, `Dockerfile.release`, `Dockerfile.snap`). Keep new Docker assets here — not at repo root. |
| `scripts/ppa-docker.sh` | PPA signed-source / binary helper inside `ubuntu-hello-ppa:26.04`. Uses `--sign-backend=gpg` + passphrase `gpg` wrapper (dpkg auto/Sequoia args break classic gpg). |
| `scripts/ci-docker.sh` | Stage runner; `UH_CI_STAGE=lint\|coverage\|compat` (+ `UH_CI_DE` for compat). BuildKit cache via `UH_CI_DOCKER_CACHE`. |
| `scripts/ci-pipeline.sh` | Full local gate: lint → coverage → compat matrix → packaging matrix (fail-fast between stages). |
| `scripts/ci-matrix.sh` | Launches **all** DE **compat** cells in parallel (never sequential). |
| `scripts/ci-packaging-matrix.sh` | Launches **all** packaging format cells in parallel (same `ci-packaging-cell.sh` as GHA). |
| `scripts/ci-packaging-cell.sh` | One format: build + smoke-verify + live E2E (shared by GHA `check.yml` and local pipeline). |
| `.github/workflows/check.yml` | GHA: **4 job definitions → 17 runners** (lint, coverage, 8× `compat` matrix, 7× `packaging` matrix) — no `needs` gates; packaging cells call `ci-packaging-cell.sh`; `concurrency` cancels stale runs on new PR/branch push; OSS up to **20 runners** (`max-parallel: 20` on both matrices). Local `./scripts/ci-pipeline.sh` fail-fast lint → coverage → compat → packaging. |
| `scripts/packaging-e2e-install.sh` | Live install E2E for built artifacts (PAM/`config.ini`; upgrade preserve + remove+reinstall). |
| `.github/workflows/release.yml` | GHA: on `v*` tag — PPA upload + parallel multi-format builds → GitHub Release with authored notes + `SHA256SUMS`. Per-format arch coverage and rationale: [`release-packaging` skill](.agents/skills/release-packaging/SKILL.md). |
| `logs/` | Agent progress + CI logs (`ci-lint.log`, `ci-coverage.log`, `ci-pipeline.log`, `ci-matrix/<de>.log`, `ci-packaging/<format>.log`). |

---

## 4. AI Coding Standards & Rules

### 4.1 C++ Implementation Guidelines

* Use **C++17** features for robust and clean code.
* Ensure all files conform to the project linter settings in `.clang-tidy`.
* Run background processes via safe subprocess spawning APIs (`posix_spawnp`) rather than raw `fork()`/`exec()` or vulnerable `system()` shell-escapes.
* Always clean up file descriptors, allocated resources, and threads (`std::future` or `std::thread`).

### 4.2 Python Implementation Guidelines

* Target **Python 3.10+**.
* Adhere to PEP 8 spacing and structure conventions.
* Implement structured error handling; wrap OS level syscalls, subprocess executions, and file I/O operations in `try-except` blocks.
* Keep imports organized and avoid circular dependencies (e.g., import cli components dynamically in the router `cli.py`).

### 4.3 Security & Integrity Rules

> [!CAUTION]
> The C++ PAM module and the comparison engine run with superuser (root) privileges. Security is paramount.

* **No Shell Arbitrary Code Execution**: Avoid executing shell strings. Use array/list-based subprocess invocations to prevent shell injection vectors.
* **Privilege Separation**: Keep system passwords, credentials, and facial models read-only and restricted to `root` or owners under `/etc/ubuntu-hello/`.
* **Resource Leak Prevention**: Ensure camera handles (`cv2.VideoCapture`), subprocesses, and shared memory pipes are explicitly closed/terminated in `finally` blocks.

### 4.4 Documentation & Comments

* Maintain documentation integrity. Keep existing comments and docstrings intact unless directly refactoring the referenced logic.
* Document any new class methods, rubberstamp plugins, or configuration options you add.

### 4.5 Lint and Test New or Changed Files (Mandatory)

> [!IMPORTANT]
> New or modified executable code must pass the same quality gates as the rest of the repo before the work is complete.

* **When adding or changing files**, run the applicable linters and tests in the **same change set** — do not defer lint/test fixes to CI or a follow-up.
* **By file type**:
  - **C++** (`*.cc`, `*.h`): meson/ninja build; clang-tidy (lint stage). New PAM/helpers: add or extend meson C++ tests when behavior is testable.
  - **Python** (`*.py`): `python3 -m py_compile` on touched modules; pytest under `tests/` when logic changes or new modules warrant coverage.
  - **Shell** (`scripts/*.sh`, packaging hooks): `shellcheck` where the lint stage applies; `scripts/no-suppressions-lint.py` forbids `# shellcheck disable` and other suppressions.
  - **All production + tests**: no `NOLINT` / `# noqa` / `# type: ignore` / `@pytest.mark.skip`/`xfail` (enforced by `scripts/no-suppressions-lint.py` in the lint stage).
  - **gettext / i18n**: see §4.7.0.
  - **Meson/build files**: confirm `meson setup` + `ninja` still succeed after edits.
* **New test files**: place under `tests/` using existing naming (`test_*.py`); wire C++ tests in meson when adding native tests.
* **Run order**: targeted checks first (e.g. `pytest tests/test_foo.py`, `python3 -m py_compile path/to/module.py`, a single meson test target), then broader gates for shared infrastructure (`UH_CI_STAGE=lint ./scripts/ci-docker.sh`, `UH_CI_STAGE=coverage ./scripts/ci-docker.sh`, or `./scripts/ci-pipeline.sh`). See [`.agents/skills/test-runner/SKILL.md`](.agents/skills/test-runner/SKILL.md) and [`.agents/skills/pipeline-runner/SKILL.md`](.agents/skills/pipeline-runner/SKILL.md).
* **Exceptions**: pure docs, release notes, or agent-only markdown with no executable code — lint/test not required unless translatable strings or i18n catalogs change.

### 4.6 Progress Visibility

* Always show what you are doing: prefer **printing/echoing progress to the terminal**, OR append lines under `logs/` at the repo root (e.g. `logs/aes-keyring-progress.log` or a general `logs/agent-progress.log`).
* Prefer `tee -a` (or equivalent) so the same progress line appears in both the terminal and the log when a feature-specific log is in use.
* Create `logs/` if missing. Do not leave long agent runs silent.
* See [logs/README.md](logs/README.md). Do **not** use other log roots for agent progress.

### 4.7 Always Update Agent Docs

* **Whenever you change any project files** (code, docs, config, tests, packaging, CI), also update the relevant agent guidance in the **same change set** so the next session has accurate context.
* Keep these in sync when behavior, paths, or workflows change:
  - [`AGENTS.md`](AGENTS.md) (this file — canonical)
  - [`.agents/skills/*/SKILL.md`](.agents/skills/)
  - [`docs/INSTRUCTIONS.md`](docs/INSTRUCTIONS.md)
  - [`docs/architecture/README.md`](docs/architecture/README.md)
  - Thin adapters if links/paths change: `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.cursor/rules/ubuntu-hello-agents.mdc`
* Prefer one canonical body here; keep adapters short pointers (no divergent rule copies).
* Document new modules, paths, security properties, workflows, and conventions you introduce or change.
* CI/Docker/DE-matrix rules below are mandatory agent constraints; if you change images, scripts, or supported DEs, update **both** `AGENTS.md` and the affected skills (especially `ci-docker-matrix`) in the same change.

### 4.7.0 Always Update Translations (Mandatory)

> [!IMPORTANT]
> Changing a user-visible / gettext-marked string without updating **all** language catalogs is incomplete work.

* If you add, edit, or remove a translatable string in PAM/Python/Glade/desktop sources, **in the same change set**:
  1. Run `./scripts/i18n-update.sh` (refresh `.pot`, `msgmerge` every `.po`, assert `LINGUAS` ↔ Whisper list).
  2. Fill **every** Whisper language for new/changed msgids (`scripts/i18n-fill-translations.py` / `scripts/i18n_fill_data/`).
  3. Run `python3 scripts/i18n-lint.py` and verify **no** empty `msgstr` and **no** unresolved fuzzy entries remain for either domain before finishing.
* Do not ship English-only UI for non-`en` locales after a string change. Details: [`.agents/skills/i18n/SKILL.md`](.agents/skills/i18n/SKILL.md).

### 4.7.1 Keep the Repository Root Clean (Mandatory)

> [!IMPORTANT]
> Do **not** pile new top-level files at the repo root. Prefer existing folders (`docker/`, `scripts/`, `docs/`, `.agents/`, `.github/`, `ubuntu-hello/`, `ubuntu-hello-gtk/`, `debian/`, `tests/`, `logs/`, …).

* **Allowed root exceptions** that are intentionally single-purpose: `VERSION` (project semver SSOT), `meson.build` / `meson.options`, `install.sh` / `uninstall.sh`, `AGENTS.md` / adapter stubs, and similar long-standing entrypoints.
* **CI / Docker assets** live under **`docker/`** (e.g. `docker/Dockerfile.ci`, `docker/Dockerfile.ci.lint`, `docker/Dockerfile.ppa`) — never add new CI Dockerfiles at the repository root.
* **Scripts** stay under `scripts/`; **workflows** under `.github/workflows/`; **agent runbooks** under `.agents/skills/`.
* When relocating or introducing paths: **update agent MDs first** (this file + affected skills + `docs/INSTRUCTIONS.md` / architecture), **then** move/add files, **then** fix all script/workflow references in the same change set. Do not leave stale root copies.

### 4.7.2 Project Version — Single Source of Truth (Mandatory)

* The **only** shipping semver pin is the repo-root **`VERSION`** file (one `N.N.N` line).
* Read it via **`scripts/read-version.py`** (or by opening `VERSION` directly). Meson, Arch PKGBUILD, i18n tooling, and test mocks must **not** hardcode a duplicate version string.
* Bumping a release = write the new number to `VERSION`, add a `debian/changelog` top entry, and regenerate release docs. Do **not** hunt-and-replace version literals across Meson files.
* **User-facing version** (CLI `paths.version`, Settings / setup wizard via `version_display.py`): always `v` + VERSION. Until that tag exists, Meson may append `-dev` only — **never** embed `git describe` / older tags (e.g. do not show `v1.1.0-dev (v1.0.4-…)`).
* Historical `docs/releases/vX.Y.Z*.md` and older changelog entries may mention prior versions (e.g. `1.0.4`) — that is expected archive content.

### 4.8 CI / Docker / Desktop Matrix (Mandatory)

> [!IMPORTANT]
> Fixed OS version, split stages (lint / coverage / compat), and parallel per-DE compat images are hard rules for agents touching Docker or CI.

* **Base image always fixed**: every CI/PPA Dockerfile must use `FROM ubuntu:26.04`. **Forbidden:** floating series tags, unpinned “current Ubuntu” aliases, or any dependency pin that uses the word `latest`.
* **Dockerfiles under `docker/`** (root stays clean): `docker/Dockerfile.ci.lint`, `docker/Dockerfile.ci.coverage`, `docker/Dockerfile.ci` (baseline compat), `docker/Dockerfile.ci.<de>`, `docker/Dockerfile.ppa`.
* **Three stages** (`UH_CI_STAGE`):
  * `lint` — `docker/Dockerfile.ci.lint` / `ubuntu-hello-ci-lint:26.04` / `build-ci-lint` — meson/ninja + clang-tidy + `py_compile` + `scripts/i18n-lint.py` (JSON + gettext `.po`) + `scripts/no-suppressions-lint.py` + `shellcheck` (packaging scripts)
  * `coverage` — `docker/Dockerfile.ci.coverage` / `ubuntu-hello-ci-coverage:26.04` / `build-ci-coverage` — meson/ninja + pytest coverage floors + meson C++ tests
  * `compat` — per-DE images under `docker/` — meson/ninja + `py_compile` + pytest (**no** cov floors) + meson C++ tests (**no** clang-tidy)
* **One Dockerfile + one image per DE** for compat — do **not** collapse DEs into a single ARG-switched Dockerfile. Prefer duplicated clear Dockerfiles. Do **not** fold lint/coverage back into every DE cell.
* **GHA check.yml parallelism**: **4** top-level jobs expand immediately to **17** runners (lint, coverage, 8× `compat` DE matrix, 7× `packaging` format matrix) to use OSS **20-runner** concurrency; **`concurrency.cancel-in-progress`** drops stale runs when the same PR or branch is pushed again; the packaging matrix skips **fork** `pull_request` events (`head.repo.full_name == github.repository`) and runs `scripts/ci-packaging-cell.sh` (build + smoke + live E2E); parallel cells share the bind mount — deb uses `override_dh_clean`, Flatpak stages under `UH_ARTIFACTS_DIR/.flatpak-work`. Local `./scripts/ci-pipeline.sh` fail-fast lint → coverage → compat → packaging (same cell script via `ci-packaging-matrix.sh`).
* **Compat matrix runs all DE cells in parallel** (local `scripts/ci-matrix.sh` and GHA `check.yml` `strategy.matrix` with `fail-fast: false`). **Never** serialize DE CI in one job or a sequential loop. Local parallel matrix sets `UH_CI_PARALLEL_BUILD=1`, which uses plain `docker build` (not buildx) so eight concurrent cells do not deadlock the shared buildx builder; lint/coverage still use buildx cache export when run alone.
* **Pinned CI deps** (explicit version tags/numbers only — never commit SHAs, never the `latest` alias):
  * GHA runners: `runs-on: ubuntu-26.04` (not a floating runner alias)
  * GHA actions: explicit tags (e.g. `actions/checkout@v7.0.1`, `docker/setup-buildx-action@v4.2.0`, `softprops/action-gh-release@v3.0.2`)
  * Docker base: `FROM ubuntu:26.04`; BuildKit frontend `# syntax=docker/dockerfile:1.26.0`
  * Pip in CI images: exact `==` pins (`pytest==9.1.1`, `pytest-cov==7.1.0`, `coverage==7.15.4`, `keyboard==0.13.5`)
  * Apt: distro-locked by `ubuntu:26.04` — no unpinned `curl|bash` installers
* **Image tags** use an explicit version suffix (`ubuntu-hello-ci-lint:26.04`, `ubuntu-hello-ci-<de>:26.04`, `ubuntu-hello-ppa:26.04`) — never rely on Docker’s implicit `:latest` tag.
* **Caching** (speed only; never weakens gates): `DOCKER_BUILDKIT=1`; Dockerfiles use BuildKit apt/pip cache mounts; `scripts/ci-docker.sh` supports `UH_CI_DOCKER_CACHE=local|gha|none`, skips rebuild when the image label matches the Dockerfile digest (`UH_CI_FORCE_BUILD=1` to force). On local `buildx` failure, continue only if the loaded image’s digest label matches the current Dockerfile digest (cache-export flake after `--load`); otherwise retry without cache export — never continue on a stale pre-existing tag. GHA uses `docker/setup-buildx-action@v4.2.0` + `UH_CI_DOCKER_CACHE=gha` (`type=gha` scopes per stage/DE). Local cache dir: `.cache/docker-ci/` (gitignored via `.cache`).
* **Lint / coverage images**:

| `UH_CI_STAGE` | Dockerfile | Image tag | Build dir |
|---|---|---|---|
| `lint` | `docker/Dockerfile.ci.lint` | `ubuntu-hello-ci-lint:26.04` | `build-ci-lint` |
| `coverage` | `docker/Dockerfile.ci.coverage` | `ubuntu-hello-ci-coverage:26.04` | `build-ci-coverage` |

* **Compat DE cells** (Dockerfile → default image tag → build dir):

| `UH_CI_DE` | Dockerfile | Image tag | Build dir |
|---|---|---|---|
| `baseline` | `docker/Dockerfile.ci` | `ubuntu-hello-ci-baseline:26.04` | `build-ci-baseline` |
| `gnome` | `docker/Dockerfile.ci.gnome` | `ubuntu-hello-ci-gnome:26.04` | `build-ci-gnome` |
| `kde` | `docker/Dockerfile.ci.kde` | `ubuntu-hello-ci-kde:26.04` | `build-ci-kde` |
| `xfce` | `docker/Dockerfile.ci.xfce` | `ubuntu-hello-ci-xfce:26.04` | `build-ci-xfce` |
| `cinnamon` | `docker/Dockerfile.ci.cinnamon` | `ubuntu-hello-ci-cinnamon:26.04` | `build-ci-cinnamon` |
| `mate` | `docker/Dockerfile.ci.mate` | `ubuntu-hello-ci-mate:26.04` | `build-ci-mate` |
| `budgie` | `docker/Dockerfile.ci.budgie` | `ubuntu-hello-ci-budgie:26.04` | `build-ci-budgie` |
| `lxqt` | `docker/Dockerfile.ci.lxqt` | `ubuntu-hello-ci-lxqt:26.04` | `build-ci-lxqt` |

* **Scripts**: `UH_CI_STAGE=lint|coverage ./scripts/ci-docker.sh` for quality stages; `UH_CI_STAGE=compat UH_CI_DE=<de> ./scripts/ci-docker.sh` for one compat cell; `./scripts/ci-pipeline.sh` for the full fail-fast gate (lint → coverage → compat → packaging); `./scripts/ci-matrix.sh` for parallel compat only; `./scripts/ci-packaging-matrix.sh` for parallel packaging only; `./scripts/ci-packaging-cell.sh <format>` for one packaging format. `docker/Dockerfile.ppa` stays `ubuntu:26.04` only (no DE packaging matrix).
* **Pipeline fix-until-green** (see `.agents/skills/pipeline-runner/SKILL.md`): when running the CI gate, **fix all failures and re-run until every stage and DE cell is green**. Do **not** ignore warnings, add NOLINT suppressions, add `# shellcheck disable=…` (or `shellcheck -e`), add `# noqa` / `# type: ignore` / similar Python suppressions, disable checks, raise clang-tidy thresholds, lower coverage floors, or skip steps to paper over red CI. Fix the code so linters pass cleanly. Each stage/cell must keep fail-fast quality steps (`set -e`, clang-tidy `WarningsAsErrors`).
* **Packaging log scan (mandatory with pipeline-runner)**: after the packaging matrix (or any packaging cell), **read** `logs/ci-packaging/<format>.log` for **every** format — do not stop at `packaging cell OK` / exit 0. Hunt and **fix** meaningful `ERROR` / `WARNING` / `!!!` / `error:` lines that indicate broken product behavior (examples: missing `dlib`, failed model downloads, `ubuntu-hello … unrecognized arguments`, PAM module / configure failures). Benign noise (optional-deps advisories, expected “already exists, skipping”) can stay; anything that means face auth, uninstall/keyring restore, or install configure is wrong must be fixed and the affected cell(s) re-run before declaring the pipeline done.
* **Keyring / wallet**: face auth sets `PAM_AUTHTOK` for consumers such as `pam_gnome_keyring` and `pam_kwallet5`. UX/docs should mention login keyring **or** KWallet; use `wallet_backend.py` for backend labels and `theme_detect.py` for multi-DE theme probes. Do not invent a second sealed-blob format for KWallet. Uninstall / apt `prerm` runs `timeout 120 ubuntu-hello keyring restore --all` before deleting `/etc/ubuntu-hello` (re-assert sealed login password as wallet password when possible; never abort removal on restore failure). Bare `keyring restore` restores only the CLI-selected user (`-U`).

---

## 5. Standard Exit Code Mapping

When modifying the verification loop or PAM helper logic, match these standardized exit codes:

| Code | Value | Description |
|---|---|---|
| `0` | `EXIT_SUCCESS` | Authentication succeeded, face verified. |
| `10` | `NO_FACE_MODEL` | Facial models missing or empty. |
| `11` | `TIMEOUT_REACHED` | Recognition loop timed out without a match (clock starts after the first usable frame; see `video.acquisition_timeout`). |
| `12` | `INVALID_ARGUMENTS` / `ABORT` | Invalid CLI args, or compare aborted via SIGTERM/SIGINT after cleanup. |
| `13` | `TOO_DARK` | Camera frames stayed dark/black through the acquisition window. |
| `14` | `INVALID_DEVICE` | Could not capture frames from target webcam. |
| `15` | `RUBBERSTAMP_FAIL` | Face verified but liveness/rubberstamp check failed. |

Related PAM returns (not compare exit codes): `PAM_AUTHINFO_UNAVAIL` when face is skipped (disabled, SSH, lid closed, or greeter skip-after-failure marker).
