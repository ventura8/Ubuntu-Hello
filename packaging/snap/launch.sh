#!/usr/bin/env bash
# The apps run from the host copy, whose compiled-in paths are host paths. Before
# that copy exists, say what to do. Store policy disallows pkexec from a snap, so
# this must not try to elevate; the desktop launcher has no terminal, hence the notification.
set -euo pipefail

command_name="$1"
shift

if [[ ! -x "/usr/bin/${command_name}" ]]; then
	message="Ubuntu Hello is not enabled on this system yet. Run: sudo linux-ubuntu-hello.host-install"
	echo "${message}" >&2
	if command -v notify-send >/dev/null 2>&1; then
		notify-send "Ubuntu Hello" "${message}" || true
	fi
	exit 1
fi

exec "/usr/bin/${command_name}" "$@"
