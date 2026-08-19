#!/usr/bin/env bash
# Build binary .deb packages into artifacts/ (run inside ubuntu:26.04 container).
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

VERSION="$(uh_read_version)"
uh_prepare_artifacts_dir

dpkg-buildpackage -b -us -uc

uh_collect_artifacts_glob \
	"../ubuntu-hello_${VERSION}"*.deb \
	"../ubuntu-hello-gtk_${VERSION}"*.deb
ls -la "${UH_ARTIFACTS_DIR}/"
