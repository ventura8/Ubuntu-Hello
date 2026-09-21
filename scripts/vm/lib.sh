# Shared helpers for scenario scripts that run INSIDE the guest.
# A scenario prints exactly one JSON object on stdout: {"checks": {...}, "info": {...}}.
# Everything else goes to stderr, which the driver keeps as the scenario log.
set -uo pipefail

declare -A CHECKS INFO

check() {                       # check <name> <command...>   -> records pass/fail
	local name="$1"; shift
	if "$@" >&2 2>&1; then CHECKS["$name"]=true; echo "  ok   $name" >&2
	else CHECKS["$name"]=false; echo "  FAIL $name" >&2; fi
}
note() { INFO["$1"]="$2"; }          # note <key> <value>   -> recorded as info

emit() {
	local first=1 k
	printf '{"checks": {'
	for k in "${!CHECKS[@]}"; do
		[ $first -eq 1 ] || printf ', '; first=0
		printf '"%s": %s' "$k" "${CHECKS[$k]}"
	done
	printf '}, "info": {'
	first=1
	for k in "${!INFO[@]}"; do
		[ $first -eq 1 ] || printf ', '; first=0
		printf '"%s": %s' "$k" "$(printf '%s' "${INFO[$k]}" | jq -Rs .)"
	done
	printf '}}\n'
}

# ---- The distro underneath ---------------------------------------------------
# Scenarios are written once. What differs per distro is the package manager,
# where a package lands, and how the PAM stack includes the module; all of that
# lives here. Local packages built from the working tree arrive in /tmp/pkgs.
. /etc/os-release
DISTRO="${ID:-unknown}"
case "$DISTRO" in
	fedora) PKG=rpm ;;
	arch)   PKG=pacman ;;
	*)      PKG=deb ;;
esac
export DEBIAN_FRONTEND=noninteractive

case "$PKG" in
	deb)    PAM_AUTH_FILE=/etc/pam.d/common-auth;  PAM_INCLUDE_LINE='@include common-auth' ;;
	rpm)    PAM_AUTH_FILE=/etc/pam.d/system-auth;  PAM_INCLUDE_LINE='auth       substack     system-auth' ;;
	pacman) PAM_AUTH_FILE=/etc/pam.d/system-auth;  PAM_INCLUDE_LINE='auth       include      system-auth' ;;
esac
# pam-auth-update (Debian) and authselect (Fedora) let the package wire the
# stack itself; Arch has no such tool and the user adds the line by hand.
pam_wired_by_package() { [ "$PKG" != pacman ]; }
pam_line_present() { grep -q 'pam_ubuntu_hello.so' "$PAM_AUTH_FILE"; }

