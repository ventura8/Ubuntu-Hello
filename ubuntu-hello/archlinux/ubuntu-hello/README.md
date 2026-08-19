# Arch Linux packaging moved to packaging/arch/

Canonical PKGBUILD (split `ubuntu-hello` + `ubuntu-hello-gtk`):

- [`packaging/arch/ubuntu-hello/PKGBUILD`](../../../packaging/arch/ubuntu-hello/PKGBUILD)

Build locally:

```bash
cd packaging/arch/ubuntu-hello
makepkg -sr --noconfirm
```

Release CI builds via `scripts/release-arch.sh`.
