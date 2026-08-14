# Shared apt dependency lists for Ubuntu Hello install.sh / uninstall.sh.
# Sourced from a clone (scripts/uh-apt-deps.sh). Do not execute directly.
#
# Covers build + runtime needs for GNOME, KDE/Plasma, XFCE, Cinnamon, MATE,
# Budgie, and LXQt on Ubuntu 26.04 (theme probes, GTK Settings, wallet PAM).

# Packages we installed that were not already present (one name per line).
UH_APT_MARKER="${UH_APT_MARKER:-/var/lib/ubuntu-hello/apt-packages-added.list}"

# Never remove these even if recorded (base OS / shared tooling).
UH_APT_NEVER_REMOVE=(
	python3
	python3-minimal
	ca-certificates
	apt
	dpkg
	bash
	coreutils
)

# Build / compile (meson, PAM C++, dlib pip build).
UH_APT_BUILD_DEPS=(
	python3-dev
	python3-setuptools
	python3-wheel
	python3-pip
	cmake
	make
	build-essential
	g++
	gettext
	libpam0g-dev
	libinih-dev
	libevdev-dev
	libopencv-dev
	libssl-dev
	libboost-all-dev
	pkg-config
	meson
	ninja-build
	libopenblas-dev
	liblapack-dev
	git
)

# Runtime: core engine + GTK UI + multi-DE probes + wallet PAM + polkit.
UH_APT_RUNTIME_DEPS=(
	python3
	python3-numpy
	python3-opencv
	python3-cryptography
	python3-babel
	python3-gi
	python3-gi-cairo
	gir1.2-gtk-3.0
	curl
	wget
	bzip2
	v4l-utils
	tpm2-tools
	# Theme probes (GNOME/Budgie/Cinnamon/MATE use gsettings+dconf; XFCE xfconf;
	# Plasma kreadconfig6; LXQt reads config files — no extra package).
	dconf-cli
	libglib2.0-bin
	xfconf
	libkf6config-bin
	# Login wallet unlock for GNOME Keyring and KWallet (all supported DEs).
	libpam-gnome-keyring
	libpam-kwallet5
	# Settings elevation
	pkexec
	polkitd
)

uh_apt_is_installed() {
	local pkg="$1"
	# </dev/null: callers may be inside `while read … done <file|<<<`; do not steal stdin.
	dpkg-query -W -f='${Status}' "$pkg" </dev/null 2>/dev/null | grep -q "install ok installed"
}

uh_apt_never_remove() {
	local pkg="$1"
	local n
	for n in "${UH_APT_NEVER_REMOVE[@]}"; do
		if [ "$n" = "$pkg" ]; then
			return 0
		fi
	done
	return 1
}

uh_apt_unique_packages() {
	# Print unique package names from BUILD + RUNTIME (stable order).
	local -A seen=()
	local p
	for p in "${UH_APT_BUILD_DEPS[@]}" "${UH_APT_RUNTIME_DEPS[@]}"; do
		if [ -z "${seen[$p]:-}" ]; then
			seen[$p]=1
			printf '%s\n' "$p"
		fi
	done
}

uh_apt_record_added() {
	# Append package names that this installer introduced (dedupe).
	if [ "$#" -eq 0 ]; then
		return 0
	fi
	mkdir -p "$(dirname "$UH_APT_MARKER")"
	local existing=""
	if [ -f "$UH_APT_MARKER" ]; then
		existing=$(cat "$UH_APT_MARKER")
	fi
	{
		printf '%s\n' "$existing"
		printf '%s\n' "$@"
	} | awk 'NF && !seen[$0]++' >"${UH_APT_MARKER}.tmp"
	mv "${UH_APT_MARKER}.tmp" "$UH_APT_MARKER"
}

uh_apt_install_all() {
	# Install every UH apt dependency; record packages that were not present.
	local -a missing=()
	local -a all=()
	local p
	mapfile -t all < <(uh_apt_unique_packages)
	for p in "${all[@]}"; do
		if ! uh_apt_is_installed "$p"; then
			missing+=("$p")
		fi
	done

	export DEBIAN_FRONTEND=noninteractive
	apt-get update -qq
	apt-get install -y -qq "${all[@]}" 2>&1 | tail -5

	if [ "${#missing[@]}" -gt 0 ]; then
		uh_apt_record_added "${missing[@]}"
	fi
}

