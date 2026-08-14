---
name: meson-build
description: >-
  Configure, compile, and install Ubuntu Hello with Meson/Ninja, or uninstall
  with uninstall.sh. Use when building from source, refreshing PAM/GTK installs,
  or cleaning builddirs.
---

# Meson Build & Install

Ubuntu Hello uses the **Meson** build system.

## Build and install

```bash
rm -rf builddir/

meson setup builddir -Dprefix=/usr -Dsysconfdir=/etc -Dlibdir=lib \
  -Dinstall_pam_config=true -Dwith_polkit=true -Dfetch_dlib_data=true \
  -Dinih:with_INIReader=true

meson compile -C builddir
sudo meson install -C builddir
```

Shorter local configure (option defaults from `meson.options`):

```bash
meson setup build
meson compile -C build
sudo meson install -C build
```

## Uninstall

```bash
sudo bash uninstall.sh
```

## Notes

* PAM C++ sources: `ubuntu-hello/src/pam/`
* GTK / Python packages install via the same Meson tree (`ubuntu-hello/`, `ubuntu-hello-gtk/`)
* For Docker CI builds (g++, clang-tidy, pytest), prefer [ci-docker-matrix](../ci-docker-matrix/SKILL.md) / [pipeline-runner](../pipeline-runner/SKILL.md) instead of host install
* Agent progress: append under `logs/` with `tee -a` when runs are long
