#!/usr/bin/env bash
# End-to-end install of built packaging artifacts on live / (CI).
# Expects artifacts/ from the matching release driver; runs inside the format image.
#
# Cycle per format:
#   1) install → assert
#   2) upgrade in place (install again) with a live config.ini marker → assert marker kept
#   3) remove → assert gone
#   4) reinstall → assert config.ini restored from packaged template (marker gone)
#
# Usage: ./scripts/packaging-e2e-install.sh <deb|rpm-fedora|rpm-opensuse|arch|snap|appimage|flatpak>
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh

FORMAT="${1:?format: deb|rpm-fedora|rpm-opensuse|arch|snap|appimage|flatpak}"
VERSION="$(uh_read_version)"
ART="${UH_ARTIFACTS_DIR:-artifacts}"
# rpmbuild tags the built RPM with the container's own arch (x86_64, aarch64, ...) —
# match whatever this cell is actually running as, not a hardcoded x86_64.
RPM_ARCH="$(uname -m)"
export UH_SKIP_ONBOARD="${UH_SKIP_ONBOARD:-1}"
# Unique per-run marker written into live config.ini before the upgrade step.
UH_E2E_CONFIG_MARKER="UH_E2E_UPGRADE_MARKER=${VERSION}-$$-${RANDOM}"

uh_e2e_fail() {
	echo "error: $*" >&2
	exit 1
}

uh_e2e_assert_file() {
	local path="$1"
	[[ -e "$path" ]] || uh_e2e_fail "expected file missing: ${path}"
}

uh_e2e_assert_gone() {
	local path="$1"
	[[ ! -e "$path" ]] || uh_e2e_fail "expected path removed: ${path}"
}

uh_e2e_find_pam_so() {
	local match
	# Debian multiarch (/usr/lib/<triplet>/security), Fedora/openSUSE (/usr/lib64/security),
	# and flat /usr/lib/security layouts.
	shopt -s nullglob
	for match in \
		/usr/lib/*/security/pam_ubuntu_hello.so \
		/usr/lib64/security/pam_ubuntu_hello.so \
		/usr/lib/security/pam_ubuntu_hello.so \
		/lib64/security/pam_ubuntu_hello.so \
		/lib/*/security/pam_ubuntu_hello.so; do
		if [[ -f "$match" ]]; then
			printf '%s\n' "$match"
			shopt -u nullglob
			return 0
		fi
	done
	shopt -u nullglob
	return 1
}

uh_e2e_assert_installed() {
	echo "==> E2E assert installed (${FORMAT})"
	uh_e2e_assert_file /usr/bin/ubuntu-hello
	command -v ubuntu-hello >/dev/null || uh_e2e_fail "ubuntu-hello not on PATH"
	ubuntu-hello --help >/dev/null || uh_e2e_fail "ubuntu-hello --help failed"
	local pam_so
	pam_so="$(uh_e2e_find_pam_so)" || uh_e2e_fail "pam_ubuntu_hello.so not found under /usr/lib*/security"
	uh_e2e_assert_file "$pam_so"
	uh_e2e_assert_file /etc/ubuntu-hello/config.ini
	uh_e2e_assert_file /usr/share/ubuntu-hello/config.ini
	# Face matching requires dlib on the host Python used by compare.py.
	python3 -c 'import dlib' >/dev/null 2>&1 \
		|| uh_e2e_fail "dlib not importable after ${FORMAT} install (face auth would be broken)"
	# PAM wiring: Debian/Ubuntu pam-configs, or authselect custom profile, or openSUSE pam-config module
	if [[ -f /usr/share/pam-configs/ubuntu-hello ]]; then
		:
	elif [[ -d /etc/authselect/custom/ubuntu-hello ]]; then
		:
	elif [[ -f /usr/share/pam-config/ubuntu-hello ]]; then
		:
	else
		# Still require the pam-configs file shipped by our package when present on Debian-like
		if [[ "${FORMAT}" == "deb" ]]; then
			uh_e2e_fail "missing /usr/share/pam-configs/ubuntu-hello after deb install"
		fi
		echo ">>> WARNING: no pam-configs/authselect/pam-config ubuntu-hello marker (continuing)"
	fi
	if [[ -x /usr/bin/ubuntu-hello-gtk ]]; then
		uh_e2e_assert_file /usr/bin/ubuntu-hello-gtk
	fi
	echo "✔ installed asserts OK"
}

