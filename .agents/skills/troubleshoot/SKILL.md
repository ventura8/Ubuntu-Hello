---
name: troubleshoot
description: >-
  Collect auth.log/journalctl traces, enable config.ini debug knobs, and recover
  from PAM lockout after a broken pam_ubuntu_hello.so. Use for auth failures and
  login lockouts.
---

# Troubleshooting & Log Collection

## System auth logs

```bash
tail -f /var/log/auth.log | grep pam_ubuntu_hello
# Or on journald:
journalctl -f | grep pam_ubuntu_hello
```

## Verbose engine logs

Edit `/etc/ubuntu-hello/config.ini`:

```ini
[debug]
end_report = true        # Log execution latency and model profiles
verbose_stamps = true    # Log rubberstamp plugin execution
gtk_stdout = true        # Pipe GTK stdout to parent terminal
```

## PAM lockout recovery

> [!WARNING]
> A broken C++ PAM module can lock out graphical logins and `sudo`.

1. Switch to a virtual console (e.g. `Ctrl`+`Alt`+`F3`).
2. Log in with a standard password.
3. Edit `/etc/pam.d/common-auth` and comment out the line referencing `pam_ubuntu_hello.so`.

### GDM user-selection → login bounce (known regression)

**Symptom:** On the GDM user list, selecting a user never reaches a stable login/password screen (immediately thrown back to user selection).

**Cause:** PAM forced concurrent `pam_get_authtok` (or a greeter `PAM_CONV` wrapper) while `core.workaround=off`. That aborts the GDM conversation.

**Fix:** Restore workaround-gated `ask_pass` only:

```cpp
const bool ask_pass = ask_auth_tok && workaround != Workaround::Off;
```

Redeploy `pam_ubuntu_hello.so`. Do **not** reintroduce greeter exceptions “to cancel Esc camera”. See [AGENTS.md](../../../AGENTS.md) greeter HARD RULE and [pam-verifier](../pam-verifier/SKILL.md).

## Related behavior

* Lock skip-after-failure markers (legacy `*screensaver*` PAM only): `/run/ubuntu-hello/face-skip/<user>`. GNOME `gdm-password` unlock does not skip — Esc→Enter retries face.
* If greeter login loops back to user selection after selecting a user, treat it as the HARD RULE regression above (or comment out `pam_ubuntu_hello.so` from a TTY to recover).
* Cancel path: PAM SIGTERM/SIGKILL of the compare **process group** (GTK is a child of `compare.py`) — only when a workaround enables concurrent password watch
* See [AGENTS.md](../../../AGENTS.md) lifecycle notes and [docs/SECURITY.md](../../../docs/SECURITY.md)
