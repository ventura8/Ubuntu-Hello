---
name: pam-verifier
description: >-
  Verify Ubuntu Hello PAM integrity behaviors: face-skip markers, process-group
  cancel/cleanup, AES-GCM UH1 / keyring PAM helpers, and related C++/Python
  tests. Use when changing pam/ or auth lifecycle.
---

# PAM Verifier

Verify integrity of `pam_ubuntu_hello.so` auth lifecycle: face-skip markers, process-group cancel, UH1/keyring helpers, and related tests.

## HARD RULE — never regress greeter login (mandatory)

> [!CAUTION]
> Forcing concurrent `pam_get_authtok` on greeters when `core.workaround=off` **breaks GDM**: selecting a user on the greeter immediately returns to the user list (login screen never sticks).

**Canonical gate in `ubuntu-hello/src/pam/main.cc`:**

```cpp
const bool ask_pass = ask_auth_tok && workaround != Workaround::Off;
```

| Allowed | Forbidden |
|---|---|
| `workaround=input` / `native` → concurrent password + cancel | `ask_pass \|\| is_greeter_service(...)` with `workaround=off` |
| Process-group SIGTERM / `PR_SET_PDEATHSIG` cleanup | Custom greeter `PAM_CONV` wrappers to “defer camera until prompt” |
| Screensaver-only face-skip (`*screensaver*`) | Using Esc→shield camera bugs as reason to force greeter `pam_get_authtok` |

Also see [AGENTS.md](../../../AGENTS.md) lifecycle CAUTION and [troubleshoot](../troubleshoot/SKILL.md) lockout recovery.

## Behaviors to preserve

1. **Process group cancel**: PAM spawns `compare.py` with `POSIX_SPAWN_SETPGROUP`. On password-first cancel (**only when a workaround enables `ask_pass`**), signal the **group** (`SIGTERM` → wait → `SIGKILL`). GTK is a child of compare; PAM must not orphan the overlay.
2. **SIGTERM cleanup**: `compare.py` handlers release the camera, terminate GTK, then `os._exit(12)` (atexit is not relied on for SIGTERM). Prefer `PR_SET_PDEATHSIG` so orphans die if the PAM worker exits.
3. **Greeter safety**: Concurrent password watch stays **workaround-gated only** (see HARD RULE above).
4. **Face-skip**: Legacy `*screensaver*` PAM services set `/run/ubuntu-hello/face-skip/<user>` after a completed non-zero compare; next identify returns `PAM_AUTHINFO_UNAVAIL`. Cleared on success / `pam_sm_setcred`. **GNOME lock (`gdm-password`) does not skip** so Esc→Enter retries face. Config: `core.skip_face_after_failure`.
5. **Keyring AUTHTOK**: After face success, PAM sets `PAM_AUTHTOK` for `pam_gnome_keyring` / `pam_kwallet5`. Software blobs use `UH1:` AES-GCM (`aes_gcm_uh1.*`, `keyring_crypto.py`).
6. **Session-idle self-abort (best-effort, does not cover GNOME/GDM's Esc)**: `compare.py`'s own `_watch_session_idle` daemon thread polls its login1 session's `IdleHint` via `busctl` and, only on an observed `false → true` transition (edge-triggered — an already-idle first reading, e.g. waking a lock screen, must not abort the normal case), sends the process `SIGTERM` so the existing main-thread `_signal_exit` handler does cleanup (never call `cleanup()` directly from the watcher thread — it would race the main thread's concurrent camera/GTK access). Does not touch `ask_pass`/PAM-level watching, so it is the sanctioned way to react to session-idle changes — do not reach for the forbidden `ask_pass` widening instead. **Live-verified on GNOME/GDM this does not actually fire on Esc-at-lock-screen** (`IdleHint` never transitions there); see the `[!NOTE]` in `AGENTS.md` lifecycle notes for the D-Bus investigation. Config: `core.abort_on_session_idle`.

Sources: `ubuntu-hello/src/pam/` (`main.cc`, `face_skip.*`, `aes_gcm_uh1.*`, `enter_device.*`), `ubuntu-hello/src/compare.py`.

## Run related tests

```bash
set -euo pipefail
mkdir -p logs

# Prefer Docker cell (matches CI)
UH_CI_DE=baseline ./scripts/ci-docker.sh 2>&1 | tee logs/pam-verifier-ci.log

# Or host Meson tests after a local build:
# meson test -C builddir pam-aes-gcm-uh1 --print-errorlogs --verbose
# meson test -C builddir pam-face-skip --print-errorlogs --verbose
# pytest tests/test_compare_cleanup.py tests/test_keyring_crypto.py -v
```

| Test | Focus |
|---|---|
| `tests/pam_face_skip_test.cc` | Face-skip marker paths |
| `tests/pam_aes_gcm_uh1_test.cc` | UH1 AES-GCM (meson `pam-aes-gcm-uh1`) |
| `tests/test_compare_cleanup.py` | Camera/GTK cleanup on abort |
| `tests/test_keyring_crypto.py` / `test_cli_keyring_aes.py` | Software keyring path |

**Before finishing any PAM greeter/Esc change:** confirm `ask_pass` is still workaround-gated only (`rg 'ask_pass' ubuntu-hello/src/pam/main.cc`).

See [AGENTS.md](../../../AGENTS.md) lifecycle notes and [docs/SECURITY.md](../../../docs/SECURITY.md).
