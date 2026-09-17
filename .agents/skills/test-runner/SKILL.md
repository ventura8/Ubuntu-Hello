---
name: test-runner
description: >-
  Run Ubuntu Hello pytest suite and PAM C++ meson tests the way this repo
  gates them (host or via UH_CI_STAGE=coverage / compat). Use for unit/coverage
  checks.
---

# Test Runner

When adding or changing code, run applicable tests in the same change set ([AGENTS.md](../../../AGENTS.md) §4.5).

## Preferred: Docker coverage stage (matches GHA coverage job)

```bash
set -euo pipefail
mkdir -p logs
UH_CI_STAGE=coverage ./scripts/ci-docker.sh 2>&1 | tee logs/ci-coverage.log
```

That runs meson/ninja, pytest (≥ 90% coverage), keyring 100% coverage, and `meson test pam-aes-gcm-uh1`.

## Compat DE cell (no coverage floors)

```bash
UH_CI_STAGE=compat UH_CI_DE=baseline ./scripts/ci-docker.sh 2>&1 | tee logs/ci-baseline.log
```

## Host pytest (when deps are installed)

```bash
set -euo pipefail
mkdir -p logs
pytest --cov=ubuntu-hello-gtk --cov=ubuntu-hello --cov-fail-under=90 tests/ \
  2>&1 | tee logs/pytest.log
```

Keyring feature gate (as in CI coverage stage):

```bash
pytest tests/test_keyring_crypto.py tests/test_cli_keyring_aes.py \
  tests/test_keyring_restore.py tests/test_gtk_tabs.py tests/test_onboarding.py \
  --cov=keyring_crypto --cov=keyring_restore --cov=cli.keyring --cov=tab_keyring \
  --cov-branch --cov-report=term-missing --cov-fail-under=100 \
  2>&1 | tee -a logs/pytest.log
```

## Recorded-footage tier (the real recognition loop)

The unit suite mocks dlib and OpenCV; the real-GTK suite still mocks dlib. This tier
is the only automated coverage of the code that decides who gets in: `compare.py`'s
scan loop, the agreeing-frames gate and the nod rubber stamp, run on frames recorded
from the real camera.

```bash
UH_REAL_DLIB=1 pytest tests/footage/           # ~15 s on signals, ~30 s on pixels
```

Fixtures, in order of preference (`tests/footage/conftest.py` picks automatically):

| where | what | committed |
|---|---|---|
| `tests/fixtures/footage/<clip>.npz` | pixels from `tests/footage/record.py` | never: it is a face |
| `tests/fixtures/footage/<clip>.signals.npz` | per-frame dark/face/landmarks/descriptor from `deidentify.py` | never: a biometric template |
| `tests/fixtures/footage-synthetic/` | random signals with the measured shape, from `synthesize.py` | yes: what CI runs |

To refresh real fixtures on a laptop with the app installed:

```bash
sudo UH_SRC=/usr/lib/x86_64-linux-gnu/ubuntu-hello python3 tests/footage/record.py
UH_REAL_DLIB=1 python3 tests/footage/deidentify.py tests/fixtures/footage/*.npz
shred -u tests/fixtures/footage/*.npz          # keep only the signals
```

Pixel clips need the real dlib and its model files; signal clips need neither
(the conftest provides a stand-in `dlib` module when the library is absent, which
is the CI case). The calibration test is about a real camera and skips on the
synthetic set.

## Booted-OS tier (QEMU/KVM virtual machine)

Containers cannot host PAM as a login path, the polkit helper's systemd sandbox, a
TPM, reboot persistence, or a package install/upgrade/remove on a real root. Every
defect that only manual testing found during v1.2.0 lived in one of those layers
(TPM unseal refused under the polkit sandbox, catalogs left behind by uninstall,
the runtime dir needed at boot). This tier boots a clean cloud image -- Ubuntu,
Fedora or Arch -- under QEMU/KVM with an emulated TPM (swtpm) and runs scenario
scripts inside it over SSH.

