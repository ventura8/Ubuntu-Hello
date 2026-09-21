#!/usr/bin/env bash
# Idempotent post-install configuration shared by deb/rpm/snap/AppImage/Flatpak.
# Safe to run multiple times. Best-effort: warnings instead of hard failures.
set -euo pipefail

uh_pkg_path() {
	local relpath="$1"
	if [[ -n "${UH_HOST_ROOT:-}" ]]; then
		printf '%s%s\n' "${UH_HOST_ROOT}" "$relpath"
	else
		printf '%s\n' "$relpath"
	fi
}

uh_configure_targets_live_host() {
	[[ -z "${UH_HOST_ROOT:-}" || "${UH_HOST_ROOT}" == "/" ]]
}

uh_verify_model_archive() {
	local archive="$1"
	local expected="$2"
	local actual
	actual="$(sha256sum "$archive" | awk '{print $1}')"
	if [[ "${actual}" != "${expected}" ]]; then
		rm -f "$archive"
		return 1
	fi
	return 0
}

uh_download_models() {
	echo ">>> Downloading face recognition models..."
	local models_dir
	models_dir="$(uh_pkg_path "/etc/ubuntu-hello/dlib-data")"
	mkdir -p "$models_dir"

	declare -A model_sha256=(
		[dlib_face_recognition_resnet_model_v1.dat.bz2]=abb1f61041e434465855ce81c2bd546e830d28bcbed8d27ffbe5bb408b11553a
		[mmod_human_face_detector.dat.bz2]=db9e9e40f092c118d5eb3e643935b216838170793559515541c56a2b50d9fc84
		[shape_predictor_5_face_landmarks.dat.bz2]=6e787bbebf5c9efdb793f6cd1f023230c4413306605f24f299f12869f95aa472
	)

	local models=(
		"dlib_face_recognition_resnet_model_v1.dat"
		"mmod_human_face_detector.dat"
		"shape_predictor_5_face_landmarks.dat"
	)
	local base_url="https://github.com/davisking/dlib-models/raw/master"

	local model archive bz2 tmp
	for model in "${models[@]}"; do
		if [ -f "$models_dir/$model" ]; then
			echo "  $model already exists, skipping."
			continue
		fi
		echo "  Downloading $model..."
		bz2="${model}.bz2"
		archive="$models_dir/${bz2}"
		tmp="$(mktemp "${models_dir}/.${model}.XXXXXX")"
		if command -v wget &>/dev/null; then
			wget -q --tries=5 -O "$tmp" "${base_url}/${bz2}" || true
		elif command -v curl &>/dev/null; then
			curl -fsSL --retry 5 -o "$tmp" "${base_url}/${bz2}" || true
		fi
		if [ ! -s "$tmp" ]; then
			rm -f "$tmp"
			echo "  WARNING: Failed to download $model (need curl or wget; check network)"
			continue
		fi
		if ! uh_verify_model_archive "$tmp" "${model_sha256[$bz2]}"; then
			rm -f "$tmp"
			echo "  WARNING: Checksum mismatch for $model"
			continue
		fi
		mv "$tmp" "$archive"
		if bunzip2 -f "$archive" && echo "  ✔ $model"; then
			:
		else
			echo "  WARNING: Failed to extract $model"
		fi
	done
}

uh_set_permissions() {
	local etc_hello models_dir tpm_keys log_dir
	etc_hello="$(uh_pkg_path "/etc/ubuntu-hello")"
	models_dir="$(uh_pkg_path "/etc/ubuntu-hello/models")"
	tpm_keys="$(uh_pkg_path "/etc/ubuntu-hello/tpm-keys")"
	log_dir="$(uh_pkg_path "/var/log/ubuntu-hello")"
	chmod 755 "$etc_hello" 2>/dev/null || true
	chmod 755 "$(uh_pkg_path "/etc/ubuntu-hello/dlib-data")" 2>/dev/null || true
	# dpkg unpacks the conffile with whatever mode the build gave it; a config
	# file is data, not a program (a clean-install check found it at 755).
	chmod 644 "$(uh_pkg_path "/etc/ubuntu-hello/config.ini")" 2>/dev/null || true
	mkdir -p "$models_dir"
	chmod 700 "$models_dir"
	mkdir -p "$tpm_keys"
	chmod 700 "$tpm_keys"
	mkdir -p "$log_dir"
	chmod 755 "$log_dir"
}

uh_restore_config_ini() {
	local config_ini config_template
	config_ini="$(uh_pkg_path "/etc/ubuntu-hello/config.ini")"
	config_template="$(uh_pkg_path "/usr/share/ubuntu-hello/config.ini")"
	if [ ! -f "$config_ini" ]; then
		if [ -f "$config_template" ]; then
			if mkdir -p "$(dirname "$config_ini")" &&
				cp "$config_template" "$config_ini" &&
				chmod 644 "$config_ini"; then
				echo ">>> Restored default config.ini"
			else
				echo "WARNING: Failed to restore config.ini from $config_template"
			fi
		else
			echo "WARNING: $config_template missing; config.ini was not created"
		fi
	fi
}

