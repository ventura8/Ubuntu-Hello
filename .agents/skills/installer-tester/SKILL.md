---
name: installer-tester
description: >-
  Validate Ubuntu Hello install.sh / uninstall.sh and related unit tests
  (test_install_download.py). Use when changing the installer, model download
  helpers, or uninstall paths.
---

# Installer Tester

Hello-specific installer validation (not a generic multi-distro e2e suite).

## Scripts

| Script | Role |
|---|---|
| `install.sh` | Root installer: banner shows `v`+`VERSION` (local file or raw GitHub peek for curl\|bash); bootstrap git, fetch source, **full apt deps** via `scripts/uh-apt-deps.sh` (GTK/Babel, multi-DE theme tools, wallet PAM, polkit, build stack), Meson build/install, dlib models, PAM/Polkit, then Wayland-aware setup-wizard launch |
| `scripts/uh-apt-deps.sh` | Shared apt package lists + install/remove helpers; records newly added packages in `/var/lib/ubuntu-hello/apt-packages-added.list`; remove simulates the apt plan and allows Remv of tracked names **plus apt-mark auto transitive deps** (e.g. `libxfconf-0-3` with `xfconf`); refuses untracked *manual* packages; keeps the marker if remove fails. Auto checks use `grep … < <(apt-mark … </dev/null)` (not `apt-mark \| grep -q`) so pipefail SIGPIPE and `while read` stdin steal cannot false-negative auto deps |
| `ubuntu-hello-gtk/bin/run_after_install.py` | Post-install GUI launcher (installed to `/usr/share/ubuntu-hello-gtk/`); called once by `install.sh` / dpkg postinst (not meson). Sets `XDG_RUNTIME_DIR` / `WAYLAND_DISPLAY` / session bus; single-flight lock avoids duplicate wizards; lock/log opens use `O_NOFOLLOW` (reject symlinks); foreign-owned regular leftovers under `/tmp` are replaced so root postinst can still launch |
| `uninstall.sh` | Full removal including tracked apt deps (and pip `dlib` / `face_recognition_models`) |

Host usage (from a clone):

```bash
sudo bash install.sh
sudo bash uninstall.sh
```

After install, the setup wizard should open automatically. Log: `/tmp/ubuntu-hello-postinstall.log`. Manual fallback: `ubuntu-hello-gtk --force-onboarding`.

Remote one-liners (documented in README):

```bash
curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/install.sh | sudo bash
curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/uninstall.sh | sudo bash
```

## Automated tests

```bash
set -euo pipefail
mkdir -p logs
pytest tests/test_install_download.py tests/test_run_after_install.py tests/test_uh_apt_deps.py -v 2>&1 | tee logs/installer-tests.log
```

`tests/test_install_download.py` covers installer config parsing and model download helpers (`TestInstallConfig`, `TestDownloadModels`) without requiring a full privileged system install in CI.

`tests/test_run_after_install.py` covers Wayland/session env detection for the post-install setup-wizard launcher.

`tests/test_uh_apt_deps.py` asserts the shared apt dep lists include Babel, multi-DE theme tools, and wallet PAM packages.
## Notes

* Prefer running installer unit tests inside `./scripts/ci-docker.sh` as part of the normal `tests/` suite when validating PRs
* Do not invent checksum gates or distro matrices this repo does not have
* Agent progress for long install experiments: `logs/` with `tee -a`
