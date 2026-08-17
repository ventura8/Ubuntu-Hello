# Ubuntu Hello Architecture

Deep dive into the architecture, component interaction, and design patterns of the **Ubuntu Hello** facial recognition authentication system.

Agent rules: [AGENTS.md](../../AGENTS.md). Contributor setup: [INSTRUCTIONS.md](../INSTRUCTIONS.md). Security: [SECURITY.md](../SECURITY.md).

---

## 0. Repository Layout

```
├── AGENTS.md                  # canonical agent rules
├── CLAUDE.md / GEMINI.md      # thin multi-tool adapters
├── .github/copilot-instructions.md
├── .cursor/rules/             # thin Cursor rule → AGENTS.md
├── .agents/skills/*/SKILL.md
├── install.sh / uninstall.sh
├── scripts/
│   ├── ci-docker.sh
│   ├── ci-pipeline.sh
│   ├── ci-matrix.sh
│   ├── i18n-update.sh
│   ├── i18n-lint.py
│   └── ppa-docker.sh
├── po/
│   └── whisper-languages.txt   # Whisper codes → LINGUAS (omit en)
├── docker/                    # CI/PPA Dockerfiles (root stays clean)
│   ├── Dockerfile.ci.lint
│   ├── Dockerfile.ci.coverage
│   ├── Dockerfile.ci          # baseline compat
│   ├── Dockerfile.ci.{gnome,kde,xfce,cinnamon,mate,budgie,lxqt}
│   └── Dockerfile.ppa
├── logs/
├── tests/
├── docs/
│   ├── INSTRUCTIONS.md
│   ├── architecture/README.md   # this file
│   └── SECURITY.md
├── ubuntu-hello/
│   ├── po/                      # domain ubuntu-hello
│   └── src/
│       ├── pam/                 # C++ PAM module
│       ├── cli/                 # CLI subcommands
│       ├── recorders/           # Camera plugins
│       ├── rubberstamps/        # Liveness plugins
│       ├── compare.py
│       ├── keyring_crypto.py
│       ├── keyring_restore.py
│       └── wallet_backend.py
└── ubuntu-hello-gtk/
    ├── bin/run_after_install.py # postinst wizard; lock/log under /run/ubuntu-hello
    ├── po/                      # domain ubuntu-hello-gtk
    └── src/
        ├── authsticky.py
        ├── window.py
        ├── onboarding.py
        └── theme_detect.py
```

---

## 1. High-Level System Architecture

Ubuntu Hello functions as a PAM (Pluggable Authentication Module) provider. When a Linux service requests authentication (e.g., `sudo`, lock screen login, or GDM), the PAM stack invokes the Ubuntu Hello C++ module. This module then spawns a Python subprocess to handle camera streaming, face detection, and verification, communicating progress back to a floating GTK status window.

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant PAM as PAM Service (sudo/gdm)
    participant C++ as pam_ubuntu_hello.so (C++)
    participant Python as compare.py (Python)
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
    GTK->>User: Display "Identifying you..." & frame count
    
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
        Note over C++: Lock/screensaver: set face-skip marker;<br/>password fallback only if workaround enabled
        C++-->>PAM: Return PAM_AUTH_ERR / IGNORE
        PAM-->>User: Fallback to Password Prompt
    else Password wins first (cancel; workaround only)
        C++->>Python: SIGTERM process group (then SIGKILL if needed)
    end
    deactivate C++
