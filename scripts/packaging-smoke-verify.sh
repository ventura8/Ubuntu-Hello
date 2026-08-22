#!/usr/bin/env bash
# Verify release packaging smoke outputs exist under artifacts/.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

FORMAT="${1:?format: deb|rpm-fedora|rpm-opensuse|arch|snap|appimage|flatpak}"
VERSION="$(uh_read_version)"
ART="${UH_ARTIFACTS_DIR:-artifacts}"
# rpmbuild tags the built RPM with the container's own arch (x86_64, aarch64, ...) —
# match whatever this cell is actually running as, not a hardcoded x86_64.
RPM_ARCH="$(uname -m)"

case "${FORMAT}" in
	deb)
		compgen -G "${ART}/ubuntu-hello_${VERSION}*_*.deb" >/dev/null
		compgen -G "${ART}/ubuntu-hello-gtk_${VERSION}*_*.deb" >/dev/null
		;;
	rpm-fedora)
		compgen -G "${ART}/ubuntu-hello-${VERSION}-*.fc*.${RPM_ARCH}.rpm" >/dev/null
		compgen -G "${ART}/ubuntu-hello-gtk-${VERSION}-*.fc*.${RPM_ARCH}.rpm" >/dev/null
		;;
	rpm-opensuse)
		compgen -G "${ART}/ubuntu-hello-${VERSION}-*.lp*.${RPM_ARCH}.rpm" >/dev/null
		compgen -G "${ART}/ubuntu-hello-gtk-${VERSION}-*.lp*.${RPM_ARCH}.rpm" >/dev/null
		;;
	arch)
		compgen -G "${ART}/ubuntu-hello-${VERSION}-*.pkg.tar.zst" >/dev/null
		compgen -G "${ART}/ubuntu-hello-gtk-${VERSION}-*.pkg.tar.zst" >/dev/null
		;;
	snap)
		compgen -G "${ART}/ubuntu-hello_${VERSION}_*.snap" >/dev/null
		;;
	appimage)
		compgen -G "${ART}/Ubuntu-Hello-${VERSION}-*.AppImage" >/dev/null
		;;
	flatpak)
		compgen -G "${ART}/com.github.ventura8.UbuntuHello-${VERSION}-*.flatpak" >/dev/null
		;;
	*)
		echo "Unknown format: ${FORMAT}" >&2
		exit 1
		;;
esac

echo "✔ ${FORMAT} artifacts verified for version ${VERSION}"
ls -la "${ART}/"
