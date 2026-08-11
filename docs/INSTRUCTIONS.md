# Ubuntu Hello — Developer Instructions

Contributor and agent guide for building, extending, debugging, and running local CI for **Ubuntu Hello**.

Canonical agent rules: [AGENTS.md](../AGENTS.md). Architecture: [architecture/README.md](architecture/README.md). Skills: [`.agents/skills/`](../.agents/skills/). Thin tool adapters: [CLAUDE.md](../CLAUDE.md), [GEMINI.md](../GEMINI.md), [`.github/copilot-instructions.md`](../.github/copilot-instructions.md).

---

## 1. Directory Structure

```
├── AGENTS.md                  # Agent workspace guidelines
├── VERSION                    # Project semver (single source of truth)
├── .agents/skills/            # Focused skill runbooks (*/SKILL.md)
├── .clang-tidy                # C++ linter settings
├── meson.build                # Root build system (version from VERSION)
├── meson.options              # Build options (paths, dependencies)
├── install.sh / uninstall.sh  # Host install / uninstall scripts
├── scripts/
│   ├── ci-docker.sh           # Stage runner (UH_CI_STAGE=lint|coverage|compat)
│   ├── ci-pipeline.sh         # Full gate: lint → coverage → compat matrix
│   ├── ci-matrix.sh           # Parallel DE compat matrix
│   ├── read-version.py        # Print semver from VERSION
│   ├── i18n-update.sh         # Refresh gettext pots/po; assert LINGUAS
│   └── ppa-docker.sh          # PPA packaging Docker helper
├── po/
│   └── whisper-languages.txt  # Canonical Whisper codes for LINGUAS (omit en)
├── docker/                    # CI/PPA Dockerfiles (keep root clean)
│   ├── Dockerfile.ci.lint
│   ├── Dockerfile.ci.coverage
│   ├── Dockerfile.ci          # Baseline compat (FROM ubuntu:26.04)
│   ├── Dockerfile.ci.<de>     # Per-DE compat images
│   └── Dockerfile.ppa
├── logs/                      # Agent progress + CI stage/matrix logs
├── tests/                     # pytest + PAM C++ unit tests
├── docs/
│   ├── INSTRUCTIONS.md        # This file
│   ├── architecture/README.md
│   └── SECURITY.md
├── ubuntu-hello/              # Core backend module
│   ├── src/
│   │   ├── bin/               # /usr/bin/ubuntu-hello wrapper template
│   │   ├── cli/               # Subcommands (add, test, config, keyring, …)
│   │   ├── pam/               # C++ PAM module (main.cc, face_skip, AES-GCM)
│   │   ├── recorders/         # Camera plugins (ffmpeg, pyv4l2, cv2)
│   │   ├── rubberstamps/      # Post-auth hooks (nod, hotkey)
│   │   ├── cli.py             # CLI router
│   │   ├── compare.py         # Face verification engine
│   │   ├── config.ini         # Default configuration template
│   │   ├── keyring_crypto.py  # UH1 AES-GCM helpers
│   │   ├── wallet_backend.py  # gnome-keyring / kwallet / none labels
│   │   └── paths_factory.py
│   ├── po/                    # gettext domain ubuntu-hello
│   └── meson.build
└── ubuntu-hello-gtk/          # Graphical UI
    ├── src/
    │   ├── authsticky.py      # Floating auth overlay
    │   ├── window.py          # Admin / control panel
    │   ├── onboarding.py      # Setup wizard
    │   ├── theme_detect.py    # Multi-DE dark/light detection
    │   ├── tab_*.py           # Settings tabs
    │   └── polkit/            # Polkit rules
    ├── po/                    # gettext domain ubuntu-hello-gtk
    └── meson.build
```

---

## 2. Dev Environment and Build Lifecycle

### 2.1 Dependencies

On Debian/Ubuntu (baseline **26.04 / resolute**):

