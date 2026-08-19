---
name: release-packaging
description: >-
  Build Ubuntu Hello release artifacts locally (.deb, Fedora/openSUSE RPM, Arch,
  Snap, AppImage, Flatpak) via Docker release images and scripts/release-*.sh.
  Use when testing packaging before a tag or debugging release.yml failures.
---

# Release packaging

Build all GitHub Release artifact families locally before pushing a `v*` tag.

## Prerequisites

- Docker with BuildKit
- Repo checkout at `/src` or project root
- Optional PPA secrets only for signed source upload (`GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`)

## Artifact output

All drivers write to **`artifacts/`** (gitignored). Tag releases upload these via
[`.github/workflows/release.yml`](../../../.github/workflows/release.yml).

| Format | Script / Docker | Output example |
|--------|-----------------|----------------|
| Debian | `docker/Dockerfile.ppa` + `scripts/release-deb.sh` | `ubuntu-hello_*_amd64.deb` |
| Fedora RPM | `docker/Dockerfile.rpm.fedora` + `scripts/release-rpm.sh fedora` | `ubuntu-hello-*-1.fc44.x86_64.rpm` |
| openSUSE RPM | `docker/Dockerfile.rpm.opensuse` + `scripts/release-rpm.sh opensuse` | `ubuntu-hello-*-1.lp160.x86_64.rpm` |
| Arch | `docker/Dockerfile.arch` + `scripts/release-arch.sh` | `*.pkg.tar.zst` (split packages) |
| Snap / AppImage / Flatpak | `docker/Dockerfile.snap` (Snap) / `docker/Dockerfile.release` + `scripts/release-portable.sh` | `.snap`, `.AppImage`, `.flatpak` |

## Parallel packaging cells

Local `ci-packaging-matrix.sh` and GHA run all seven formats concurrently against a shared bind-mounted tree. Deb packaging overrides `dh_clean` so it does not `find` the whole tree (sibling cells may create/remove `build-*`). Flatpak stages under `${UH_ARTIFACTS_DIR}/.flatpak-work` instead of repo-root `build-flatpak*`.

## Quick commands

```bash
# Debian
docker build -f docker/Dockerfile.ppa -t ubuntu-hello-ppa:26.04 .
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-ppa:26.04 ./scripts/release-deb.sh

# Fedora RPM
docker build -f docker/Dockerfile.rpm.fedora -t ubuntu-hello-rpm-fedora:44 .
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-rpm-fedora:44 ./scripts/release-rpm.sh fedora

# openSUSE RPM
docker build -f docker/Dockerfile.rpm.opensuse -t ubuntu-hello-rpm-opensuse:16.0 .
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-rpm-opensuse:16.0 ./scripts/release-rpm.sh opensuse

# Arch
docker build -f docker/Dockerfile.arch -t ubuntu-hello-arch:base-devel-20260816.0.574111 .
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-arch:base-devel-20260816.0.574111 ./scripts/release-arch.sh

# Portable formats (AppImage + Flatpak in release image; Snap in Ubuntu 24.04 image — core24 base)
docker build -f docker/Dockerfile.release -t ubuntu-hello-release:26.04 .
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-release:26.04 ./scripts/release-portable.sh appimage
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-release:26.04 ./scripts/release-portable.sh flatpak

# Snap (core24 cannot build on Ubuntu 26.04 hosts — use docker/Dockerfile.snap on 24.04)
# Revision SSOT: packaging/snap/SNAPCRAFT_REVISION (installed by docker/snap-entrypoint.sh)
# snapd manages snap mounts via systemd units, so the container boots systemd as
# PID 1 and scripts/ci-snap-build.sh drives the build via `docker exec` once
# systemd reports ready — a plain entrypoint script as PID 1 cannot run snapd.
./scripts/ci-snap-build.sh
```

**Security:** `--privileged` combined with a read-write checkout mount grants broad
host access. Use only with trusted source trees. For narrower scope, build one format
at a time without `--privileged` where possible:

```bash
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-release:26.04 ./scripts/release-portable.sh appimage
docker run --rm -v "$PWD:/src:rw" -w /src ubuntu-hello-release:26.04 ./scripts/release-portable.sh flatpak
./scripts/ci-snap-build.sh
```

Version strings for artifact names come from repo-root [`VERSION`](../../../VERSION) via `scripts/read-version.py`.

## Smoke verification

After each local build, confirm expected artifacts exist:

```bash
./scripts/packaging-smoke-verify.sh deb          # or rpm-fedora, rpm-opensuse, arch, snap, appimage, flatpak
```

Then run a **live** install → **upgrade** (config preserve) → remove → reinstall E2E inside the same format image
(PAM + `config.ini` restore; setup wizard skipped via `UH_SKIP_ONBOARD=1`).

**Debian example** (`ubuntu-hello-ppa:26.04` + `deb`):

```bash
docker run --rm -e UH_SKIP_ONBOARD=1 -v "$PWD:/src:rw" -w /src ubuntu-hello-ppa:26.04 \
  ./scripts/packaging-e2e-install.sh deb
```

Same pattern for other formats (image + `packaging-e2e-install.sh` argument):

