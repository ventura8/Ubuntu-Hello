#!/usr/bin/env bash
# A real upgrade from the previous release (1.1.6, from GitHub) to the packages
# under test, over a hand-edited config. Runs after the purge, so the system is
# clean. Note that 1.1.6's Debian package carries the missing-header defect: its
# postinstall can fail on a clean machine and leave dpkg half-configured. That is
# what real upgraders are coming from, so the upgrade must repair it.
source /tmp/lib.sh
CFG=/etc/ubuntu-hello/config.ini
PREV=1.1.6
RELEASE="https://github.com/ventura8/Ubuntu-Hello/releases/download/v${PREV}"
case "$PKG" in
	deb)    assets=("ubuntu-hello_${PREV}-1ppa1_amd64.deb" "ubuntu-hello-gtk_${PREV}-1ppa1_amd64.deb") ;;
	rpm)    assets=("ubuntu-hello-${PREV}-1.fc44.x86_64.rpm" "ubuntu-hello-gtk-${PREV}-1.fc44.x86_64.rpm") ;;
	pacman) assets=("ubuntu-hello-${PREV}-1-x86_64.pkg.tar.zst" "ubuntu-hello-gtk-${PREV}-1-x86_64.pkg.tar.zst") ;;
esac
mkdir -p /tmp/prev && cd /tmp/prev
for a in "${assets[@]}"; do
	curl -fsSL -o "$a" "$RELEASE/$a" >&2 || { note download_failed "$a"; emit; exit 0; }
done
note prev_version "$PREV"

prev_rc=0
pkg_install "${assets[@]/#/./}" || prev_rc=$?
note prev_install_exit "$prev_rc"
note prev_state "$(pkg_state ubuntu-hello)"
check prev_config_present test -f "$CFG"

# The user's edits, made on the old version.
sudo sed -i 's/^certainty = .*/certainty = 3.5/; s/^dark_threshold = .*/dark_threshold = 75/' "$CFG"
sudo sed -i '/^confirmations = /d' "$CFG"
echo "# keep-me: a comment the user added" | sudo tee -a "$CFG" >/dev/null
sudo mkdir -p /etc/ubuntu-hello/models
echo '[{"time": 0, "label": "kept", "id": 0, "data": [[0.0]]}]' | sudo tee /etc/ubuntu-hello/models/alice.dat >/dev/null

up_rc=0
if [ "${UH_SOURCE:-ppa}" = local ]; then
	mapfile -t pkgs < <(local_pkgs)
	pkg_upgrade_keeping_config "${pkgs[@]}" || up_rc=$?
else
	pkg_upgrade_keeping_config ubuntu-hello ubuntu-hello-gtk || up_rc=$?
fi
note upgrade_exit "$up_rc"
note upgraded_version "$(pkg_version ubuntu-hello)"
check upgrade_succeeded          test "$up_rc" = 0
check upgraded_package_configured pkg_installed ubuntu-hello
check upgraded_gtk_configured    pkg_installed ubuntu-hello-gtk
check upgrade_repaired_dlib      sudo python3 -c 'import dlib'
check kept_hand_edits            grep -q '^dark_threshold = 75' "$CFG"
check kept_threshold             grep -q '^certainty = 3.5' "$CFG"
check kept_user_comment          grep -q 'keep-me' "$CFG"
check kept_models                sudo test -f /etc/ubuntu-hello/models/alice.dat
check migration_added_key        grep -q '^confirmations = ' "$CFG"
check migration_used_fast_level  test "$(grep '^confirmations = ' "$CFG" | awk '{print $3}')" = 2
pam_wired_by_package || pam_line_present || sudo sed -i '0,/^auth/s//auth       [success=done default=ignore]  pam_ubuntu_hello.so\nauth/' "$PAM_AUTH_FILE"
check pam_line_present           pam_line_present
check runtime_dir_present        test -d /run/ubuntu-hello
check password_login_works       sh -c "printf 'alice-pass\n' | timeout 60 sudo -u alice -i sudo -S -k true"
emit