```bash
sudo apt-get update && sudo apt-get install -y \
  python3 python3-pip python3-dev python3-setuptools python3-wheel \
  python3-numpy python3-opencv python3-cryptography python3-babel \
  python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  cmake make build-essential g++ gettext \
  libpam0g-dev libinih-dev libevdev-dev libopencv-dev libssl-dev \
  libboost-all-dev pkg-config \
  meson ninja-build git curl wget bzip2 \
  v4l-utils libopenblas-dev liblapack-dev tpm2-tools \
  dconf-cli libglib2.0-bin xfconf libkf6config-bin \
  libpam-gnome-keyring libpam-kwallet5 \
  pkexec polkitd
```

`install.sh` installs this full set via [`scripts/uh-apt-deps.sh`](../scripts/uh-apt-deps.sh) (build + runtime for every supported DE). Packages that were **not** already present are recorded under `/var/lib/ubuntu-hello/apt-packages-added.list` and removed again by `uninstall.sh` (base packages such as `python3` are never removed). Uninstall also allows apt to drop **auto-installed** transitive deps of those tracked packages (e.g. `libxfconf-0-3` with `xfconf`); it still refuses to remove untracked **manual** packages. The auto check uses `grep … < <(apt-mark showauto </dev/null)` so it stays correct under `set -o pipefail` and inside `while read` plan validation.
dlib (often via pip):

```bash
pip3 install dlib --break-system-packages
```

### 2.2 Compilation and Installation (Meson)

```bash
rm -rf builddir/

meson setup builddir -Dprefix=/usr -Dsysconfdir=/etc -Dlibdir=lib \
  -Dinstall_pam_config=true -Dwith_polkit=true -Dfetch_dlib_data=true \
  -Dinih:with_INIReader=true

meson compile -C builddir
sudo meson install -C builddir
```

Shorter local configure (defaults from `meson.options`):

```bash
meson setup build
meson compile -C build
sudo meson install -C build
```

#### Notable build options

* `python_path` — Absolute path to Python binary (Meson-detected by default).
* `config_dir` — Config path (default `/etc/ubuntu-hello`).
* `user_models_dir` — Face models (default `/etc/ubuntu-hello/models`).

### 2.3 Localization (gettext)

**Default** is Automatic (system `LANG` / `LANGUAGE` / `LC_*`). Settings may override language for Python UI processes.

| Domain | Tree | Sources |
|---|---|---|
| `ubuntu-hello` | `ubuntu-hello/po/` | PAM `S()`, core Python `_()` |
| `ubuntu-hello-gtk` | `ubuntu-hello-gtk/po/` | GTK `_()`, Glade, desktop |

- Catalogs install to `$prefix/share/locale/<lang>/LC_MESSAGES/<domain>.mo`.
- Language list: `po/whisper-languages.txt` (98 Whisper codes, omit `en`); both `LINGUAS` must match.
- **Settings → Language**: Automatic (default), English, or another locale; writes `~/.config/ubuntu-hello/preferences.ini` (`[ui] language=…`). Combo labels use Babel/CLDR (`python3-babel`) in the active UI language, with each language’s native name in parentheses when it differs (e.g. `German (Deutsch)`). Applies **instantly** in the open Settings window (gettext reload + Glade rebuild; no restart). Automatic always remains in the list. PAM ignores this file; CLI/compare pick it up on next start.
- **Settings search**: header-bar `Gtk.SearchEntry` on the **left** (`pack_type=start`) with **fuzzy** match (stdlib `difflib` + subsequence) over currently displayed (translated) labels; rebuilds after language switch.
- **Native multi-DE**: Settings remains GTK3+Glade on GNOME/KDE/XFCE/Cinnamon/MATE/Budgie/LXQt; use `theme_detect`; do not introduce web UI.
- Refresh: `./scripts/i18n-update.sh` (asserts LINGUAS, refreshes `.pot`, `msgmerge`s `.po` without wiping msgstr).
- **Mandatory**: after any translatable string add/change/remove, refresh catalogs and fill **all** Whisper languages in the same change (`i18n-fill-translations.py`); do not leave empty `msgstr` or fuzzy gaps. See AGENTS.md §4.6.0 and the i18n skill.
- Verify: Settings Language (instant) **or** `LANG=ro_RO.UTF-8 LANGUAGE=ro ubuntu-hello list` — spot-check non-English strings.
- Skill: [`.agents/skills/i18n/SKILL.md`](../.agents/skills/i18n/SKILL.md).