uh_e2e_assert_removed() {
	echo "==> E2E assert removed (${FORMAT})"
	uh_e2e_assert_gone /usr/bin/ubuntu-hello
	# config tree removed by prerm / install-host uninstall
	uh_e2e_assert_gone /etc/ubuntu-hello/config.ini
	# PAM module + format markers accepted by uh_e2e_assert_installed must be gone
	if uh_e2e_find_pam_so >/dev/null 2>&1; then
		uh_e2e_fail "pam_ubuntu_hello.so still present after remove"
	fi
	uh_e2e_assert_gone /usr/share/pam-configs/ubuntu-hello
	uh_e2e_assert_gone /usr/share/pam-config/ubuntu-hello
	[[ ! -d /etc/authselect/custom/ubuntu-hello ]] \
		|| uh_e2e_fail "authselect custom/ubuntu-hello profile still present after remove"
	echo "✔ removed asserts OK"
}

uh_e2e_assert_config_restored() {
	echo "==> E2E assert config.ini restored after reinstall"
	uh_e2e_assert_file /etc/ubuntu-hello/config.ini
	# Purge + reinstall must ship a fresh template, not the mutated upgrade copy.
	if grep -Fq "${UH_E2E_CONFIG_MARKER}" /etc/ubuntu-hello/config.ini; then
		uh_e2e_fail "upgrade marker still present after purge+reinstall (expected fresh config.ini)"
	fi
	echo "✔ config.ini restored"
}

uh_e2e_mark_live_config() {
	uh_e2e_assert_file /etc/ubuntu-hello/config.ini
	printf '\n# %s\n' "${UH_E2E_CONFIG_MARKER}" >>/etc/ubuntu-hello/config.ini
}

uh_e2e_assert_config_preserved() {
	echo "==> E2E assert config.ini preserved across upgrade"
	uh_e2e_assert_file /etc/ubuntu-hello/config.ini
	grep -Fq "${UH_E2E_CONFIG_MARKER}" /etc/ubuntu-hello/config.ini \
		|| uh_e2e_fail "upgrade clobbered live config.ini (missing marker ${UH_E2E_CONFIG_MARKER})"
	echo "✔ config.ini preserved across upgrade"
}

uh_e2e_deb_install() {
	apt-get update -qq
	local debs=()
	local f
	while IFS= read -r f; do
		debs+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello_${VERSION}*_*.deb" || true)
	while IFS= read -r f; do
		debs+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-gtk_${VERSION}*_*.deb" || true)
	[[ "${#debs[@]}" -ge 2 ]] || uh_e2e_fail "expected deb artifacts under ${ART}"
	# Same-version local debs: install on first pass, --reinstall on upgrade.
	if dpkg -s ubuntu-hello >/dev/null 2>&1; then
		DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall "${debs[@]}"
	else
		DEBIAN_FRONTEND=noninteractive apt-get install -y "${debs[@]}"
	fi
}

uh_e2e_deb_remove() {
	DEBIAN_FRONTEND=noninteractive apt-get remove --purge -y ubuntu-hello-gtk ubuntu-hello || true
	DEBIAN_FRONTEND=noninteractive apt-get autoremove -y || true
}

uh_e2e_rpm_fedora_install() {
	local rpms=()
	local f
	while IFS= read -r f; do
		rpms+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-${VERSION}-*.fc*.${RPM_ARCH}.rpm" || true)
	while IFS= read -r f; do
		rpms+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-gtk-${VERSION}-*.fc*.${RPM_ARCH}.rpm" || true)
	[[ "${#rpms[@]}" -ge 2 ]] || uh_e2e_fail "expected Fedora RPM artifacts under ${ART}"
	# Local CI artifacts are unsigned. Same NEVRA needs reinstall for a true upgrade pass.
	if rpm -q ubuntu-hello >/dev/null 2>&1; then
		dnf reinstall -y --nogpgcheck "${rpms[@]}"
	else
		dnf install -y --nogpgcheck "${rpms[@]}"
	fi
}

