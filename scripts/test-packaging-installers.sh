#!/usr/bin/env bash
# Exercise every packaging installer entrypoint in an isolated test root (no real /usr writes).
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

# Namespace by UH_CI_DE when set so parallel compat-matrix containers
# (all bind-mounting the same host checkout) don't race on one build dir.
BUILD_DIR="$(readlink -mf "${UH_TEST_BUILD_DIR:-build-packaging-installers${UH_CI_DE:+-${UH_CI_DE}}}")"
FAKEROOT="${BUILD_DIR}/fakeroot"
HOST_ROOT="${BUILD_DIR}/host-root"
BUNDLE="${BUILD_DIR}/bundle"
MESON_BUILD="${BUILD_DIR}/meson"

echo "==> Packaging installer tests (build dir: ${BUILD_DIR})"

command -v meson >/dev/null 2>&1 || { echo "error: meson required" >&2; exit 1; }
command -v ninja >/dev/null 2>&1 || { echo "error: ninja required" >&2; exit 1; }

rm -rf "${BUILD_DIR}"
mkdir -p "${FAKEROOT}" "${HOST_ROOT}" "${BUNDLE}/usr/share/ubuntu-hello"

echo "==> Meson build + install to fakeroot"
uh_meson_setup "${MESON_BUILD}" --prefix=/usr
uh_meson_build_install "${MESON_BUILD}" "${FAKEROOT}"

echo "==> Stage portable bundle (AppImage-style usr/ prefix)"
cp -a "${FAKEROOT}/usr/." "${BUNDLE}/usr/"
INSTALL_HOST="${BUNDLE}/usr/share/ubuntu-hello/install-host.sh"
chmod +x "${INSTALL_HOST}"

uh_assert_host_file() {
	local relpath="$1"
	[[ -e "${HOST_ROOT}${relpath}" ]] || {
		echo "error: expected ${HOST_ROOT}${relpath} after install" >&2
		exit 1
	}
}

echo "==> install-host.sh --install (Flatpak/AppImage host installer)"
UH_HOST_ROOT="${HOST_ROOT}" \
	UH_SKIP_CONFIGURE=1 \
	UH_INSTALL_LOG="${BUILD_DIR}/install-host.log" \
	bash "${INSTALL_HOST}" --install

uh_assert_host_file "/usr/bin/ubuntu-hello"
uh_assert_host_file "/usr/share/ubuntu-hello/package-configure.sh"
uh_assert_host_file "/usr/share/ubuntu-hello/package-prerm.sh"
uh_assert_host_file "/usr/share/ubuntu-hello/ubuntu-hello.install"

echo "==> install-host.sh --uninstall"
UH_HOST_ROOT="${HOST_ROOT}" \
	UH_INSTALL_LOG="${BUILD_DIR}/uninstall-host.log" \
	bash "${INSTALL_HOST}" --uninstall

[[ ! -e "${HOST_ROOT}/usr/bin/ubuntu-hello" ]] || {
	echo "error: ubuntu-hello binary still present after uninstall" >&2
	exit 1
}

echo "==> Re-install for configure hook tests"
UH_HOST_ROOT="${HOST_ROOT}" \
	UH_SKIP_CONFIGURE=1 \
	UH_INSTALL_LOG="${BUILD_DIR}/install-host-2.log" \
	bash "${INSTALL_HOST}" --install

echo "==> Debian postinst configure (package-configure dry-run)"
export UH_PACKAGE_CONFIGURE_DRY_RUN=1
(
	# shellcheck source=/dev/null
	. "${HOST_ROOT}/usr/share/ubuntu-hello/package-configure.sh"
	uh_package_configure
)

echo "==> Host configure dry-run with UH_HOST_ROOT (non-live paths)"
export UH_HOST_ROOT="${HOST_ROOT}"
export UH_PACKAGE_CONFIGURE_DRY_RUN=1
(
	# shellcheck source=/dev/null
	. "${HOST_ROOT}/usr/share/ubuntu-hello/package-configure.sh"
	uh_package_configure
)

echo "==> Snap configure hook (package-configure dry-run)"
SNAP="${BUNDLE}" \
	UH_PACKAGE_CONFIGURE_DRY_RUN=1 \
	bash packaging/snap/hooks/configure

echo "==> Snap install-wrapper --install (delegates to install-host.sh)"
UH_HOST_ROOT="${HOST_ROOT}" \
	UH_SKIP_CONFIGURE=1 \
	UH_INSTALL_LOG="${BUILD_DIR}/snap-wrapper.log" \
	SNAP="${BUNDLE}" \
	bash packaging/snap/install-wrapper.sh --install

uh_assert_host_file "/usr/bin/ubuntu-hello"

