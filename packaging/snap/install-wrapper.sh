#!/usr/bin/env bash
set -euo pipefail

# Records that the host copy belongs to this snap, so the configure hook keeps it
# current across store refreshes and the remove hook can take it away again.
marker="${UH_HOST_ROOT:-}/var/lib/ubuntu-hello/.snap-host-install"

# Host tools only: the snap's own python3 and libraries must not stand in for them.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

"${SNAP}/usr/share/ubuntu-hello/install-host.sh" "$@"

case "${1:-}" in
	--install)
		mkdir -p "$(dirname "${marker}")"
		touch "${marker}"
		;;
	--uninstall)
		rm -f "${marker}"
		rmdir --ignore-fail-on-non-empty "$(dirname "${marker}")" 2>/dev/null || true
		;;
esac