uh_migrate_config_ini() {
	# An existing config keeps every value the user has; only keys added since
	# it was written are filled in (currently `[video] confirmations`). The code
	# is the installed config_ensure.py, shared with the source installer.
	local config_ini libdir
	config_ini="$(uh_pkg_path "/etc/ubuntu-hello/config.ini")"
	[ -f "$config_ini" ] || return 0
	for libdir in /usr/lib/x86_64-linux-gnu/ubuntu-hello /usr/lib/aarch64-linux-gnu/ubuntu-hello \
	              /usr/lib64/ubuntu-hello /usr/lib/ubuntu-hello /usr/local/lib/ubuntu-hello; do
		libdir="$(uh_pkg_path "$libdir")"
		if [ -f "$libdir/config_ensure.py" ]; then
			python3 -c "import sys; sys.path.insert(0, '$libdir'); import config_ensure; config_ensure.add_missing_confirmations('$config_ini')" || \
				echo "WARNING: config migration failed (see above); the file is unchanged"
			return 0
		fi
	done
	echo "WARNING: config_ensure.py not found; config migration skipped"
}

UH_AUTHSELECT_PROFILE='ubuntu-hello'
# 'done', not the pam-auth-update-only macro 'end': authselect writes this line
# verbatim into system-auth, and Linux-PAM treats the unknown token 'end' as a
# failure to ignore -- so the module's skip return (PAM_AUTHINFO_UNAVAIL for SSH,
# no camera, disabled) surfaced and broke password auth on Fedora. 'done' stops
# the stack on a face match and ignores every other return, as intended.
UH_AUTHSELECT_PAM_LINE='auth        [success=done default=ignore]        pam_ubuntu_hello.so'

uh_configure_pam_authselect() {
	# Fedora (and other authselect-managed distros) have no pam-auth-update /
	# pam-config; authselect owns /etc/pam.d/{system,password}-auth via a
	# custom profile. Idempotent: reselect if our profile already exists.
	if [[ -d "/etc/authselect/custom/${UH_AUTHSELECT_PROFILE}" ]]; then
		authselect select "custom/${UH_AUTHSELECT_PROFILE}" --force >/dev/null 2>&1 || true
		return 0
	fi
	local base_profile
	base_profile="$(authselect current -r 2>/dev/null | awk '{print $1}')"
	[[ -z "$base_profile" ]] && base_profile="local"
	authselect create-profile "${UH_AUTHSELECT_PROFILE}" --base-on="${base_profile}" \
		--symlink-meta --symlink-dconf >/dev/null 2>&1 || return 0
	local f path
	for f in system-auth password-auth; do
		path="/etc/authselect/custom/${UH_AUTHSELECT_PROFILE}/${f}"
		[[ -f "$path" ]] || continue
		grep -q 'pam_ubuntu_hello.so' "$path" ||
			sed -i "0,/^auth/s//${UH_AUTHSELECT_PAM_LINE}\nauth/" "$path"
	done
	authselect select "custom/${UH_AUTHSELECT_PROFILE}" --force >/dev/null 2>&1 || true
}

uh_configure_pam() {
	if ! uh_configure_targets_live_host; then
		echo ">>> Skipping PAM configure (non-default UH_HOST_ROOT)"
		return 0
	fi
	echo ">>> Configuring PAM..."
	if command -v pam-auth-update &>/dev/null; then
		pam-auth-update --package 2>/dev/null || true
	elif command -v pam-config &>/dev/null; then
		pam-config -a --ubuntu-hello 2>/dev/null || true
	elif command -v authselect &>/dev/null; then
		uh_configure_pam_authselect
	fi
}

uh_configure_polkit_override() {
	if ! uh_configure_targets_live_host; then
		echo ">>> Skipping polkit override (non-default UH_HOST_ROOT)"
		return 0
	fi
	local override_dir
	override_dir="$(uh_pkg_path "/etc/systemd/system/polkit-agent-helper@.service.d")"
	mkdir -p "$override_dir"
	# polkit >= 126 runs polkit-agent-helper-1 as a hardened transient unit
	# (DevicePolicy=strict, ProtectSystem=strict, ...). Re-allow what face auth
	# needs: the camera, the Enter-key uinput device, and -- for TPM-sealed
	# keyring unlock -- the TPM character devices plus write access to the
	# tpm-keys dir where tpm2_createprimary/tpm2_load save transient .ctx files.
	# ProtectHome=yes would also hide /run/user (the session bus), so the
	# desktop notification could never reach the user from a polkit prompt.
	# tmpfs keeps /home and /root empty inside the unit; only /run/user is
	# bound back in (the notifier drops to the user's uid before connecting).
	# /run/ubuntu-hello holds the root-only notification-id state (read-only
	# under ProtectSystem=strict).
	cat >"${override_dir}/ubuntu-hello.conf" <<'POLKIT_EOF'
[Service]
PrivateDevices=no
DeviceAllow=char-video4linux rw
DeviceAllow=/dev/uinput rw
DeviceAllow=char-tpm rw
DeviceAllow=/dev/tpm0 rw
ReadWritePaths=/etc/ubuntu-hello/tpm-keys
ProtectHome=tmpfs
BindReadOnlyPaths=-/run/user
ReadWritePaths=-/run/ubuntu-hello
POLKIT_EOF
	chmod 644 "${override_dir}/ubuntu-hello.conf"
	systemctl daemon-reload 2>/dev/null || true
	# Create /run/ubuntu-hello now (tmpfiles.d handles later boots).
	systemd-tmpfiles --create "$(uh_pkg_path /usr/lib/tmpfiles.d/ubuntu-hello.conf)" 2>/dev/null || true
}