| Format | Image | Command |
|--------|-------|---------|
| `deb` | `ubuntu-hello-ppa:26.04` | `./scripts/packaging-e2e-install.sh deb` |
| `rpm-fedora` | `ubuntu-hello-rpm-fedora:44` | `./scripts/packaging-e2e-install.sh rpm-fedora` |
| `rpm-opensuse` | `ubuntu-hello-rpm-opensuse:16.0` | `./scripts/packaging-e2e-install.sh rpm-opensuse` |
| `arch` | `ubuntu-hello-arch:base-devel-20260816.0.574111` | `./scripts/packaging-e2e-install.sh arch` |
| `appimage` | `ubuntu-hello-release:26.04` (use `--privileged`) | `./scripts/packaging-e2e-install.sh appimage` |
| `flatpak` | `ubuntu-hello-release:26.04` (`--privileged --device /dev/fuse`) | `./scripts/packaging-e2e-install.sh flatpak` |
| `snap` | (systemd snap container) | included in `./scripts/ci-snap-build.sh` after the build (`UH_SKIP_ONBOARD=1`) |

CI runs smoke verify + E2E after every packaging matrix cell via [`scripts/ci-packaging-cell.sh`](../../../scripts/ci-packaging-cell.sh) (GHA [`check.yml`](../../../.github/workflows/check.yml) and local [`ci-pipeline.sh`](../../../scripts/ci-pipeline.sh) / [`ci-packaging-matrix.sh`](../../../scripts/ci-packaging-matrix.sh)). E2E cycle: install → in-place upgrade (live `config.ini` marker must survive) → remove → reinstall (fresh template, marker gone).

## Installer integration tests (coverage CI)

[`scripts/test-packaging-installers.sh`](../../../scripts/test-packaging-installers.sh) builds a Meson
DESTDIR tree and exercises every installer entrypoint in an isolated host root:

| Format | Entrypoint exercised |
|--------|---------------------|
| AppImage / Flatpak | `install-host.sh --install` / `--uninstall` |
| Debian | `package-configure.sh` (postinst equivalent, dry-run) |
| Snap | `hooks/configure` + `install-wrapper.sh` |
| RPM | `%post` hooks present in Fedora/openSUSE specs |

Run via pytest: `tests/test_packaging_release.py::test_packaging_installers_complete` (requires meson + ninja).

## Shared configure scripts

Post-install logic is centralized:

- [`scripts/package-configure.sh`](../../../scripts/package-configure.sh) — models, permissions, config restore, **ensure dlib** (pip pin when missing; `CMAKE_POLICY_VERSION_MINIMUM=3.5` for CMake 4+), PAM, polkit
- [`scripts/package-gtk-onboard.sh`](../../../scripts/package-gtk-onboard.sh)
- [`scripts/package-prerm.sh`](../../../scripts/package-prerm.sh) — removes pip-installed dlib when the configure marker is present

Installed to `/usr/share/ubuntu-hello/` by Meson. Debian postinst sources them;
RPM `%post`, Snap configure hook, and AppImage/Flatpak `install-host.sh` call the same helpers.

## Metadata paths

| Path | Role |
|------|------|
| [`packaging/arch/ubuntu-hello/PKGBUILD`](../../../packaging/arch/ubuntu-hello/PKGBUILD) | Split Arch packages |
| [`packaging/rpm/fedora/ubuntu-hello.spec`](../../../packaging/rpm/fedora/ubuntu-hello.spec) | Fedora RPM |
| [`packaging/rpm/opensuse/ubuntu-hello.spec`](../../../packaging/rpm/opensuse/ubuntu-hello.spec) | openSUSE RPM |
| [`packaging/snap/snapcraft.yaml`](../../../packaging/snap/snapcraft.yaml) | Classic Snap (`--libdir=lib/x86_64-linux-gnu` so host-install globs match) |
| [`packaging/appimage/build-appimage.sh`](../../../packaging/appimage/build-appimage.sh) | Installer AppImage |
| [`packaging/flatpak/com.github.ventura8.UbuntuHello.yml`](../../../packaging/flatpak/com.github.ventura8.UbuntuHello.yml) | Flatpak manifest |

## CI smoke (PRs)

[`check.yml`](../../../.github/workflows/check.yml) `packaging` matrix: `deb`, `rpm-fedora`, `rpm-opensuse`, `arch`, `snap`, `appimage`, `flatpak` (fork PRs skipped). Each cell builds artifacts, runs `packaging-smoke-verify.sh`, then live E2E install/upgrade/remove/reinstall (`packaging-e2e-install.sh`; Snap via `ci-snap-build.sh`).

Full multi-format gate runs on tag push via `release.yml`.

## AppImage / Flatpak host install

Portable artifacts bundle Meson output but **PAM requires a one-time elevated
install** on the host:

```bash
chmod +x Ubuntu-Hello-*-x86_64.AppImage
./Ubuntu-Hello-*-x86_64.AppImage --install

flatpak install --user ./com.github.ventura8.UbuntuHello-*.flatpak
flatpak run --command=ubuntu-hello-host-install com.github.ventura8.UbuntuHello
flatpak run com.github.ventura8.UbuntuHello
```

Uses polkit + [`packaging/flatpak/install-host.sh`](../../../packaging/flatpak/install-host.sh).