uh_e2e_rpm_fedora_remove() {
	dnf remove -y ubuntu-hello-gtk ubuntu-hello || true
}

uh_e2e_rpm_opensuse_install() {
	local rpms=()
	local f
	while IFS= read -r f; do
		rpms+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-${VERSION}-*.lp*.${RPM_ARCH}.rpm" || true)
	while IFS= read -r f; do
		rpms+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-gtk-${VERSION}-*.lp*.${RPM_ARCH}.rpm" || true)
	[[ "${#rpms[@]}" -ge 2 ]] || uh_e2e_fail "expected openSUSE RPM artifacts under ${ART}"
	# Local CI artifacts are unsigned; allow install without repo signatures.
	if rpm -q ubuntu-hello >/dev/null 2>&1; then
		zypper --non-interactive --no-gpg-checks install --force --allow-unsigned-rpm "${rpms[@]}"
	else
		zypper --non-interactive --no-gpg-checks install --allow-unsigned-rpm "${rpms[@]}"
	fi
}

uh_e2e_rpm_opensuse_remove() {
	zypper --non-interactive remove -y ubuntu-hello-gtk ubuntu-hello || true
}

uh_e2e_arch_install() {
	pacman -Sy --noconfirm
	local pkgs=()
	local f
	while IFS= read -r f; do
		pkgs+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-${VERSION}-*.pkg.tar.zst" || true)
	while IFS= read -r f; do
		pkgs+=("$f")
	done < <(compgen -G "${ART}/ubuntu-hello-gtk-${VERSION}-*.pkg.tar.zst" || true)
	[[ "${#pkgs[@]}" -ge 2 ]] || uh_e2e_fail "expected Arch pkg artifacts under ${ART}"
	pacman -U --noconfirm "${pkgs[@]}"
}

uh_e2e_arch_remove() {
	pacman -Rns --noconfirm ubuntu-hello-gtk ubuntu-hello || true
}

uh_e2e_host_install_from_prefix() {
	local prefix="$1"
	local install_sh="${prefix}/usr/share/ubuntu-hello/install-host.sh"
	[[ -f "$install_sh" ]] || uh_e2e_fail "install-host.sh missing under ${prefix}"
	chmod +x "$install_sh"
	UH_SKIP_ONBOARD=1 bash "$install_sh" --install
}

uh_e2e_host_uninstall_from_prefix() {
	local prefix="$1"
	local install_sh="${prefix}/usr/share/ubuntu-hello/install-host.sh"
	[[ -f "$install_sh" ]] || uh_e2e_fail "install-host.sh missing under ${prefix}"
	bash "$install_sh" --uninstall
}

uh_e2e_appimage_prepare_prefix() {
	local appimage extract_dir offset
	# Match this cell's own arch (x86_64, aarch64, ...) rather than a bare
	# wildcard + head -n1, which could silently grab a stale/foreign-arch
	# AppImage if artifacts/ ever holds more than one arch at once.
	appimage="$(compgen -G "${ART}/Ubuntu-Hello-${VERSION}-${RPM_ARCH}.AppImage" | head -n1 || true)"
	[[ -n "$appimage" ]] || uh_e2e_fail "AppImage artifact missing for arch ${RPM_ARCH}"
	appimage="$(readlink -f "$appimage")"
	chmod +x "$appimage"
	extract_dir="$(mktemp -d /tmp/uh-e2e-appimage.XXXXXX)"
	echo ">>> Extracting AppImage to ${extract_dir}" >&2
	(
		cd "$extract_dir"
		# Prefer offset+unsquashfs when available (no FUSE required, and never
		# executes the AppImage itself — see uh_appimage_squashfs_offset for
		# why running it directly fails under QEMU cross-arch emulation).
		if command -v unsquashfs >/dev/null 2>&1 \
			&& offset="$(uh_appimage_squashfs_offset "${appimage}" 2>/dev/null)" && [[ -n "${offset}" ]]; then
			unsquashfs -o "${offset}" -d squashfs-root "${appimage}" >/dev/null
		else
			APPIMAGE_EXTRACT_AND_RUN=1 "${appimage}" --appimage-extract >/dev/null
		fi
	)
	[[ -d "${extract_dir}/squashfs-root" ]] || uh_e2e_fail "AppImage extract failed"
	# stdout must be only the prefix path (captured by callers).
	printf '%s\n' "${extract_dir}/squashfs-root"
}

