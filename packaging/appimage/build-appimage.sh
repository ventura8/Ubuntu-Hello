#!/usr/bin/env bash
# Build an installer-style AppImage bundling the Meson install tree.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/../../scripts/release-common.sh"

VERSION="$(uh_read_version)"
APPDIR="${UH_REPO_ROOT}/build-appimage/AppDir"
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"
# appimagetool tags the ARCH env var into the output filename; match whatever
# arch this build is actually running as (x86_64, aarch64, ...).
ARCH="$(uname -m)"
OUTPUT="${UH_ARTIFACTS_DIR}/Ubuntu-Hello-${VERSION}-${ARCH}.AppImage"

uh_prepare_artifacts_dir
rm -rf "${UH_REPO_ROOT}/build-appimage"
mkdir -p "${APPDIR}/usr"

uh_meson_setup "${UH_REPO_ROOT}/build-appimage/meson-build" --prefix=/usr
uh_meson_build_install "${UH_REPO_ROOT}/build-appimage/meson-build" "${APPDIR}"

cat >"${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
INSTALL_SH="${HERE}/usr/share/ubuntu-hello/install-host.sh"
case "${1:-}" in
  --install|--uninstall)
    exec pkexec "${INSTALL_SH}" "$@"
    ;;
  --help|-h)
    echo "Usage: $0 [--install|--uninstall|--help]"
    echo "  --install    Install Ubuntu Hello to the host system (requires polkit)"
    echo "  --uninstall  Remove Ubuntu Hello from the host system"
    exit 0
    ;;
  *)
    echo "Ubuntu Hello AppImage — run with --install to install to the system." >&2
    exec "${HERE}/usr/bin/ubuntu-hello-gtk" "$@"
    ;;
esac
EOF
chmod 755 "${APPDIR}/AppRun"

# appimagetool requires the desktop file and its icon directly at the AppDir
# root (usr/share/applications/... alone isn't enough).
cp "${APPDIR}/usr/share/applications/ubuntu-hello-gtk.desktop" "${APPDIR}/ubuntu-hello-gtk.desktop"
cp "${APPDIR}/usr/share/pixmaps/ubuntu-hello-gtk.png" "${APPDIR}/ubuntu-hello-gtk.png"

if ! command -v "${APPIMAGETOOL}" &>/dev/null; then
  echo "ERROR: appimagetool not found (set APPIMAGETOOL=...)" >&2
  exit 1
fi

ARCH="${ARCH}" "${APPIMAGETOOL}" "${APPDIR}" "${OUTPUT}"
ls -la "${OUTPUT}"
echo "Built AppImage: ${OUTPUT}"