uh_ensure_dlib() {
	# Face auth needs the Python dlib module. Debian may provide python3-dlib;
	# Fedora/openSUSE/Arch and portable host installs usually do not. When
	# missing on the live host, install a pinned wheel/sdist via pip3 and
	# record a marker so package-prerm can uninstall only what we installed.
	#
	# Set UH_DLIB_PIP_SKIP=1 to skip this step entirely (e.g. snap builds
	# where dlib is pre-bundled at build time in the snap payload).
	if [[ "${UH_DLIB_PIP_SKIP:-0}" == "1" ]]; then
		echo ">>> dlib pip install skipped (UH_DLIB_PIP_SKIP=1)"
		return 0
	fi
	local python_bin pip_bin marker
	local dlib_spec="${UH_DLIB_PIP_SPEC:-dlib==20.0.1}"
	local marker_rel="${UH_DLIB_PIP_MARKER:-/var/lib/ubuntu-hello/.dlib-pip-installed}"

	python_bin="$(command -v python3 || true)"
	[[ -n "$python_bin" ]] || return 0
	if "$python_bin" -c 'import dlib' 2>/dev/null; then
		return 0
	fi

	if ! uh_configure_targets_live_host; then
		echo ">>> WARNING: dlib missing in staged root; skip pip install"
		return 0
	fi

	pip_bin="$(command -v pip3 || true)"
	if [[ -z "$pip_bin" ]]; then
		echo ""
		echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
		echo "!!! ERROR: Python module 'dlib' is not installed and pip3 is missing."
		echo "!!! Face authentication will NOT work."
		echo "!!! Install pip3, then: pip3 install ${dlib_spec} --break-system-packages"
		echo "!!! (or your distro's dlib package, if one exists)."
		echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
		echo ""
		return 1
	fi

	echo ">>> Installing ${dlib_spec} via pip (required for face auth)..."
	# Older dlib (19.24.x) shipped a pybind11 with cmake_minimum_required (<3.5),
	# which CMake 4+ on Ubuntu 26.04 / Fedora 44 / Arch rejects unless the policy
	# floor is raised. Kept for UH_DLIB_PIP_SPEC overrides to those versions; it
	# is a harmless no-op for the default dlib 20.x.
	local -a pip_cmd=(
		env "CMAKE_POLICY_VERSION_MINIMUM=${UH_DLIB_CMAKE_POLICY_VERSION_MINIMUM:-3.5}"
		"$pip_bin" install "${dlib_spec}"
	)
	if "$pip_bin" install --help 2>/dev/null | grep -qF -- '--break-system-packages'; then
		pip_cmd+=(--break-system-packages)
	fi
	if ! "${pip_cmd[@]}"; then
		echo ""
		echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
		echo "!!! ERROR: failed to install ${dlib_spec}."
		echo "!!! Face authentication will NOT work until dlib is available."
		echo "!!! Ensure cmake / a C++ toolchain are installed, then retry."
		echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
		echo ""
		return 1
	fi

	if ! "$python_bin" -c 'import dlib' 2>/dev/null; then
		echo "!!! ERROR: dlib installed but still not importable" >&2
		return 1
	fi

	marker="$(uh_pkg_path "${marker_rel}")"
	mkdir -p "$(dirname "$marker")"
	: >"$marker"
	echo ">>> dlib ready"
}

uh_package_configure() {
	if [[ "${UH_PACKAGE_CONFIGURE_DRY_RUN:-0}" == "1" ]]; then
		echo ">>> Ubuntu Hello configure dry-run (UH_PACKAGE_CONFIGURE_DRY_RUN=1)"
		return 0
	fi
	uh_download_models
	uh_set_permissions
	uh_restore_config_ini
	uh_migrate_config_ini
	# PAM and the polkit hardening drop-in are wired before dlib and never
	# blocked by it: a dlib build failure (e.g. a missing toolchain) must not
	# leave the login stack half-configured or the polkit sandbox unpatched.
	# dlib is non-fatal here -- the user can install it later and face auth then
	# works -- so the install still completes with the security config in place.
	uh_configure_pam
	uh_configure_polkit_override
	uh_ensure_dlib || echo ">>> WARNING: dlib is not available yet; face authentication will not work until it is installed (see messages above)."
	echo ">>> Ubuntu Hello installation complete!"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	uh_package_configure
fi
