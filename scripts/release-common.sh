#!/usr/bin/env bash
# Shared helpers for release packaging scripts.
set -euo pipefail

UH_REPO_ROOT="${UH_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UH_ARTIFACTS_DIR="${UH_ARTIFACTS_DIR:-${UH_REPO_ROOT}/artifacts}"

uh_read_version() {
	python3 "${UH_REPO_ROOT}/scripts/read-version.py"
}

uh_meson_setup() {
	local build_dir="$1"
	shift
	local -a meson_opts=(
		-Ddlib_data_dir=/etc/ubuntu-hello/dlib-data
		-Dconfig_dir=/etc/ubuntu-hello
		-Duser_models_dir=/etc/ubuntu-hello/models
		-Dinstall_pam_config=true
		-Dwith_polkit=true
		-Dfetch_dlib_data=false
	)
	# Subproject option only when falling back to the inih wrap. Distros with
	# a system INIReader (Arch libinih, Fedora inih, …) reject unknown
	# -Dinih:with_INIReader=true and break packaging.
	if ! pkg-config --exists INIReader 2>/dev/null; then
		meson_opts+=(-Dinih:with_INIReader=true)
	fi
	# Stale builddirs from prior packaging cells cause "already configured".
	if [[ -d "${build_dir}" ]]; then
		rm -rf "${build_dir}"
	fi
	meson setup "${build_dir}" "${UH_REPO_ROOT}" "${meson_opts[@]}" "$@"
}

uh_meson_build_install() {
	local build_dir="$1"
	local destdir="$2"
	ninja -C "${build_dir}"
	DESTDIR="${destdir}" ninja -C "${build_dir}" install
}

uh_prepare_artifacts_dir() {
	mkdir -p "${UH_ARTIFACTS_DIR}"
}

# Source tarball for RPM/Arch/Snap. Pack the working tree (not HEAD via git-archive) so
# packaging CI/GHA exercise uncommitted packaging fixes on the bind mount.
# Excludes build/cache leftovers the same way .gitignore / .dockerignore do.
# Parallel packaging cells share /src; GNU tar exits 1 if a file changes mid-read —
# treat that as a soft warning (still require a complete extract).
uh_create_source_tarball() {
	local version="$1"
	local dest="$2"
	local tmp stage root="${UH_REPO_ROOT}"
	local tar_out extract_rc
	# Always stage under /tmp — never next to dest (e.g. packaging/snap/), or
	# leftover dirs poison the Docker build context (permission denied on COPY).
	tmp="$(mktemp "/tmp/ubuntu-hello-${version}.tar.gz.XXXXXX")"
	stage="$(mktemp -d "/tmp/uh-src-${version}.XXXXXX")"
	mkdir -p "${stage}/ubuntu-hello-${version}"
	set +e
	tar -C "${root}" \
		--warning=no-file-changed \
		--exclude='./.git' \
		--exclude='./build' \
		--exclude='./build-*' \
		--exclude='./builddir' \
		--exclude='./builddir-*' \
		--exclude='./obj-*' \
		--exclude='./artifacts' \
		--exclude='./logs' \
		--exclude='./.cache' \
		--exclude='./.flatpak-builder' \
		--exclude='./debian/tmp' \
		--exclude='./debian/.debhelper' \
		--exclude='./debian/ubuntu-hello' \
		--exclude='./debian/ubuntu-hello-gtk' \
		--exclude='./debian/*.substvars' \
		--exclude='./debian/files' \
		--exclude='./packaging/snap/parts' \
		--exclude='./packaging/snap/stage' \
		--exclude='./packaging/snap/prime' \
		--exclude='./packaging/snap/overlay' \
		--exclude='./packaging/snap/.craft' \
		--exclude='./packaging/snap/.uh-src-*' \
		--exclude='./packaging/arch/ubuntu-hello/src' \
		--exclude='./packaging/arch/ubuntu-hello/pkg' \
		--exclude='./__pycache__' \
		--exclude='./.pytest_cache' \
		--exclude='*.snap' \
		-cf - . \
		| tar -C "${stage}/ubuntu-hello-${version}" -xf -
	# bash 5.3 leaves PIPESTATUS[1] unset (not merely empty) under `set -u`
	# after some tar exit codes; ${...:-1} avoids the nounset error while
	# still treating a missing status as a failure.
	tar_out=${PIPESTATUS[0]:-1}
	extract_rc=${PIPESTATUS[1]:-1}
	set -e
	# GNU tar: 0=ok, 1=some files changed/differed (ok under parallel cells), ≥2=fatal.
	if [[ "${extract_rc}" -ge 2 ]] || [[ "${tar_out}" -ge 2 ]]; then
		rm -rf "${stage}"
		rm -f "${tmp}"
		return 1
	fi
	if ! tar -C "${stage}" -czf "${tmp}" "ubuntu-hello-${version}"; then
		rm -rf "${stage}"
		rm -f "${tmp}"
		return 1
	fi
	rm -rf "${stage}"
	mv "${tmp}" "${dest}"
}