uh_e2e_flatpak_locate_files() {
	local cand
	for cand in \
		"${HOME}/.local/share/flatpak/app/com.github.ventura8.UbuntuHello/current/active/files" \
		"/var/lib/flatpak/app/com.github.ventura8.UbuntuHello/current/active/files"; do
		if [[ -f "${cand}/usr/share/ubuntu-hello/install-host.sh" ]]; then
			printf '%s\n' "$cand"
			return 0
		fi
	done
	return 1
}

uh_e2e_flatpak_install() {
	local bundle prefix scope
	bundle="$(compgen -G "${ART}/com.github.ventura8.UbuntuHello-${VERSION}-*.flatpak" | head -n1 || true)"
	[[ -n "$bundle" ]] || uh_e2e_fail "Flatpak bundle missing under ${ART}"
	command -v flatpak >/dev/null || uh_e2e_fail "flatpak not installed"
	# Preserve install scope across upgrade/remove: user if already user-installed,
	# system if already system-installed, otherwise default to user for a fresh cycle.
	if flatpak info --user com.github.ventura8.UbuntuHello >/dev/null 2>&1; then
		scope=user
	elif flatpak info --system com.github.ventura8.UbuntuHello >/dev/null 2>&1; then
		scope=system
	else
		scope=user
	fi
	FLATPAK_SCOPE="${scope}"
	export FLATPAK_SCOPE
	if [[ "${scope}" == "user" ]]; then
		flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
		if flatpak info --user com.github.ventura8.UbuntuHello >/dev/null 2>&1; then
			flatpak install --user -y --noninteractive --reinstall "${bundle}" 2>/dev/null \
				|| flatpak install --user -y --noninteractive "${bundle}"
		else
			flatpak install --user -y --noninteractive "${bundle}"
		fi
	else
		flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
		if flatpak info --system com.github.ventura8.UbuntuHello >/dev/null 2>&1; then
			flatpak install --system -y --noninteractive --reinstall "${bundle}" 2>/dev/null \
				|| flatpak install --system -y --noninteractive "${bundle}"
		else
			flatpak install --system -y --noninteractive "${bundle}"
		fi
	fi
	prefix="$(uh_e2e_flatpak_locate_files)" \
		|| uh_e2e_fail "could not locate Flatpak files/ tree with install-host.sh after bundle install"
	FLATPAK_PREFIX="${prefix}"
	export FLATPAK_PREFIX
	uh_e2e_host_install_from_prefix "${FLATPAK_PREFIX}"
}

uh_e2e_flatpak_remove() {
	if [[ -n "${FLATPAK_PREFIX:-}" && -f "${FLATPAK_PREFIX}/usr/share/ubuntu-hello/install-host.sh" ]]; then
		bash "${FLATPAK_PREFIX}/usr/share/ubuntu-hello/install-host.sh" --uninstall || true
	elif [[ -f /usr/share/ubuntu-hello/install-host.sh ]]; then
		bash /usr/share/ubuntu-hello/install-host.sh --uninstall || true
	fi
	local scope="${FLATPAK_SCOPE:-}"
	if [[ -z "${scope}" ]]; then
		if flatpak info --user com.github.ventura8.UbuntuHello >/dev/null 2>&1; then
			scope=user
		elif flatpak info --system com.github.ventura8.UbuntuHello >/dev/null 2>&1; then
			scope=system
		fi
	fi
	if [[ "${scope}" == "user" ]]; then
		flatpak uninstall --user -y --noninteractive com.github.ventura8.UbuntuHello || true
	elif [[ "${scope}" == "system" ]]; then
		flatpak uninstall --system -y --noninteractive com.github.ventura8.UbuntuHello || true
	fi
	unset FLATPAK_PREFIX
	unset FLATPAK_SCOPE
}