local_pkgs() {                  # the two packages the driver copied over, main one first
	# Two ls calls: one call sorts "ubuntu-hello-gtk_" before "ubuntu-hello_".
	case "$PKG" in
		deb)    ls /tmp/pkgs/ubuntu-hello_*.deb; ls /tmp/pkgs/ubuntu-hello-gtk_*.deb ;;
		rpm)    ls /tmp/pkgs/ubuntu-hello-[0-9]*.rpm; ls /tmp/pkgs/ubuntu-hello-gtk-[0-9]*.rpm ;;
		pacman) ls /tmp/pkgs/ubuntu-hello-[0-9]*.pkg.tar.zst; ls /tmp/pkgs/ubuntu-hello-gtk-[0-9]*.pkg.tar.zst ;;
	esac 2>/dev/null
}
# A flaky mirror can time out mid-download; retry a few times so a transient
# failure does not silently drop a runtime dependency (seen on Arch, where a
# half-synced db left libinih uninstalled and the PAM module could not load).
retry() { local n=0; until "$@"; do n=$((n+1)); [ "$n" -ge 3 ] && return 1; sleep 5; done; }
pkg_refresh() {
	case "$PKG" in
		deb)    retry sudo apt-get update -qq ;;
		rpm)    retry sudo dnf -q makecache ;;
		pacman) retry sudo pacman -Syy --noconfirm ;;
	esac >&2
}
pkg_install() {                 # pkg_install <package file or name>...
	case "$PKG" in
		deb)    sudo apt-get install -y -qq "$@" ;;
		rpm)    sudo dnf install -y -q "$@" ;;
		pacman) retry sudo pacman -U --noconfirm "$@" ;;
	esac >&2
}
pkg_upgrade_keeping_config() {  # an upgrade that must not touch the admin's config
	case "$PKG" in
		deb)    sudo apt-get install -y -qq -o Dpkg::Options::=--force-confold "$@" ;;
		rpm)    sudo dnf install -y -q "$@" ;;     # %config(noreplace) keeps the file
		pacman) sudo pacman -U --noconfirm "$@" ;; # a changed config gets a .pacnew
	esac >&2
}
pkg_reinstall() {               # reinstall the main package; the local file when there is one
	local file; file="$(local_pkgs | head -1)"
	case "$PKG" in
		deb)    sudo apt-get install -y -qq --reinstall "${file:-ubuntu-hello}" ;;
		rpm)    sudo dnf reinstall -y -q "${file:-ubuntu-hello}" || sudo rpm -U --replacepkgs "$file" ;;
		pacman) sudo pacman -U --noconfirm "$file" ;;
	esac >&2
}
pkg_purge() {
	case "$PKG" in
		deb)    sudo apt-get purge -y -qq ubuntu-hello ubuntu-hello-gtk && sudo apt-get autoremove -y -qq ;;
		rpm)    sudo dnf remove -y -q ubuntu-hello ubuntu-hello-gtk ;;
		pacman) sudo pacman -Rns --noconfirm ubuntu-hello-gtk ubuntu-hello ;;
	esac >&2
}
pkg_installed() {               # fully configured, not the half-way state a failed postinst leaves
	case "$PKG" in
		deb)    [ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" = "install ok installed" ] ;;
		rpm)    rpm -q "$1" >/dev/null 2>&1 ;;
		pacman) pacman -Q "$1" >/dev/null 2>&1 ;;
	esac
}
pkg_state() {                   # for the log: whatever the package manager says
	case "$PKG" in
		deb)    dpkg-query -W -f='${Status}' "$1" 2>/dev/null ;;
		rpm)    rpm -q "$1" 2>/dev/null || true ;;
		pacman) pacman -Q "$1" 2>/dev/null || true ;;
	esac
}
pkg_version() {
	case "$PKG" in
		deb)    dpkg-query -W -f='${Version}' "$1" 2>/dev/null ;;
		rpm)    rpm -q "$1" >/dev/null 2>&1 && rpm -q --qf '%{VERSION}-%{RELEASE}' "$1" ;;  # "not installed" goes to stdout
		pacman) pacman -Q "$1" 2>/dev/null | awk '{print $2}' ;;
	esac
}
pkg_file_version() {            # the version inside a package file
	case "$PKG" in
		deb)    dpkg-deb -f "$1" Version ;;
		rpm)    rpm -qp --qf '%{VERSION}-%{RELEASE}' "$1" 2>/dev/null ;;
		pacman) bsdtar -xOf "$1" .PKGINFO 2>/dev/null | awk -F' = ' '/^pkgver/{print $2}' ;;
	esac
}
# The paths a package lands on: Debian multiarch, Fedora lib64, Arch lib.
pam_module_path() { ls /usr/lib/x86_64-linux-gnu/security/pam_ubuntu_hello.so /usr/lib64/security/pam_ubuntu_hello.so /usr/lib/security/pam_ubuntu_hello.so 2>/dev/null | head -1; }
libdir_path()     { ls -d /usr/lib/x86_64-linux-gnu/ubuntu-hello /usr/lib64/ubuntu-hello /usr/lib/ubuntu-hello 2>/dev/null | head -1; }
gtk_libdir_path() { ls -d /usr/lib/x86_64-linux-gnu/ubuntu-hello-gtk /usr/lib64/ubuntu-hello-gtk /usr/lib/ubuntu-hello-gtk 2>/dev/null | head -1; }
# A crashed process of ours, whichever way the distro records crashes.
crash_reports() {
	{ sudo ls /var/crash 2>/dev/null; sudo coredumpctl list --no-pager --no-legend 2>/dev/null; } | grep -i 'ubuntu[-_]hello'
}
