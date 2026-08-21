---
name: face-cli
description: >-
  Manage Ubuntu Hello face profiles with the ubuntu-hello CLI (add, list,
  remove, clear) and related admin commands. Use for enrollment and model
  maintenance.
---

# Face Profile Management (CLI)

Use the `ubuntu-hello` wrapper for administrative profile actions (typically as root):

## Enroll

```bash
sudo ubuntu-hello add
sudo ubuntu-hello -y add              # non-interactive (default label; used by SUW / Settings)
```

Follow interactive prompts to save face profile descriptors. Without a TTY (or with `-y`), `add` skips the label prompt and uses the default. Do **not** use `argparse.REMAINDER` for CLI extras — it swallows `-y` after `add` and breaks the setup wizard (`EOFError` on `input()`). Top-level `--all` is for `keyring restore --all`.

## List / remove / clear

```bash
sudo ubuntu-hello list
sudo ubuntu-hello remove <model_id>
sudo ubuntu-hello clear
```

## Related commands

```bash
sudo ubuntu-hello config          # edit /etc/ubuntu-hello/config.ini
sudo ubuntu-hello disable         # disable or re-enable
sudo ubuntu-hello keyring enable  # login keyring / KWallet auto-unlock
sudo ubuntu-hello test            # camera + recognition diagnostics UI
sudo ubuntu-hello snapshot
sudo ubuntu-hello version
```

Models land under `/etc/ubuntu-hello/models/<username>.dat` (JSON + 128-d descriptors). See [docs/architecture/README.md](../../../docs/architecture/README.md).
