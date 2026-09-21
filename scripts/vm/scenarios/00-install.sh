#!/usr/bin/env bash
# Install onto a clean system and check everything the package is supposed to
# leave behind, including the pieces a container cannot host. The packages are
# the ones built from the working tree (UH_SOURCE=local), or on Ubuntu the
# PPA's published build.
source /tmp/lib.sh
note distro "$DISTRO ${VERSION_ID:-rolling}"
note source "${UH_SOURCE:-ppa}"
start=$(date +%s)
rc=0
if [ "${UH_SOURCE:-ppa}" = local ]; then
	echo "== installing local packages from /tmp/pkgs" >&2
	pkg_refresh
	mapfile -t pkgs < <(local_pkgs)
	note candidate "$(pkg_file_version "${pkgs[0]}")"
	pkg_install "${pkgs[@]}" || rc=$?
else
	[ "$PKG" = deb ] || { note error "only Ubuntu has a PPA; pass UH_VM_PKG_DIR"; emit; exit 0; }
	echo "== adding $UH_PPA" >&2
	sudo add-apt-repository -y "$UH_PPA" >&2
	pkg_refresh
	note candidate "$(apt-cache policy ubuntu-hello | awk '/Candidate/{print $2}')"
	echo "== installing (this compiles dlib on a clean system)" >&2
	pkg_install ubuntu-hello ubuntu-hello-gtk || rc=$?
fi
note install_seconds "$(( $(date +%s) - start ))"
note install_exit "$rc"
note version "$(pkg_version ubuntu-hello)"
note package_state "$(pkg_state ubuntu-hello); gtk: $(pkg_state ubuntu-hello-gtk)"

check install_succeeded        test "$rc" = 0
check package_installed        pkg_installed ubuntu-hello
check gtk_package_installed    pkg_installed ubuntu-hello-gtk
check cli_runs                 sudo ubuntu-hello version
# Arch has no pam-auth-update/authselect: the line is the user's to add, and the
# scenario adds it here the way the docs say so the login scenarios can run.
if pam_wired_by_package; then
	note pam_wired_by package
else
	note pam_wired_by manual
	pam_line_present || sudo sed -i '0,/^auth/s//auth       [success=done default=ignore]  pam_ubuntu_hello.so\nauth/' "$PAM_AUTH_FILE"
fi
check pam_line_present         pam_line_present
check pam_module_file          test -n "$(pam_module_path)"
check config_installed         test -f /etc/ubuntu-hello/config.ini
check config_has_confirmations grep -q '^confirmations = ' /etc/ubuntu-hello/config.ini
check config_dir_mode          test "$(stat -c %a /etc/ubuntu-hello)" = 755
check polkit_dropin            test -f /etc/systemd/system/polkit-agent-helper@.service.d/ubuntu-hello.conf
check polkit_dropin_tpm        grep -q 'DeviceAllow=char-tpm' /etc/systemd/system/polkit-agent-helper@.service.d/ubuntu-hello.conf
check tmpfiles_rule            test -f /usr/lib/tmpfiles.d/ubuntu-hello.conf
check runtime_dir_now          test -d /run/ubuntu-hello
check dlib_importable          python3 -c 'import dlib'
check pillow_importable        python3 -c 'import PIL'
check catalogs_installed       test "$(ls /usr/share/locale/*/LC_MESSAGES/ubuntu-hello*.mo | wc -l)" -ge 190
LIBDIR="$(libdir_path)"
check landmark_model           sh -c "d=\$(sudo python3 -c 'import sys; sys.path.insert(0, \"$LIBDIR\"); import paths_factory; print(paths_factory.dlib_data_dir_path())'); test -f \"\$d/shape_predictor_5_face_landmarks.dat\""
check config_file_not_executable test "$(stat -c %a /etc/ubuntu-hello/config.ini)" = 644
check models_dir_root_only     test "$(stat -c %a /etc/ubuntu-hello/models 2>/dev/null || echo 700)" = 700
check tpm_device_visible       test -e /dev/tpmrm0
note polkit_version "$(pkg_version polkitd || pkg_version polkit || echo none)"
note catalogs "$(ls /usr/share/locale/*/LC_MESSAGES/ubuntu-hello*.mo | wc -l)"
emit
