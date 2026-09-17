#!/usr/bin/env bash
# Half one: record state, ask the driver for a reboot (it reboots whenever an
# after-reboot half exists). Half two checks what only a boot can prove.
source /tmp/lib.sh
note boot_id_before "$(cat /proc/sys/kernel/random/boot_id)"
check pam_line_present_before pam_line_present
emit
