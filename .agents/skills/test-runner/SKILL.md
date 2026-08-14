---
name: test-runner
description: >-
  Run Ubuntu Hello pytest suite and PAM C++ meson tests the way this repo
  gates them (host or via UH_CI_STAGE=coverage / compat). Use for unit/coverage
  checks.
---

# Test Runner

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
  tests/test_gtk_tabs.py tests/test_onboarding.py \
  --cov=keyring_crypto --cov=cli.keyring --cov=tab_keyring \
  --cov-branch --cov-report=term-missing --cov-fail-under=100 \
  2>&1 | tee -a logs/pytest.log
```

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
| `tests/test_install_download.py` | `install.sh` download helpers |
| `tests/test_authsticky_window.py` | Overlay UI |

Always tee long runs to `logs/` (Hello convention), not other log roots.

Full gate (lint + coverage + matrix): [pipeline-runner](../pipeline-runner/SKILL.md).