```bash
sudo apt install qemu-system-x86 qemu-utils swtpm swtpm-tools cloud-image-utils   # once
dpkg-buildpackage -b -us -uc && mkdir -p .cache/vm/pkgs-ubuntu && mv ../ubuntu-hello*_amd64.deb .cache/vm/pkgs-ubuntu/
UH_VM=1 UH_VM_PKG_DIR=$PWD/.cache/vm/pkgs-ubuntu pytest tests/vm/   # ~20 min: boots, installs the tree's packages, asserts
UH_VM=1 pytest tests/vm/                     # same, but installs the PPA's published build instead
scripts/vm/vm.sh run [install ...]           # the driver alone; results in .cache/vm/results/<distro>/
UH_VM_KEEP=1 scripts/vm/vm.sh run install && UH_VM_REUSE=1 scripts/vm/vm.sh run pam-sudo
scripts/vm/vm.sh shell                       # poke at the guest (user `tester`, passwordless sudo)
# Fedora and Arch: packages from the packaging cells, then the same tier
UH_PACKAGING_ARTIFACTS_ISOLATE=1 scripts/ci-packaging-cell.sh rpm-fedora   # -> artifacts/ci-packaging/rpm-fedora
UH_VM_DISTRO=fedora UH_VM_PKG_DIR=$PWD/artifacts/ci-packaging/rpm-fedora scripts/vm/vm.sh run
UH_VM_DISTRO=arch   UH_VM_PKG_DIR=$PWD/artifacts/ci-packaging/arch scripts/vm/vm.sh run
UH_VM=1 UH_VM_DISTROS="ubuntu fedora arch" UH_VM_PKG_DIR=<dir with one subdir per distro> pytest tests/vm/
```

Prefer `UH_VM_PKG_DIR`: it tests the working tree. Without it the tier installs whatever
the PPA last published, which is a different question (and is how the first run found
that the published 1.2.0 package failed to install on a clean 26.04 at all). Fedora and
Arch have no PPA, so for them it is required. The scenarios are written once; what
differs per distro (package manager, lib dir, how the PAM stack includes the module,
who wires PAM: the package on Debian/Fedora, the user on Arch) lives in `scripts/vm/lib.sh`.

| scenario | what only a booted OS can prove |
|---|---|
| `00-install` | install on a clean root: PAM line, polkit drop-in, tmpfiles rule, catalogs, dlib built, `/run/ubuntu-hello` |
| `10-config-migration` | reinstall keeps hand edits, adds `confirmations` at the user's level, never tightens a hand-tuned threshold |
| `20-pam-sudo` | `sudo` for a user with a model runs the module; no camera fails closed to the password fast; wrong password still refused; caller env never inherited; host syslog identity kept |
| `25-greeter-pam` | the greeter HARD RULE: under `gdm-password` with `workaround = off` the module never prompts itself (`scripts/vm/pam_probe.py` runs the real PAM stack and counts prompts) |
| `30-polkit-sandbox` | the same TPM unseal fails under polkit's hardening without the drop-in and succeeds with it |
| `40-reboot` | after a real reboot: `/run/ubuntu-hello` recreated by tmpfiles.d, PAM intact, polkit socket up |
| `90-remove` | purge leaves nothing: no files, no PAM line, no drop-in, no catalogs, no crash report, login still works |
| `95-upgrade-from-previous` | a real upgrade from the previous GitHub release over a hand-edited config: edits and models kept, the new key added, a half-configured old install repaired |

Each scenario prints one JSON object (`{"checks": {...}, "info": {...}}`); `tests/vm/` asserts
on it, so a failure names the check and shows the info. Logs: `.cache/vm/results/<distro>/*.log`,
guest console `.cache/vm/<distro>-console.log`. `UH_VM_RESULTS=<dir>` re-asserts an old run without
booting. The guest has two users: `tester` (key login, passwordless sudo, drives the run)
and `alice` (password `alice-pass`, sudo with password: the PAM scenarios log in as her).

In CI a matrix job per distro builds the packages from the commit in the matching
packaging cell first and the tier installs those. It is slow, so it runs on release branches and on demand
(`workflow_dispatch`). No real face is involved anywhere: recognition itself is covered
by `tests/footage/`.

## PAM C++ tests (Meson)

After `meson setup` / `ninja`:

```bash
meson test -C builddir pam-aes-gcm-uh1 --print-errorlogs --verbose
meson test -C builddir pam-face-skip --print-errorlogs --verbose
```

Sources: `tests/pam_aes_gcm_uh1_test.cc`, `tests/pam_face_skip_test.cc` (wired in `ubuntu-hello/src/pam/meson.build`).

## Notable Python tests

| Module | Focus |
|---|---|
| `tests/test_compare_cleanup.py` | SIGTERM / GTK cleanup |
| `tests/test_theme_detect.py` | Multi-DE theme probes |
| `tests/test_wallet_backend.py` | Wallet labels |
| `tests/test_i18n_lint.py` | JSON + gettext catalog lint; `test_all_translations_filled` fails on empty/fuzzy `.po` or fill-pack gaps vs `.pot` (`scripts/i18n-lint.py`) |
| `tests/test_config_ensure.py` | Restore missing `config.ini` after apt remove/reinstall |

Always tee long runs to `logs/` (Hello convention), not other log roots.

Full gate (lint + coverage + matrix): [pipeline-runner](../pipeline-runner/SKILL.md).
