#!/usr/bin/env bash
# Ensure pinned snapcraft and a fresh apt cache inside Ubuntu 24.04 (core24
# snap builds). Run under a container whose PID 1 is real systemd — snapd is
# already up via systemd socket activation by the time this runs (see
# docker/Dockerfile.snap and scripts/ci-snap-build.sh).
set -euo pipefail

for _ in $(seq 1 60); do
	if snap version >/dev/null 2>&1; then
		break
	fi
	sleep 1
done

if ! snap list snapcraft >/dev/null 2>&1; then
	REV="$(cat /etc/ubuntu-hello/SNAPCRAFT_REVISION)"
	snap install snapcraft --classic --revision="${REV}"
fi

# snapcraft --destructive-mode resolves build-packages via apt; the image
# drops the apt cache to stay small, so refresh it before building.
apt-get update -qq

export PATH="/snap/bin:${PATH}"
exec "$@"