#### Native Settings UX checklist (manual)

Settings stays **native GTK3 + Glade** (stock `HeaderBar` / `Notebook` / `SearchEntry` / `ComboBoxText` / dialogs). Do **not** introduce web/Electron/custom chrome. Automated smoke: Settings E2E under xvfb in every `UH_CI_DE` compat cell. On each supported DE (Ubuntu **26.04**), also verify subjectively:

| Check | GNOME | KDE/Plasma | XFCE | Cinnamon | MATE | Budgie | LXQt |
|---|---|---|---|---|---|---|---|
| Window opens; system light/dark via `theme_detect` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Layout readable (no clipped HeaderBar / notebook) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Fuzzy HeaderBar search filters tabs; clear restores | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Language tab applies **instantly** (Automatic stays listed; no restart) | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Polkit elevation keeps display + locale + DE env | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Keyboard focus order usable on stock widgets | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

### 2.4 Uninstall

```bash
sudo bash uninstall.sh
```

### 2.5 Quick install (from GitHub)

```bash
curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/install.sh | sudo bash
```

After a successful install the **setup wizard** should open automatically **once** (approve the polkit prompt if shown). `install.sh` / dpkg postinst call `run_after_install.py` a single time after install completes (meson does not launch the GUI). The launcher forwards the real user’s Wayland/X11 session (`XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY`, session bus) — not just `DISPLAY`. If the window does not appear, check `/tmp/ubuntu-hello-postinstall.log` and run:

```bash
ubuntu-hello-gtk --force-onboarding
```

### 2.6 PPA (Ubuntu / Linux Mint)

```bash
sudo add-apt-repository ppa:ventura8/ubuntu-hello
sudo apt update
sudo apt install ubuntu-hello
```

PPA packaging Docker helper: `scripts/ppa-docker.sh` / `docker/Dockerfile.ppa` (`FROM ubuntu:26.04` only).

Keep the **repo root clean**: put new CI/Docker assets under `docker/` (see [AGENTS.md](../AGENTS.md) §4.6.1).

---

## 3. Extending the Codebase

### 3.1 Adding a New CLI Command

CLI subcommands live in `ubuntu-hello/src/cli/`.

1. Create `ubuntu-hello/src/cli/example.py`.
2. Use args via `builtins.ubuntu_hello_args`.
3. Register in `ubuntu-hello/src/cli.py`:

```python
parser.add_argument(
    "command",
    choices=["add", "clear", "config", "disable", "list", "remove", "set",
             "snapshot", "test", "version", "keyring", "example"]
)
# ...
elif args.command == "example":
    import cli.example
```

### 3.2 Adding a New Rubberstamp

1. Create `ubuntu-hello/src/rubberstamps/yourstamp.py`.
2. Inherit from `RubberStamp`; implement `declare_config` and `run`.
3. Enable in `/etc/ubuntu-hello/config.ini`:

```ini
[rubberstamps]
enabled = true
stamp_rules =
    yourstamp  5s  failsafe  custom_parameter=12.0
```

See skill [rubberstamp](../.agents/skills/rubberstamp/SKILL.md).

---

## 4. Debugging & Troubleshooting

### 4.1 Bypassing PAM

```bash
sudo python3 /lib/security/ubuntu-hello/compare.py <target_username>
```

Use `/usr/lib/security/ubuntu-hello/compare.py` if that is the installed path. Visual diagnostics: `sudo ubuntu-hello test`.

### 4.2 Logging

```bash
tail -f /var/log/auth.log | grep pam_ubuntu_hello
# or
journalctl -f | grep pam_ubuntu_hello
```

Debug knobs in `/etc/ubuntu-hello/config.ini`:

```ini
[debug]
end_report = true
verbose_stamps = true
gtk_stdout = true
```

### 4.3 PAM Lockout Recovery

If a broken PAM module locks out graphical login / `sudo`:

1. Switch to a virtual console (`Ctrl`+`Alt`+`F3`).
2. Log in with password.
3. Comment out the `pam_ubuntu_hello.so` line in `/etc/pam.d/common-auth`.

### 4.4 Exit codes