# Copy files matching globs into UH_ARTIFACTS_DIR (version-filtered release outputs).
uh_collect_artifacts_glob() {
	local copied=0
	local pattern match
	local had_nullglob=0
	shopt -q nullglob && had_nullglob=1
	shopt -s nullglob
	for pattern in "$@"; do
		for match in ${pattern}; do
			[[ -e "$match" ]] || continue
			cp -a "$match" "${UH_ARTIFACTS_DIR}/"
			copied=1
		done
	done
	if [[ "${had_nullglob}" -eq 0 ]]; then
		shopt -u nullglob
	fi
	if [[ "${copied}" -eq 0 ]]; then
		echo "error: no artifacts matched: $*" >&2
		return 1
	fi
}

# Copy paths listed in a debian .install file from fakeroot into pkgdir.
# Lines are relative to / (e.g. usr/bin/ubuntu-hello). Glob segments (*)
# are expanded against fakeroot. Fails when a literal path or glob matches nothing.
uh_split_install_from_list() {
	local fakeroot="$1"
	local pkgdir="$2"
	local list_file="$3"
	local line relpath
	local had_error=0
	while IFS= read -r line || [[ -n "$line" ]]; do
		line="${line%%#*}"
		line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
		[[ -z "$line" ]] && continue
		relpath="${line#./}"
		if [[ "$relpath" == *"*"* ]]; then
			local pattern="${fakeroot}/${relpath}"
			local matches=()
			shopt -s nullglob
			mapfile -t matches < <(compgen -G "${pattern}" || true)
			shopt -u nullglob
			# Distros with a flat lib/ (no multiarch subdir, e.g. Arch) never
			# match a usr/lib/*/X pattern; retry collapsed to usr/lib/X.
			if ((${#matches[@]} == 0)) && [[ "$relpath" == usr/lib/\*/* ]]; then
				local fallback="${fakeroot}/usr/lib/${relpath#usr/lib/*/}"
				[[ -e "$fallback" ]] && matches=("$fallback")
			fi
			if ((${#matches[@]} == 0)); then
				echo "error: install list glob matched nothing: ${relpath} (in ${list_file})" >&2
				had_error=1
				continue
			fi
			local match dst
			for match in "${matches[@]}"; do
				[[ -e "$match" ]] || continue
				dst="${pkgdir}${match#"${fakeroot}"}"
				mkdir -p "$(dirname "$dst")"
				cp -a "$match" "$dst"
			done
		else
			local src="${fakeroot}/${relpath}"
			local dst="${pkgdir}/${relpath}"
			if [[ ! -e "$src" ]]; then
				echo "error: install list path missing: ${relpath} (in ${list_file})" >&2
				had_error=1
				continue
			fi
			mkdir -p "$(dirname "$dst")"
			cp -a "$src" "$dst"
		fi
	done <"$list_file"
	if [[ "${had_error}" -ne 0 ]]; then
		return 1
	fi
}
