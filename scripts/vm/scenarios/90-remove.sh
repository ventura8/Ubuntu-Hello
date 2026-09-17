#!/usr/bin/env bash
# Purge must leave nothing: no files, no PAM line, no drop-in, no catalogs.
# The catalog leftover is exactly what the source uninstaller shipped with.
source /tmp/lib.sh
pkg_purge
check packages_gone        sh -c '! pkg_installed ubuntu-hello && ! pkg_installed ubuntu-hello-gtk'
check pam_line_gone        sh -c '! pam_line_present'
check pam_module_gone      test -z "$(pam_module_path)"
check config_dir_gone      sh -c '! test -e /etc/ubuntu-hello'
check libdir_gone          test -z "$(libdir_path)"
check gtk_libdir_gone      test -z "$(gtk_libdir_path)"
check catalogs_gone        test "$(ls /usr/share/locale/*/LC_MESSAGES/ubuntu-hello*.mo 2>/dev/null | wc -l)" = 0
check polkit_dropin_gone   sh -c '! ls /etc/systemd/system/polkit-agent-helper@.service.d/*.conf >/dev/null 2>&1'
check tmpfiles_rule_gone   sh -c '! test -e /usr/lib/tmpfiles.d/ubuntu-hello.conf'
check desktop_file_gone    sh -c '! test -e /usr/share/applications/ubuntu-hello-gtk.desktop'
check password_login_works sh -c "printf 'alice-pass\n' | timeout 60 sudo -u alice -i sudo -S -k true"
# apport / systemd-coredump keep a report for every crashed process; one for our
# programs means an unhandled exception happened somewhere in the run, whatever
# the checks said.
note crash_reports "$(crash_reports | head -5)"
check no_crash_reports     test -z "$(crash_reports | head -1)"
LEFTOVER_FIND=(sudo find / -xdev \( -path '*ubuntu-hello*' -o -path '*ubuntu_hello*' \)
               -not -path '/tmp/*' -not -path '/var/log/*' -not -path '/var/crash/*' -not -path '/var/lib/systemd/coredump/*'
               -not -path '/var/lib/apt/*' -not -path '/var/cache/apt/*' -not -path '/etc/apt/*'
               -not -path '/var/lib/dnf/*' -not -path '/var/cache/dnf/*' -not -path '/var/cache/libdnf5/*' -not -path '/var/lib/rpm/*'
               -not -path '/var/lib/pacman/*' -not -path '/var/cache/pacman/*')
note leftovers "$("${LEFTOVER_FIND[@]}" 2>/dev/null | head -20)"
check no_leftovers         test -z "$("${LEFTOVER_FIND[@]}" 2>/dev/null | head -1)"
emit
