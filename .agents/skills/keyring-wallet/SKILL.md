---
name: keyring-wallet
description: >-
  Configure login keyring / KWallet auto-unlock via PAM_AUTHTOK, wallet_backend
  labels, and UH1 AES-GCM / TPM sealed credentials. Use for keyring CLI, PAM
  helpers, or wallet UX copy.
---

# Keyring / KWallet Auto-Unlock

Face auth unlocks the login wallet by setting **`PAM_AUTHTOK`** after a successful match. Downstream PAM modules consume that token:

* **`pam_gnome_keyring`** — GNOME and most Ubuntu-family DEs
* **`pam_kwallet5`** — KDE Plasma / KWallet

There is **one** sealed credential path (no separate KWallet blob format). Packaging **Depends** on both `libpam-gnome-keyring` and `libpam-kwallet5` so GNOME Keyring and KWallet unlock work across supported DEs.

## Agent-facing helpers

| Path | Role |
|---|---|
| `ubuntu-hello/src/wallet_backend.py` | Labels backend `gnome-keyring`, `kwallet`, or `none` from desktop env |
| `ubuntu-hello/src/keyring_crypto.py` | Software `UH1:` AES-256-GCM helpers |
| `ubuntu-hello/src/cli/keyring.py` | `ubuntu-hello keyring enable/disable` |
| `ubuntu-hello/src/pam/aes_gcm_uh1.*` | C++ UH1 decrypt for PAM |
| `ubuntu-hello-gtk/src/tab_keyring.py` | Settings UI |

User-facing copy (CLI, GTK, onboarding): **login keyring or KWallet**.

## Commands

```bash
sudo ubuntu-hello keyring enable
sudo ubuntu-hello keyring disable
```

## Security notes

* TPM sealing when available; else `UH1:` + master key under `/etc/ubuntu-hello/` (`0600` / dir `0700`)
* Legacy XOR/`machine-id` blobs are not decrypted on face auth; re-enable upgrades to `UH1:`
* Details: [docs/SECURITY.md](../../../docs/SECURITY.md)

## Tests

`tests/test_keyring_crypto.py`, `tests/test_cli_keyring_aes.py`, `tests/test_wallet_backend.py`, `tests/pam_aes_gcm_uh1_test.cc` (meson test `pam-aes-gcm-uh1`).
