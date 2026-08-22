#!/usr/bin/env bash
# Build Snap, AppImage, and Flatpak release artifacts into artifacts/
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

VERSION="$(uh_read_version)"
uh_prepare_artifacts_dir

MODE="${1:-all}"

build_appimage() {
	echo "==> Building AppImage"
	chmod +x packaging/appimage/build-appimage.sh
	APPIMAGETOOL=/usr/local/bin/appimagetool packaging/appimage/build-appimage.sh
}

build_flatpak() {
	echo "==> Building Flatpak bundle"
	# Stage under artifacts so parallel packaging cells (esp. deb dh_clean)
	# do not race on repo-root build-flatpak* / .flatpak-builder directories.
	local staging="${UH_ARTIFACTS_DIR}/.flatpak-work"
	local repo="${staging}/repo"
	local build_dir="${staging}/build"
	local state_dir="${staging}/state"
	rm -rf "${staging}"
	mkdir -p "${staging}"
	flatpak-builder --state-dir="${state_dir}" --repo="${repo}" --force-clean "${build_dir}" \
		packaging/flatpak/com.github.ventura8.UbuntuHello.yml
	# Bundle name carries this build's own arch (x86_64, aarch64, ...) —
	# matches whatever arch flatpak-builder actually targeted.
	local flatpak_arch
	flatpak_arch="$(uname -m)"
	flatpak build-bundle "${repo}" \
		"${UH_ARTIFACTS_DIR}/com.github.ventura8.UbuntuHello-${VERSION}-${flatpak_arch}.flatpak" \
		com.github.ventura8.UbuntuHello
}

build_snap() {
	echo "==> Building Snap"
	# snapcraft.yaml lives under packaging/snap/, nested inside the repo it
	# packages; pointing its part `source:` at the live tree (../..) makes
	# craft-parts drop the whole packaging/ dir to avoid copying its own
	# build output into itself. Package a clean tarball instead.
	local snap_tarball="packaging/snap/ubuntu-hello-src.tar.gz"
	rm -f "${snap_tarball}"
	uh_create_source_tarball "${VERSION}" "${snap_tarball}"
	cd packaging/snap
	# Stale part state (e.g. from a previous failed run) can hide source or
	# option changes behind snapcraft's "already ran" pull/build cache.
	rm -rf parts stage prime overlay
	snapcraft pack --destructive-mode --output "${UH_ARTIFACTS_DIR}/ubuntu-hello_${VERSION}_amd64.snap"
	cd "${UH_REPO_ROOT}"
	rm -f "${snap_tarball}"
}

case "${MODE}" in
	all)
		build_snap
		build_appimage
		build_flatpak
		;;
	snap) build_snap ;;
	appimage) build_appimage ;;
	flatpak) build_flatpak ;;
	*)
		echo "Usage: $0 [all|snap|appimage|flatpak]" >&2
		exit 1
		;;
esac

ls -la "${UH_ARTIFACTS_DIR}/"