uh_apt_is_auto_installed() {
	local pkg="$1"
	# Match one package name from apt-mark showauto.
	# Do not use `apt-mark | grep -q` under pipefail: an early grep exit
	# SIGPIPEs apt-mark (status 141) and false-negatives real auto packages.
	# </dev/null: callers may be inside `while read … done <<<"$planned"`.
	grep -qxF -- "$pkg" < <(apt-mark showauto </dev/null 2>/dev/null)
}

uh_apt_planned_removals() {
	# Print package names apt-get would remove for the given packages (simulate).
	# Strips architecture suffixes (pkg:amd64 → pkg).
	if [ "$#" -eq 0 ]; then
		return 0
	fi
	export DEBIAN_FRONTEND=noninteractive
	apt-get -s remove -y "$@" 2>/dev/null \
		| sed -n 's/^Remv[[:space:]]\{1,\}\([^[:space:]]*\).*/\1/p' \
		| sed 's/:.*//' \
		| awk 'NF && !seen[$0]++'
}

uh_apt_remove_exact() {
	# Simulate then remove the listed packages; autoremove leftovers.
	# Allows Remv of tracked names and of apt-mark "auto" dependencies pulled
	# in with them. Refuses Remv of untracked *manual* packages (user installs).
	# Does not touch UH_APT_MARKER. Returns non-zero on plan/remove failure.
	if [ "$#" -eq 0 ]; then
		return 0
	fi
	local -a pkgs=("$@")
	local -A allowed=()
	local p planned
	for p in "${pkgs[@]}"; do
		allowed["$p"]=1
	done

	export DEBIAN_FRONTEND=noninteractive
	planned=$(uh_apt_planned_removals "${pkgs[@]}") || return 1
	while IFS= read -r p || [ -n "$p" ]; do
		[ -z "$p" ] && continue
		if [ -n "${allowed[$p]:-}" ]; then
			continue
		fi
		if uh_apt_never_remove "$p"; then
			echo "Refusing apt remove: protected package '$p' would also be removed" >&2
			return 1
		fi
		# Transitive auto deps of tracked packages (e.g. libxfconf-0-3 with xfconf).
		if uh_apt_is_auto_installed "$p"; then
			continue
		fi
		echo "Refusing apt remove: untracked manual package '$p' would also be removed" >&2
		return 1
	done <<<"$planned"

	apt-get remove -y -qq "${pkgs[@]}" 2>&1 | tail -5
	local remove_rc=${PIPESTATUS[0]}
	if [ "$remove_rc" -ne 0 ]; then
		return "$remove_rc"
	fi
	apt-get autoremove -y -qq 2>&1 | tail -3
	local auto_rc=${PIPESTATUS[0]}
	return "$auto_rc"
}

uh_apt_clear_marker() {
	rm -f "$UH_APT_MARKER"
	rmdir "$(dirname "$UH_APT_MARKER")" 2>/dev/null || true
}

uh_apt_remove_tracked() {
	# Remove packages recorded at install time (skip never-remove / still needed).
	# Keeps the marker if plan validation or removal fails so a later retry works.
	if [ ! -f "$UH_APT_MARKER" ]; then
		return 0
	fi
	local -a to_remove=()
	local p
	while IFS= read -r p || [ -n "$p" ]; do
		p="${p%%#*}"
		p="$(echo "$p" | tr -d '[:space:]')"
		[ -z "$p" ] && continue
		uh_apt_never_remove "$p" && continue
		uh_apt_is_installed "$p" || continue
		to_remove+=("$p")
	done <"$UH_APT_MARKER"

	if [ "${#to_remove[@]}" -eq 0 ]; then
		uh_apt_clear_marker
		return 0
	fi

	if ! uh_apt_remove_exact "${to_remove[@]}"; then
		return 1
	fi
	uh_apt_clear_marker
	return 0
}