echo "==> RPM spec post-install hooks are present"
grep -q 'uh_package_configure' packaging/rpm/fedora/ubuntu-hello.spec
grep -q 'uh_package_configure' packaging/rpm/opensuse/ubuntu-hello.spec
grep -q 'uh_package_gtk_onboard' packaging/rpm/fedora/ubuntu-hello.spec

echo "==> Release drivers invoke smoke + E2E verification (CI contract)"
check_yml=".github/workflows/check.yml"
cell="scripts/ci-packaging-cell.sh"
pipeline="scripts/ci-pipeline.sh"
matrix="scripts/ci-packaging-matrix.sh"
# Literal needles (escaped $ so shellcheck does not want expansion / no disable=).
gha_format_arg="\"\${{ matrix.format }}\""
cell_format_arg="\"\${FORMAT}\""
# GHA and local pipeline share the same packaging cell.
grep -q 'ci-packaging-cell.sh' "${check_yml}" \
	|| { echo "error: check.yml must invoke ci-packaging-cell.sh" >&2; exit 1; }
grep -q "ci-packaging-cell.sh ${gha_format_arg}" "${check_yml}" \
	|| { echo "error: check.yml must pass matrix.format to ci-packaging-cell.sh" >&2; exit 1; }
grep -q 'ci-packaging-matrix.sh' "${pipeline}" \
	|| { echo "error: ci-pipeline.sh must run packaging matrix (align with GHA)" >&2; exit 1; }
grep -q 'ci-packaging-cell.sh' "${matrix}" \
	|| { echo "error: ci-packaging-matrix.sh must invoke ci-packaging-cell.sh" >&2; exit 1; }
# Cell: smoke then non-snap E2E with UH_SKIP_ONBOARD=1.
grep -q 'packaging-smoke-verify.sh' "${cell}" \
	|| { echo "error: ci-packaging-cell.sh missing smoke verify" >&2; exit 1; }
grep -q 'packaging-e2e-install.sh' "${cell}" \
	|| { echo "error: ci-packaging-cell.sh missing E2E install" >&2; exit 1; }
grep -q -- '-e UH_SKIP_ONBOARD=1' "${cell}" \
	|| { echo "error: ci-packaging-cell.sh must pass -e UH_SKIP_ONBOARD=1 to E2E docker run" >&2; exit 1; }
grep -q 'KIND}" != "snap"' "${cell}" \
	|| { echo "error: ci-packaging-cell.sh must skip duplicate Snap E2E on outer docker run" >&2; exit 1; }
smoke_line="$(grep -n "packaging-smoke-verify.sh ${cell_format_arg}" "${cell}" | head -n1 | cut -d: -f1)"
e2e_line="$(grep -n "packaging-e2e-install.sh ${cell_format_arg}" "${cell}" | head -n1 | cut -d: -f1)"
[[ -n "${smoke_line}" && -n "${e2e_line}" && "${e2e_line}" -gt "${smoke_line}" ]] \
	|| { echo "error: ci-packaging-cell.sh must run packaging-e2e-install.sh after smoke verify" >&2; exit 1; }
for fmt in deb rpm-fedora rpm-opensuse arch snap appimage flatpak; do
	# check.yml: format: [deb, rpm-fedora, ...]
	grep -qE "format: \\[.*\\b${fmt}\\b" "${check_yml}" \
		|| { echo "error: check.yml missing packaging matrix format ${fmt}" >&2; exit 1; }
	grep -qE "\\b${fmt}\\b" "${matrix}" \
		|| { echo "error: ci-packaging-matrix.sh missing format ${fmt}" >&2; exit 1; }
done
# Snap build is invoked from the shared packaging cell (GHA → cell → ci-snap-build).
grep -q 'ci-snap-build.sh' "${cell}" \
	|| { echo "error: ci-packaging-cell.sh must invoke ci-snap-build.sh for snap" >&2; exit 1; }
grep -q 'packaging-e2e-install.sh snap' scripts/ci-snap-build.sh \
	|| { echo "error: ci-snap-build.sh must run snap E2E" >&2; exit 1; }
grep -q -- '-e UH_SKIP_ONBOARD=1' scripts/ci-snap-build.sh \
	|| { echo "error: ci-snap-build.sh must pass -e UH_SKIP_ONBOARD=1 into Snap E2E docker exec" >&2; exit 1; }
grep -A2 -- '-e UH_SKIP_ONBOARD=1' scripts/ci-snap-build.sh | grep -q 'packaging-e2e-install.sh snap' \
	|| { echo "error: ci-snap-build.sh UH_SKIP_ONBOARD=1 must apply to packaging-e2e-install.sh snap" >&2; exit 1; }

echo "✔ All packaging installer entrypoints completed successfully"
