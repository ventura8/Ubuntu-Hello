#!/usr/bin/env bash
# The apps run from the host copy, whose compiled-in paths are host paths. Before
# that copy exists, say what to do; the desktop launcher can do it after a prompt.
set -euo pipefail

command_name="$1"
shift

if [[ ! -x "/usr/bin/${command_name}" ]]; then
	if [[ "${command_name}" == "ubuntu-hello-gtk" && -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
		pkexec env "SNAP=${SNAP}" UH_SKIP_ONBOARD=1 "${SNAP}/meta/install-wrapper.sh" --install
	else
		echo "Ubuntu Hello is not enabled on this system yet. Run: sudo linux-ubuntu-hello.host-install" >&2
		exit 1
	fi
fi

exec "/usr/bin/${command_name}" "$@"
