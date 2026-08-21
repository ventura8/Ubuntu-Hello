#!/usr/bin/env bash
# Build Fedora or openSUSE RPMs into artifacts/
set -euo pipefail

DISTRO="${1:?fedora or opensuse}"
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

VERSION="$(uh_read_version)"
uh_prepare_artifacts_dir

SPEC="packaging/rpm/${DISTRO}/ubuntu-hello.spec"
TARBALL="/tmp/ubuntu-hello-${VERSION}.tar.gz"
RPMTOP="${RPMTOP:-${HOME}/rpmbuild}"

rm -f "${TARBALL}"
uh_create_source_tarball "${VERSION}" "${TARBALL}"

mkdir -p "${RPMTOP}/SOURCES" "${RPMTOP}/SPECS" "${RPMTOP}/RPMS" "${RPMTOP}/SRPMS"
cp "${TARBALL}" "${RPMTOP}/SOURCES/"
cp "${SPEC}" "${RPMTOP}/SPECS/ubuntu-hello.spec"

DIST_ARGS=()
if [[ "${DISTRO}" == "opensuse" ]]; then
	# openSUSE's rpm config doesn't auto-define %dist like Fedora's does —
	# set it explicitly so the built filename carries the expected .lpNNN tag.
	DIST_ARGS=(-D "dist .lp160")
fi

rpmbuild -ba "${RPMTOP}/SPECS/ubuntu-hello.spec" \
	-D "uh_version ${VERSION}" \
	-D "_topdir ${RPMTOP}" \
	"${DIST_ARGS[@]}"

mapfile -t rpms < <(find "${RPMTOP}/RPMS" -name "*ubuntu-hello*${VERSION}*.rpm" -type f)
if ((${#rpms[@]} == 0)); then
	echo "error: no RPM packages produced for version ${VERSION}" >&2
	exit 1
fi
cp -a "${rpms[@]}" "${UH_ARTIFACTS_DIR}/"
ls -la "${UH_ARTIFACTS_DIR}/"