uh_e2e_snap_install_host() {
	local snap_file
	snap_file="$(compgen -G "${ART}/ubuntu-hello_${VERSION}_*.snap" | head -n1 || true)"
	[[ -n "$snap_file" ]] || uh_e2e_fail "Snap artifact missing"
	if snap list ubuntu-hello >/dev/null 2>&1; then
		# Same local snap revision cannot refresh; re-run host overlay for upgrade.
		echo ">>> Snap already installed; re-running install-host.sh (upgrade)"
	else
		snap install --dangerous --classic "${snap_file}"
	fi
	# Host install via classic snap payload (copies PAM/CLI onto /)
	UH_SKIP_ONBOARD=1 bash /snap/ubuntu-hello/current/usr/share/ubuntu-hello/install-host.sh --install
}

uh_e2e_snap_remove_host_and_snap() {
	if [[ -f /snap/ubuntu-hello/current/usr/share/ubuntu-hello/install-host.sh ]]; then
		bash /snap/ubuntu-hello/current/usr/share/ubuntu-hello/install-host.sh --uninstall || true
	elif [[ -f /usr/share/ubuntu-hello/install-host.sh ]]; then
		bash /usr/share/ubuntu-hello/install-host.sh --uninstall || true
	fi
	if snap list ubuntu-hello >/dev/null 2>&1; then
		snap remove ubuntu-hello || true
	fi
}

uh_e2e_run_cycle() {
	local do_install="$1"
	local do_remove="$2"

	echo "==> E2E install (${FORMAT})"
	"${do_install}"
	uh_e2e_assert_installed

	echo "==> E2E upgrade in place (${FORMAT})"
	uh_e2e_mark_live_config
	"${do_install}"
	uh_e2e_assert_installed
	uh_e2e_assert_config_preserved

	echo "==> E2E remove (${FORMAT})"
	"${do_remove}"
	uh_e2e_assert_removed

	echo "==> E2E reinstall (${FORMAT})"
	"${do_install}"
	uh_e2e_assert_installed
	uh_e2e_assert_config_restored
}

case "${FORMAT}" in
	deb)
		uh_e2e_run_cycle uh_e2e_deb_install uh_e2e_deb_remove
		;;
	rpm-fedora)
		uh_e2e_run_cycle uh_e2e_rpm_fedora_install uh_e2e_rpm_fedora_remove
		;;
	rpm-opensuse)
		uh_e2e_run_cycle uh_e2e_rpm_opensuse_install uh_e2e_rpm_opensuse_remove
		;;
	arch)
		uh_e2e_run_cycle uh_e2e_arch_install uh_e2e_arch_remove
		;;
	snap)
		# Prefer being invoked from ci-snap-build.sh inside the systemd container.
		uh_e2e_run_cycle uh_e2e_snap_install_host uh_e2e_snap_remove_host_and_snap
		;;
	appimage)
		APPIMAGE_PREFIX="$(uh_e2e_appimage_prepare_prefix)"
		export APPIMAGE_PREFIX
		uh_e2e_appimage_install() { uh_e2e_host_install_from_prefix "${APPIMAGE_PREFIX}"; }
		uh_e2e_appimage_remove() { uh_e2e_host_uninstall_from_prefix "${APPIMAGE_PREFIX}"; }
		uh_e2e_run_cycle uh_e2e_appimage_install uh_e2e_appimage_remove
		;;
	flatpak)
		uh_e2e_run_cycle uh_e2e_flatpak_install uh_e2e_flatpak_remove
		;;
	*)
		uh_e2e_fail "Unknown format: ${FORMAT}"
		;;
esac

echo "✔ packaging E2E install OK for ${FORMAT} (version ${VERSION})"
