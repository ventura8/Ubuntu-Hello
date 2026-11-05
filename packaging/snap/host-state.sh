#!/usr/bin/env bash
# Shared by install-wrapper.sh and the snap hooks: who owns the host copy, and the
# lock that stops a refresh, a remove and a manual run from interleaving.

# Host tools only: the snap's own python3 and libraries must not stand in for them.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

# Records that the host copy belongs to this snap, so the configure hook keeps it
# current across store refreshes and the remove hook can take it away again.
# install-host.sh writes it once dependencies are in, just before it copies files.
uh_snap_marker() {
	printf '%s\n' "${UH_HOST_ROOT:-}/var/lib/ubuntu-hello/.snap-host-install"
}

uh_snap_lock() {
	local lock="${UH_HOST_ROOT:-}/run/lock/ubuntu-hello-snap-host.lock"
	mkdir -p "$(dirname "${lock}")"
	exec 9>"${lock}"
	flock 9
}

# True when a .deb, .rpm or pacman package owns the host paths: this snap must not
# overwrite or delete them.
uh_native_package_owns_host() {
	local path="${UH_HOST_ROOT:-}/usr/bin/ubuntu-hello"
	if command -v dpkg-query >/dev/null 2>&1 && dpkg-query -S "${path}" >/dev/null 2>&1; then
		return 0
	fi
	if command -v rpm >/dev/null 2>&1 && rpm -qf "${path}" >/dev/null 2>&1; then
		return 0
	fi
	command -v pacman >/dev/null 2>&1 && pacman -Qo "${path}" >/dev/null 2>&1
}

# True for a host copy this snap has no marker for (AppImage, Flatpak, source build, or
# data a removed package left behind). Adopting it would let a later `snap remove`
# delete someone else's config and enrolled face models.
uh_unowned_host_copy() {
	local root="${UH_HOST_ROOT:-}"
	[[ ! -f "$(uh_snap_marker)" ]] || return 1
	[[ -e "${root}/usr/bin/ubuntu-hello" || -e "${root}/etc/ubuntu-hello/config.ini" || -d "${root}/etc/ubuntu-hello/models" ]]
}

uh_snap_forget_host_copy() {
	local marker
	marker="$(uh_snap_marker)"
	rm -f "${marker}"
	rmdir --ignore-fail-on-non-empty "$(dirname "${marker}")" 2>/dev/null || true
}