| Exit | Constant | Meaning |
|---|---|---|
| `0` | `EXIT_SUCCESS` | Face verified |
| `10` | `NO_FACE_MODEL` | Missing/empty models |
| `11` | `TIMEOUT_REACHED` | Recognition timeout (after first usable frame) |
| `12` | `INVALID_ARGUMENTS` / `ABORT` | Bad args or SIGTERM/SIGINT after cleanup |
| `13` | `TOO_DARK` | Frames too dark / no usable frame before acquisition timeout |
| `14` | `INVALID_DEVICE` | Camera open/read failed |
| `15` | `RUBBERSTAMP_FAIL` | Liveness check failed |

PAM may return `PAM_AUTHINFO_UNAVAIL` without running compare (disabled/SSH/lid closed, or legacy `*screensaver*` skip-after-failure under `/run/ubuntu-hello/face-skip/`). GNOME lock uses `gdm-password` and does **not** skip after failure (Esc→Enter retries face). See `core.skip_face_after_failure` in `config.ini`.

> [!CAUTION]
> **Greeter login hard rule:** with `core.workaround=off`, do **not** force concurrent `pam_get_authtok` (or greeter `PAM_CONV` wrappers) on `gdm-password` / login. That aborts GDM user-selection → login (user is thrown straight back to the account list). `ask_pass` must stay `ask_auth_tok && workaround != Off`. Details: [AGENTS.md](../AGENTS.md), [`.agents/skills/pam-verifier/SKILL.md`](../.agents/skills/pam-verifier/SKILL.md).

---

## 5. Local CI Quality Bar

CI is split into three fail-fast stages on Ubuntu **26.04** (see [AGENTS.md](../AGENTS.md) §4.7):

| Stage | Image | Checks |
|---|---|---|
| `lint` | `ubuntu-hello-ci-lint:26.04` | meson/ninja, clang-tidy (PAM C++), `py_compile` |
| `coverage` | `ubuntu-hello-ci-coverage:26.04` | meson/ninja, pytest ≥ 90%, keyring coverage 100%, `meson test pam-aes-gcm-uh1` |
| `compat` | `ubuntu-hello-ci-<de>:26.04` | meson/ninja, `py_compile`, pytest (no cov floors), `meson test pam-aes-gcm-uh1` |

Commands:

```bash
# Full gate (preferred): lint → coverage → parallel compat matrix
./scripts/ci-pipeline.sh

# Individual stages
UH_CI_STAGE=lint ./scripts/ci-docker.sh
UH_CI_STAGE=coverage ./scripts/ci-docker.sh
UH_CI_STAGE=compat UH_CI_DE=kde ./scripts/ci-docker.sh

# Compat-only parallel matrix (all DEs)
./scripts/ci-matrix.sh
```

Caching: BuildKit is on by default for image builds; set `UH_CI_DOCKER_CACHE=local` (default), `gha` (GitHub Actions), or `none`. Unchanged Dockerfiles reuse the tagged image (digest label); `UH_CI_FORCE_BUILD=1` forces a rebuild.

Pins: GHA `runs-on: ubuntu-26.04`; actions use explicit version tags (e.g. `@v7.0.1`); CI pip packages are exact (`pytest==9.1.1`, `pytest-cov==7.1.0`, `coverage==7.15.4`, `keyboard==0.13.5`); Docker `ubuntu:26.04` + `# syntax=docker/dockerfile:1.26.0`. Never pin by commit SHA; never use a `latest` alias.

Logs: `logs/ci-lint.log`, `logs/ci-coverage.log`, `logs/ci-pipeline.log`, `logs/ci-matrix/<de>.log` (see [logs/README.md](../logs/README.md)).

Coverage/lint SVG badges (host): `python3 generate_badges.py`.

Do not invent extra lint/coverage gates that this repository does not enforce.

---

## 6. Face Profile CLI Cheatsheet

```bash
sudo ubuntu-hello add
sudo ubuntu-hello list
sudo ubuntu-hello remove <model_id>
sudo ubuntu-hello clear
sudo ubuntu-hello keyring enable   # login keyring / KWallet via PAM_AUTHTOK
sudo ubuntu-hello test
```

More detail: [`.agents/skills/face-cli/SKILL.md`](../.agents/skills/face-cli/SKILL.md).
