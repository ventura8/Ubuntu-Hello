#!/usr/bin/env bash
# Launch setup wizard when no face models are enrolled (shared packaging helper).
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

uh_models_enrolled() {
	local d
	d="$(uh_pkg_path "/etc/ubuntu-hello/models")"
	[ -d "$d" ] || return 1
	local match
	match="$(find "$d" -maxdepth 1 -type f ! -name '.*' -print -quit 2>/dev/null)"
	[ -n "$match" ]
}

uh_launch_setup_wizard() {
	local reason="$1"
	local launcher
	launcher="$(uh_pkg_path "/usr/share/ubuntu-hello-gtk/run_after_install.py")"

	if [ ! -f "$launcher" ]; then
		echo ">>> Setup wizard launcher missing — run: ubuntu-hello-gtk --force-onboarding"
		return 0
	fi

	if ! uh_configure_targets_live_host; then
		echo ">>> Skipping setup wizard launch (non-default UH_HOST_ROOT)"
		return 0
	fi

	echo ">>> Launching Ubuntu Hello setup wizard (${reason}; no face models enrolled yet)..."
	local launch_rc=0
	python3 "$launcher" || launch_rc=$?
	if [ "$launch_rc" -eq 0 ]; then
		echo ">>> Setup wizard launch requested (approve the polkit prompt if shown)"
	else
		echo "WARNING: setup wizard did not start — run: ubuntu-hello-gtk --force-onboarding"
	fi
}

uh_package_gtk_onboard() {
	local reason="${1:-ubuntu-hello-gtk.configure}"
	if [[ "${UH_SKIP_ONBOARD:-0}" == "1" ]]; then
		echo ">>> Setup wizard skipped (UH_SKIP_ONBOARD=1)"
		return 0
	fi
	if uh_models_enrolled; then
		echo ">>> Setup wizard skipped (face models already enrolled)"
	else
		uh_launch_setup_wizard "$reason"
	fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	uh_package_gtk_onboard "${1:-ubuntu-hello-gtk.configure}"
fi
