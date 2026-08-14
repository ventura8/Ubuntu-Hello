---
name: diagnostics
description: >-
  Dry-run compare.py without PAM and run ubuntu-hello test for camera/recognition
  diagnostics. Use when verifying face matching outside sudo/gdm.
---

# Dry-Run & Diagnostics

## Bypass PAM (compare.py)

Run the face recognition engine without the PAM stack:

```bash
sudo python3 /lib/security/ubuntu-hello/compare.py <target_username>
```

Replace with `/usr/lib/security/ubuntu-hello/compare.py` when that is the installed path.

Exit codes match [AGENTS.md](../../../AGENTS.md) §5 (`0` success, `10` no model, `11` timeout after first usable frame, `12` abort, `13` too dark / no usable frame, `14` bad device, `15` rubberstamp fail). Recognition timeout (`video.timeout`) starts only after the first non-dark frame so IR warm-up does not consume the window.

## Visual camera verification

```bash
sudo ubuntu-hello test
```

Opens a diagnostics window: camera reader, face detection, landmarks, and live confidence.

## Agent logs

For long diagnostic sessions, tee progress into `logs/` (see [logs/README.md](../../../logs/README.md)).
