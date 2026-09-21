#!/usr/bin/env bash
source /tmp/lib.sh
note boot_id_after "$(cat /proc/sys/kernel/random/boot_id)"
# /run is a fresh tmpfs every boot: the runtime dir exists only if the
# tmpfiles.d rule the package ships is honoured at boot.
check runtime_dir_recreated_at_boot test -d /run/ubuntu-hello
check runtime_dir_root_only        test "$(stat -c %a /run/ubuntu-hello)" = 700
check pam_line_survives            pam_line_present
# The polkit daemon must be reachable after reboot for any authorization prompt
# to work. It is D-Bus activated (inactive until first used on Fedora), so
# trigger it, then confirm it is active; the agent-helper the drop-in patches is
# a transient per-prompt @.service, not a persistent socket.
check polkit_daemon_reachable      sh -c 'sudo systemctl start polkit.service >/dev/null 2>&1; systemctl is-active --quiet polkit.service'
check tpm_still_visible            test -e /dev/tpmrm0
check password_login_still_works   sh -c "printf 'alice-pass\n' | timeout 60 sudo -u alice -i sudo -S -k true"
emit