```

---

## 2. Core Components

### 2.1 Pluggable Authentication Module (PAM) (`ubuntu-hello/src/pam/`)

* **Role**: The main entry point loaded by Linux security services. Written in C++ for security and low runtime footprint.
* **Key Files**:
  - `main.cc`: Implements standard PAM hooks (`pam_sm_authenticate`, `pam_sm_open_session`, `pam_sm_setcred`, etc.). Parses config, detects laptop lid status via ACPI button interface (`/proc/acpi/button/lid/*/state`), and checks for SSH environments. After successful face auth, `try_set_keyring_authtok` sets `PAM_AUTHTOK` so downstream modules (`pam_gnome_keyring`, `pam_kwallet5`) can unlock the session wallet.
  - `face_skip.hh` / `face_skip.cc`: Per-user skip markers under `/run/ubuntu-hello/face-skip/<username>` after a completed face failure on legacy `*screensaver*` PAM services (`core.skip_face_after_failure`, default on). Next auth returns `PAM_AUTHINFO_UNAVAIL` until success/`pam_sm_setcred`. **GNOME lock shares `gdm-password` with login — skip is not applied there** so Esc→Enter can retry face.
  - `aes_gcm_uh1.cc` / `hh`: Software keyring blob format `UH1:` (AES-256-GCM).
  - `enter_device.cc` / `hh`: Simulates a keyboard "Enter" keypress via `/dev/uinput` to dismiss active system password prompts when facial authentication completes.
  - `optional_task.hh`: Helper class managing background threads using `std::future` to listen for user passwords concurrently.
* **Process Lifecycle**: Spawns Python using `posix_spawnp` with `POSIX_SPAWN_SETPGROUP` so compare and its GTK child share a process group. On password-first cancel (**only when `core.workaround` is `input`/`native`**), PAM sends `SIGTERM` to the group, waits briefly, then `SIGKILL` if needed — compare owns GTK teardown; PAM does not signal GTK directly. `compare.py` sets `PR_SET_PDEATHSIG` so orphans die with the PAM worker.
* **HARD RULE (greeter login):** Never force greeter `pam_get_authtok` / custom `PAM_CONV` when `workaround=off`. That breaks GDM user-selection → login (immediate bounce back to the user list). Canonical: `ask_pass = ask_auth_tok && workaround != Off`. See [AGENTS.md](../../AGENTS.md).
* **Desktop notes**: Face authentication through `common-auth` is DE-agnostic (GDM/SDDM/LightDM). Wallet auto-unlock depends on which AUTHTOK consumer is installed in the PAM stack.

### 2.2 Face Matching Engine (`ubuntu-hello/src/compare.py`)

* **Role**: Orchestrates camera feeds and face detection.
* **Logic Flow**:
  1. Spawns `ubuntu-hello-gtk --start-auth-ui` as a subprocess to create the overlay; registers `atexit` plus `SIGTERM`/`SIGINT` handlers so the camera is `release()`d and GTK is terminated even when PAM kills the process group.
  2. Spawns a background thread to load heavy recognition models (`dlib`).
  3. Initializes the camera recorder plugin and applies contrast optimization (`cv2.createCLAHE`).
  4. Runs a capture-and-compare loop:
     - Discards fully black/dark frames based on configurable brightness thresholds.
     - Feeds valid frames to the `dlib` face detector.
     - Evaluates facial features using a 5-point landmark shape predictor and a ResNet face recognition model.
     - Computes the Euclidean distance (L2 norm) between current face descriptors and saved models:
       $$\text{distance} = \|\mathbf{v}_{\text{known}} - \mathbf{v}_{\text{current}}\|_{2}$$
     - If the lowest distance is below `certainty / 10`, matches are accepted.
  5. Executes post-auth checks (Rubberstamps) if enabled.
  6. Exits with corresponding status codes (`0` on success, or error status codes); cleanup always releases the camera before exit.

### 2.3 GTK Graphical Interface (`ubuntu-hello-gtk/src/`)

* **Role**: Displays status notifications and onboarding/administration windows.
* **Key Sub-modules**:
  - `authsticky.py` (`StickyWindow`): A frameless, semi-transparent top-aligned overlay mimicking Windows Hello. Communicates with the matching engine by parsing `sys.stdin` commands line-by-line.
  - `theme_detect.py`: Shared dark/light detection across GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, and LXQt (used by `window.py` and `authsticky.py`).
  - `window.py` / `tab_models.py` / `tab_video.py` / `tab_keyring.py`: The administrative UI for managing users, adding/removing face profiles, tweaking camera parameters, and enabling keyring/KWallet auto-unlock.
  - `onboarding.py`: Wizard helping first-time users identify their camera and construct their first facial profile.
* **Post-install launcher** (`bin/run_after_install.py`): `install.sh` / dpkg postinst spawn this as root when no face models are enrolled. Single-flight lock and log live under `/run/ubuntu-hello/` (`0700`, `O_NOFOLLOW`), not world-writable `/tmp`.

### 2.4 Administration CLI (`ubuntu-hello/src/cli.py` & `cli/`)

* **Role**: Configures the facial engine, handles profile collection, and executes diagnostic tests.
* **Commands**:
  - `add.py`: Guides users in creating a new profile. Captures up to 60 frames and extracts the first frame containing exactly one face to write to models.
  - `list.py`, `remove.py`, `clear.py`: Manage profile models (`models/<username>.dat`).
  - `keyring.py`: Enable/disable automatic unlock of the login keyring or KWallet after face login (same sealed credential + `PAM_AUTHTOK`). `keyring restore` re-asserts the sealed login password for the selected user; `keyring restore --all` sweeps every sealed user (used by uninstall / apt `prerm` before deleting seals).
  - `test.py`: Debugging CLI tool that launches a local window showing the camera stream with highlighted landmarks and matching thresholds.
* **Helpers**: `wallet_backend.py` labels the inferred session wallet (GNOME Keyring vs KWallet) for CLI/GTK copy. `keyring_restore.py` unseals TPM/`UH1:` credentials and talks to GNOME Keyring (`ChangeWithMasterPassword`) or KWallet over the user’s session bus.

### 2.5 Camera Recorders Abstraction (`ubuntu-hello/src/recorders/`)

* **Role**: Abstracts different camera APIs to deal with varying Linux kernel webcam drivers.
* **Plugins**:
  - `video_capture.py`: Selects the appropriate reader according to the `recording_plugin` config parameter.
  - `v4l2.py` / `pyv4l2_reader.py`: Low-level wrapper around V4L2 (Video4Linux) ioctl calls.
  - `ffmpeg_reader.py`: Uses a non-blocking subprocess pipeline reading raw frames from FFmpeg.
  - `cv2.VideoCapture`: standard OpenCV backend.

### 2.6 Rubberstamps Hooks System (`ubuntu-hello/src/rubberstamps/`)

* **Role**: Provides post-verification liveness checks to prevent static photo spoofs.
* **Architecture**:
  - Inherits from `RubberStamp` base class. Uses `SourceFileLoader` to load plugins dynamically.
  - `nod.py`: Nose-tracking algorithm checking if the user actively nods up and down (to verify authentication) or shakes their head (to abort).
  - `hotkey.py`: Prompts the user to hit a specific hotkey (like `Enter` or `Esc`) to verify intent.

---

## 3. Data Flow & Inter-Process Communication (IPC)

### 3.1 C++ to Python Subprocess Launch

The PAM module starts Python as a subprocess in its own process group:

```cpp
posix_spawnattr_setflags(&spawn_attr, POSIX_SPAWN_SETPGROUP);
posix_spawnattr_setpgroup(&spawn_attr, 0);
posix_spawnp(&child_pid, PYTHON_EXECUTABLE_PATH, &actions, &spawn_attr, args, nullptr);
```

C++ evaluates the return code using standard wait macros:

* `EXIT_SUCCESS` ($0$): Face authenticated (also clears any face-skip marker).
* `11` (`TIMEOUT_REACHED`): No confident match within `video.timeout` seconds **after** the first usable (non-dark) frame. IR warm-up / black frames use a separate acquisition window (`video.acquisition_timeout`, auto-derived when `-1`) so they do not burn the recognition timeout.
* `13` (`TOO_DARK`): No usable frames before acquisition timeout, or every captured frame was above `dark_threshold`.
* `10` (`NO_FACE_MODEL`): No face models recorded for the target user.

On lock/screensaver services, a non-zero compare exit sets `/run/ubuntu-hello/face-skip/<user>` so the next attempt returns `PAM_AUTHINFO_UNAVAIL` until successful auth/`pam_sm_setcred`. Login greeters always retry face. Cancel paths signal the whole group (`kill(-child_pid, SIGTERM)` then `SIGKILL` after a short grace period).

### 3.2 Python to GTK Overlay Pipe

`compare.py` starts `ubuntu-hello-gtk` and keeps a handle to standard input:

```python
gtk_proc = subprocess.Popen(["ubuntu-hello-gtk", "--start-auth-ui"], stdin=subprocess.PIPE)
```

Status updates are written as short formatted line structures:

- `M=<text>`: Updates main window status text.
- `S=<text>`: Updates subtext status.

`authsticky.py` runs a 10ms loop reading line-by-line using `gobject.timeout_add`:

```python
comm = sys.stdin.readline()[:-1]
if comm.startswith("M="):
    self.message = comm[2:].strip()
```

---

## 4. Models and Profiles Format

Face models are saved in `/etc/ubuntu-hello/models/<username>.dat` as standard JSON databases:

```json
[
  {
    "id": 0,
    "label": "Initial model",
    "time": 1785939200,
    "data": [
      [-0.110293, 0.089201, 0.043920, "..."]
    ]
  }
]
```

* `id`: Incremental integer identifying the scan.
* `label`: String to identify the scan condition (e.g. "Glasses", "Morning").
* `time`: Unix timestamp.
* `data`: Array containing a 128-dimensional floating point vector generated by dlib's ResNet face recognition model.

---

## 5. Keyring / KWallet Auto-Unlock

Supported Ubuntu-family desktops for theme follow and wallet UX: **GNOME, KDE Plasma, XFCE, Cinnamon, MATE, Budgie, LXQt**.

After a successful face match, `pam_ubuntu_hello` unseals the stored password (TPM or `UH1:` AES-GCM) and sets `PAM_AUTHTOK`. Downstream modules consume that token:

* `pam_gnome_keyring` / `pam_kwallet5` — GNOME Keyring (GNOME-family) and KWallet (Plasma); packaging Depends on both so wallet unlock works across supported DEs; `install.sh` installs the same pair for source installs.

No second sealed-blob format exists for KWallet; Plasma support is the same credential plus UX/docs/packaging awareness.

---

## 5.1 Localization

gettext catalogs with **Automatic** system locale by default and an optional Settings language override:

* Domains `ubuntu-hello` / `ubuntu-hello-gtk` under `$prefix/share/locale/.../LC_MESSAGES/`.
* PAM: `setlocale` + `bindtextdomain` + UTF-8 codeset only (no user preference file).
* Python: configured `i18n.py` reads `~/.config/ubuntu-hello/preferences.ini` (`[ui] language=auto|<code>`) before loading catalogs; GTK Builder: `set_translation_domain` before Glade load.
* Settings: **Language** tab (Automatic + English + locales; Babel/CLDR combo labels via `python3-babel`, UI name + native name in parentheses; **instant** in-session apply) + header-bar **fuzzy** search (`search_fuzzy.py`); native GTK3 on all supported DEs via stock widgets + `theme_detect`. Manual multi-DE UX checklist: [docs/INSTRUCTIONS.md §2.3](../INSTRUCTIONS.md).
* Whisper language parity list: `po/whisper-languages.txt` → `LINGUAS`. Filled `msgstr` for all Whisper languages.
* Polkit policy XML localization is deferred.

---

## 6. CI / Docker Layout

Target OS for CI is fixed **Ubuntu 26.04**. Quality work is split into **lint**, **coverage**, and **compat** stages. Each DE compat cell has its own Dockerfile and image; the compat matrix runs **in parallel**.

| Stage / `UH_CI_DE` | Dockerfile | Default image |
|---|---|---|
| `lint` | `docker/Dockerfile.ci.lint` | `ubuntu-hello-ci-lint:26.04` |
| `coverage` | `docker/Dockerfile.ci.coverage` | `ubuntu-hello-ci-coverage:26.04` |
| `baseline` (compat) | `docker/Dockerfile.ci` | `ubuntu-hello-ci-baseline:26.04` |
| `gnome` … `lxqt` (compat) | `docker/Dockerfile.ci.<de>` | `ubuntu-hello-ci-<de>:26.04` |

* Full gate: `./scripts/ci-pipeline.sh` (lint → coverage → compat matrix; fail-fast between stages)
* Single stage/cell: `UH_CI_STAGE=lint|coverage|compat` (+ `UH_CI_DE` for compat) `./scripts/ci-docker.sh`
* Compat-only parallel matrix: `./scripts/ci-matrix.sh` → `logs/ci-matrix/<de>.log`
* GHA: lint + coverage jobs in parallel; `compat` needs both; `strategy.matrix.de` with `fail-fast: false`; Buildx + `UH_CI_DOCKER_CACHE=gha`
* Caching: BuildKit apt/pip mounts in Dockerfiles; local `.cache/docker-ci/`; digest-label image reuse
* Pins: Actions/runners → version tags only (`ubuntu-26.04`, `@v7.0.1`, …); pip → `==` versions; Docker → `ubuntu:26.04` + `dockerfile:1.26.0`; never SHAs; never a `latest` alias
* PPA image: `docker/Dockerfile.ppa` / `scripts/ppa-docker.sh` (no DE packaging matrix)
* Root clean: Dockerfiles stay under `docker/` ([AGENTS.md](../../AGENTS.md) §4.6.1)

Lint: meson/ninja + clang-tidy + py_compile + `scripts/i18n-lint.py` (JSON + `.po`). Coverage: meson/ninja + pytest floors + meson C++ tests. Compat: meson/ninja + py_compile + pytest without floors + meson C++ tests.
