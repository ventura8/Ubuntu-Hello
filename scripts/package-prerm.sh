#!/usr/bin/env bash
# Shared removal steps for deb/rpm/snap/AppImage/Flatpak uninstall.
set -euo pipefail

UH_DLIB_PIP_MARKER='/var/lib/ubuntu-hello/.dlib-pip-installed'

uh_pkg_path() {
	local relpath="$1"
	if [[ -n "${UH_HOST_ROOT:-}" ]]; then
		printf '%s%s\n' "${UH_HOST_ROOT}" "$relpath"
	else
		printf '%s\n' "$relpath"
	fi
}

uh_prerm_targets_live_host() {
	[[ -z "${UH_HOST_ROOT:-}" || "${UH_HOST_ROOT}" == "/" ]]
}

uh_package_prerm() {
	local pam_config override_dir marker

	# Remove PAM config
	pam_config="$(uh_pkg_path /usr/share/pam-configs/ubuntu-hello)"
	rm -f "$pam_config" 2>/dev/null || true
	if uh_prerm_targets_live_host && command -v pam-auth-update &>/dev/null; then
		pam-auth-update --package 2>/dev/null || true
	elif uh_prerm_targets_live_host && command -v authselect &>/dev/null; then
		if authselect current -r 2>/dev/null | grep -q '^custom/ubuntu-hello'; then
			authselect select local --force >/dev/null 2>&1 || true
		fi
		rm -rf /etc/authselect/custom/ubuntu-hello 2>/dev/null || true
	fi

	# Restore OS login wallet password from sealed SUW credentials while
	# ubuntu-hello and /etc/ubuntu-hello still exist. Never fail removal.
	if uh_prerm_targets_live_host && command -v ubuntu-hello >/dev/null 2>&1; then
		timeout 120 ubuntu-hello keyring restore --all || true
	fi

	# Remove Polkit override (Meson install_config + package-configure names)
	override_dir="$(uh_pkg_path /etc/systemd/system/polkit-agent-helper@.service.d)"
	rm -f "${override_dir}/ubuntu-hello.conf" "${override_dir}/override.conf" 2>/dev/null || true
	rmdir "$override_dir" 2>/dev/null || true
	if uh_prerm_targets_live_host; then
		systemctl daemon-reload 2>/dev/null || true
	fi

	# Remove config, models, and data
	rm -rf "$(uh_pkg_path /etc/ubuntu-hello)" 2>/dev/null || true
	rm -rf "$(uh_pkg_path /var/log/ubuntu-hello)" 2>/dev/null || true

	# Uninstall dlib pip package only when we installed it via package-configure
	marker="$(uh_pkg_path "${UH_DLIB_PIP_MARKER}")"
	if uh_prerm_targets_live_host && [[ -f "${marker}" ]]; then
		local -a pip_cmd=(pip3 uninstall dlib -y)
		# Prefer the same pip3 binary that will run uninstall; Python version alone
		# is not enough (older pip on 3.11+ may lack the flag).
		if pip3 uninstall --help 2>/dev/null | grep -qF -- '--break-system-packages'; then
			pip_cmd+=(--break-system-packages)
		fi
		if "${pip_cmd[@]}" 2>/dev/null; then
			rm -f "${marker}"
		fi
	fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	uh_package_prerm
fi
