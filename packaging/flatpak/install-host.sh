#!/usr/bin/env bash
# Copy bundled prefix onto the host system, then run package-configure.
# Bundle root is derived from this script's install path — never from the environment.
set -euo pipefail

UH_INSTALL_LOG="${UH_INSTALL_LOG:-/var/log/ubuntu-hello-host-install.log}"
UH_HOST_POLICY_NAME="com.github.ventura8.UbuntuHello.host.policy"

uh_host_path() {
	local relpath="$1"
	if [[ -n "${UH_HOST_ROOT:-}" ]]; then
		printf '%s%s\n' "${UH_HOST_ROOT}" "$relpath"
	else
		printf '%s\n' "$relpath"
	fi
}

uh_log() {
	echo "$*" | tee -a "$UH_INSTALL_LOG"
}

uh_fatal() {
	echo "error: $*" >&2
	exit 1
}

# Derive DESTDIR root (.../usr/share/ubuntu-hello -> bundle or /).
uh_resolve_destdir_root() {
	local script share_dir share_parent prefix_root
	script="$(readlink -f "${BASH_SOURCE[0]}")"
	share_dir="$(dirname "$script")"
	if [[ "$(basename "$share_dir")" != "ubuntu-hello" ]]; then
		uh_fatal "unexpected install-host.sh location: ${script}"
	fi
	share_parent="$(readlink -f "${share_dir}/..")"
	if [[ "$(basename "$share_parent")" != "share" ]]; then
		uh_fatal "install-host.sh must live under .../share/ubuntu-hello/"
	fi
	prefix_root="$(readlink -f "${share_parent}/..")"
	if [[ "$(basename "$prefix_root")" == "usr" ]]; then
		readlink -f "${prefix_root}/.."
		return 0
	fi
	printf '%s\n' "$prefix_root"
}

