#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]}")/host-state.sh"

uh_snap_lock

if uh_native_package_owns_host; then
	echo "error: Ubuntu Hello is already installed by a system package; remove that package first." >&2
	exit 1
fi

UH_HOST_INSTALL_MARKER="$(uh_snap_marker)"
export UH_HOST_INSTALL_MARKER
"${SNAP}/usr/share/ubuntu-hello/install-host.sh" "$@"

if [[ "${1:-}" == "--uninstall" ]]; then
	uh_snap_forget_host_copy
fi
