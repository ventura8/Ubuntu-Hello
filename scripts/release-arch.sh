#!/usr/bin/env bash
# Build Arch linux packages into artifacts/
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

VERSION="$(uh_read_version)"
uh_prepare_artifacts_dir

PKGDIR="packaging/arch/ubuntu-hello"
TARBALL="/tmp/ubuntu-hello-${VERSION}.tar.gz"
ARCH_BUILD_HOME="/tmp/ubuntu-hello-archbuild"
# Keep makepkg SRCDEST/PKGDEST/BUILDDIR off the bind-mounted repo tree so
# parallel CI cells and re-runs never reuse a half-configured src/build.
ARCH_MAKEPKG_ROOT="/tmp/ubuntu-hello-makepkg-${VERSION}-$$"

rm -f "${TARBALL}"
uh_create_source_tarball "${VERSION}" "${TARBALL}"

mkdir -p "${ARCH_MAKEPKG_ROOT}"
cp "${PKGDIR}/PKGBUILD" "${PKGDIR}/ubuntu-hello.install" "${ARCH_MAKEPKG_ROOT}/"
cp "${TARBALL}" "${ARCH_MAKEPKG_ROOT}/ubuntu-hello-${VERSION}.tar.gz"
# Fresh checksum + concrete pkgver (PKGBUILD's startdir/../../../scripts path
# only works when makepkg runs under packaging/arch/ubuntu-hello/).
TARBALL_SHA256="$(sha256sum "${TARBALL}" | awk '{print $1}')"
sed -i \
	-e "s/^pkgver=.*/pkgver=${VERSION}/" \
	-e "s/^sha256sums=.*/sha256sums=('${TARBALL_SHA256}')/" \
	"${ARCH_MAKEPKG_ROOT}/PKGBUILD"

# Drop leftover makepkg trees under packaging/arch (gitignored but sticky on bind mounts).
rm -rf "${PKGDIR}/src" "${PKGDIR}/pkg"
rm -f "${PKGDIR}/"*.pkg.tar.zst

uh_run_makepkg() {
	local makepkg_env=(
		HOME="${ARCH_BUILD_HOME}"
		BUILDDIR="${ARCH_MAKEPKG_ROOT}/build"
		SRCDEST="${ARCH_MAKEPKG_ROOT}"
		PACKAGE_DIR="${ARCH_MAKEPKG_ROOT}"
		PKGDEST="${ARCH_MAKEPKG_ROOT}"
	)
	mkdir -p "${ARCH_MAKEPKG_ROOT}/build" "${ARCH_BUILD_HOME}"
	if [[ "$(id -u)" -eq 0 ]] && id builduser &>/dev/null; then
		chown -R builduser:builduser "${ARCH_BUILD_HOME}" "${ARCH_MAKEPKG_ROOT}"
		runuser -u builduser -- env "${makepkg_env[@]}" \
			bash -c "cd \"${ARCH_MAKEPKG_ROOT}\" && makepkg -sr --noconfirm --skippgpcheck"
	else
		env "${makepkg_env[@]}" bash -c "cd \"${ARCH_MAKEPKG_ROOT}\" && makepkg -sr --noconfirm --skippgpcheck"
	fi
}

uh_run_makepkg

uh_collect_artifacts_glob "${ARCH_MAKEPKG_ROOT}/*${VERSION}*.pkg.tar.zst"
# Also copy into PKGDIR for local convenience (optional).
cp -a "${ARCH_MAKEPKG_ROOT}/"*.pkg.tar.zst "${PKGDIR}/" 2>/dev/null || true
ls -la "${UH_ARTIFACTS_DIR}/"