uh_assert_under_root() {
	local path="$1" root="$2"
	local canonical root_canonical
	canonical="$(readlink -f "$path")"
	root_canonical="$(readlink -f "$root")"
	[[ -n "$canonical" && -n "$root_canonical" ]] || uh_fatal "missing path under bundle root"
	[[ "$canonical" == "${root_canonical}"/* || "$canonical" == "$root_canonical" ]] \
		|| uh_fatal "refusing path outside bundle: ${path}"
}

uh_safe_copy_file() {
	local src="$1" dst="$2" root="$3"
	local mode
	uh_assert_under_root "$src" "$root"
	if [[ -L "$src" ]]; then
		uh_fatal "refusing symlink source: ${src}"
	fi
	# Preserve the user's live config.ini across reinstall/update instead of
	# clobbering it with the bundle's default (package-configure.sh's
	# uh_restore_config_ini already recreates it from the datadir template
	# when genuinely missing).
	if [[ "$(basename "$dst")" == "config.ini" && "$dst" == "$(uh_host_path /etc/ubuntu-hello/config.ini)" && -e "$dst" ]]; then
		return 0
	fi
	mode="$(stat -c '%a' "$src")"
	install -D -m "$mode" -- "$src" "$dst"
}

uh_copy_tree_under_root() {
	local src="$1" dst="$2" root="$3"
	local item rel
	uh_assert_under_root "$src" "$root"
	[[ -d "$src" ]] || uh_fatal "not a directory: ${src}"
	[[ -L "$src" ]] && uh_fatal "refusing symlink directory: ${src}"
	mkdir -p "$dst"
	while IFS= read -r -d '' item; do
		if [[ -L "$item" ]]; then
			uh_fatal "refusing symlink in tree: ${item}"
		fi
		rel="${item#"${src}/"}"
		if [[ -d "$item" ]]; then
			uh_copy_tree_under_root "$item" "${dst}/${rel}" "$root"
		else
			mkdir -p "$(dirname "${dst}/${rel}")"
			uh_safe_copy_file "$item" "${dst}/${rel}" "$root"
		fi
	done < <(find "$src" -mindepth 1 -print0)
}

uh_install_from_list() {
	local prefix="$1" list_file="$2"
	local line relpath bundle_flat=0
	# Manifest entries are host-relative (e.g. usr/bin/foo). Meson installs
	# with --prefix=/usr (AppImage/RPM/deb DESTDIR bundles) or --prefix=/app
	# (Flatpak, which has no nested usr/ tree) — normalize accordingly.
	[[ -d "${prefix}/usr" ]] || bundle_flat=1
	while IFS= read -r line || [[ -n "$line" ]]; do
		line="${line%%#*}"
		line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
		[[ -z "$line" ]] && continue
		relpath="${line#./}"
		relpath="${relpath%/}"
		local src_relpath="$relpath"
		if [[ "$bundle_flat" -eq 1 && "$relpath" == usr/* ]]; then
			src_relpath="${relpath#usr/}"
		fi
		if [[ "$relpath" == *"*"* ]]; then
			local pattern="${prefix}/${src_relpath}" src dst host_relpath matched=0
			shopt -s nullglob
			for src in ${pattern}; do
				matched=1
				host_relpath="${src#"${prefix}/"}"
				if [[ "$bundle_flat" -eq 1 && "$relpath" == usr/* ]]; then
					host_relpath="usr/${host_relpath}"
				fi
				dst="$(uh_host_path "/${host_relpath}")"
				if [[ -d "$src" ]]; then
					uh_copy_tree_under_root "$src" "$dst" "$prefix"
				else
					uh_safe_copy_file "$src" "$dst" "$prefix"
				fi
			done
			# Flatpak/some distros use a flat lib/ (no multiarch subdir);
			# retry lib/*/X (or usr/lib/*/X when bundle_flat=0) patterns
			# collapsed to lib/X (usr/lib/X) when unmatched.
			local unprefixed_relpath="${src_relpath#usr/}"
			if [[ "$matched" -eq 0 && "$unprefixed_relpath" == lib/\*/* ]]; then
				local fallback_relpath="lib/${unprefixed_relpath#lib/*/}"
				[[ "$src_relpath" == usr/* ]] && fallback_relpath="usr/${fallback_relpath}"
				src="${prefix}/${fallback_relpath}"
				if [[ -e "$src" ]]; then
					host_relpath="$fallback_relpath"
					if [[ "$bundle_flat" -eq 1 && "$relpath" == usr/* ]]; then
						host_relpath="usr/${host_relpath}"
					fi
					dst="$(uh_host_path "/${host_relpath}")"
					if [[ -d "$src" ]]; then
						uh_copy_tree_under_root "$src" "$dst" "$prefix"
					else
						uh_safe_copy_file "$src" "$dst" "$prefix"
					fi
				fi
			fi
			shopt -u nullglob
		else
			local src="${prefix}/${src_relpath}"
			local dst
			dst="$(uh_host_path "/${relpath}")"
			if [[ -e "$src" ]]; then
				if [[ -d "$src" ]]; then
					uh_copy_tree_under_root "$src" "$dst" "$prefix"
				else
					uh_safe_copy_file "$src" "$dst" "$prefix"
				fi
			else
				: # not present in this bundle (e.g. runtime-only dirs) — skip
			fi
		fi
	done <"$list_file"
}

uh_safe_remove() {
	local target="$1"
	local root canonical rel
	root="$(readlink -f "$(uh_host_path "/")" 2>/dev/null || uh_host_path "/")"
	root="${root%/}"
	canonical="$(readlink -f "$target" 2>/dev/null || printf '%s' "$target")"
	if [[ -z "$canonical" || "$canonical" == "${root:-/}" || "$canonical" == "/" ]]; then
		uh_log "refusing removal of unsafe path: ${target}"
		return 0
	fi
	rel="${canonical#"${root}"/}"
	if [[ "$rel" == "$canonical" || "$rel" != */* ]]; then
		uh_log "refusing removal of unsafe path: ${target}"
		return 0
	fi
	rm -rf "$target" 2>/dev/null || true
}

uh_remove_from_list() {
	local prefix="$1" list_file="$2"
	local line relpath
	while IFS= read -r line || [[ -n "$line" ]]; do
		line="${line%%#*}"
		line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
		[[ -z "$line" ]] && continue
		relpath="${line#./}"
		relpath="${relpath%/}"
		if [[ "$relpath" == *"*"* ]]; then
			local host_pattern match matched=0
			host_pattern="$(uh_host_path "/${relpath}")"
			shopt -s nullglob
			for match in ${host_pattern}; do
				matched=1
				uh_safe_remove "$match"
			done
			# Flatpak/some distros use a flat lib/ (no multiarch subdir);
			# retry usr/lib/*/X patterns collapsed to usr/lib/X when unmatched.
			if [[ "$matched" -eq 0 && "$relpath" == usr/lib/\*/* ]]; then
				match="$(uh_host_path "/usr/lib/${relpath#usr/lib/*/}")"
				[[ -e "$match" ]] && uh_safe_remove "$match"
			fi
			shopt -u nullglob
		else
			uh_safe_remove "$(uh_host_path "/${relpath}")"
		fi
	done <"$list_file"
}

uh_install_tree() {
	local destdir_root="$1"
	local prefix list_dir
	prefix="$(readlink -f "$destdir_root")"
	list_dir="$(readlink -f "$(dirname "${BASH_SOURCE[0]}")")"
	[[ -f "${list_dir}/ubuntu-hello.install" ]] || uh_fatal "missing ubuntu-hello.install beside install-host.sh"
	uh_install_from_list "$prefix" "${list_dir}/ubuntu-hello.install"
	if [[ -f "${list_dir}/ubuntu-hello-gtk.install" ]]; then
		uh_install_from_list "$prefix" "${list_dir}/ubuntu-hello-gtk.install"
	fi
}

uh_install_host_polkit() {
	local list_dir policy_src policy_dst
	list_dir="$(readlink -f "$(dirname "${BASH_SOURCE[0]}")")"
	policy_src="${list_dir}/${UH_HOST_POLICY_NAME}"
	policy_dst="$(uh_host_path "/usr/share/polkit-1/actions/${UH_HOST_POLICY_NAME}")"
	if [[ -f "$policy_src" ]]; then
		install -D -m 644 -- "$policy_src" "$policy_dst"
	fi
}

uh_remove_host_polkit() {
	rm -f "$(uh_host_path "/usr/share/polkit-1/actions/${UH_HOST_POLICY_NAME}")" 2>/dev/null || true
	rm -f "$(uh_host_path "/usr/share/polkit-1/actions/com.github.ventura8.UbuntuHello.install.policy")" 2>/dev/null || true
}

uh_host_install() {
	local destdir_root
	destdir_root="$(uh_resolve_destdir_root)"
	uh_log ">>> Installing Ubuntu Hello to host from ${destdir_root}"
	uh_install_tree "$destdir_root"
	uh_install_host_polkit
	if [[ "${UH_SKIP_CONFIGURE:-0}" != "1" ]]; then
		local configure_sh gtk_onboard_sh
		configure_sh="$(uh_host_path "/usr/share/ubuntu-hello/package-configure.sh")"
		gtk_onboard_sh="$(uh_host_path "/usr/share/ubuntu-hello/package-gtk-onboard.sh")"
		export UH_HOST_ROOT="${UH_HOST_ROOT:-}"
		if [[ -f "$configure_sh" ]]; then
			# shellcheck source=/dev/null
			. "$configure_sh"
			uh_package_configure
		fi
		if [[ -f "$gtk_onboard_sh" ]]; then
			# shellcheck source=/dev/null
			. "$gtk_onboard_sh"
			uh_package_gtk_onboard "host-install"
		fi
	fi
	uh_log ">>> Host install complete"
}

uh_host_uninstall() {
	uh_log ">>> Removing Ubuntu Hello from host"
	local list_dir prerm_sh
	list_dir="$(readlink -f "$(dirname "${BASH_SOURCE[0]}")")"
	prerm_sh="$(uh_host_path "/usr/share/ubuntu-hello/package-prerm.sh")"
	if [[ -f "$prerm_sh" ]]; then
		# shellcheck source=/dev/null
		. "$prerm_sh"
		uh_package_prerm
	fi
	if [[ -f "${list_dir}/ubuntu-hello-gtk.install" ]]; then
		uh_remove_from_list "/" "${list_dir}/ubuntu-hello-gtk.install"
	fi
	if [[ -f "${list_dir}/ubuntu-hello.install" ]]; then
		uh_remove_from_list "/" "${list_dir}/ubuntu-hello.install"
	fi
	uh_remove_host_polkit
	rm -rf "$(uh_host_path /etc/ubuntu-hello)" 2>/dev/null || true
	uh_log ">>> Host uninstall complete"
}

case "${1:-}" in
	--install) uh_host_install ;;
	--uninstall) uh_host_uninstall ;;
	*)
		echo "Usage: $0 --install|--uninstall" >&2
		exit 1
		;;
esac
