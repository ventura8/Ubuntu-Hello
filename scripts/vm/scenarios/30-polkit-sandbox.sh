#!/usr/bin/env bash
# polkit >= 126 runs polkit-agent-helper-1 as a hardened transient unit
# (DevicePolicy=strict/closed, ProtectSystem=strict, ProtectHome). The shipped
# drop-in re-allows exactly what face auth and the TPM keyring need. This runs
# the same work under the same hardening, with and without the drop-in, so the
# sandbox defect that only a real polkit prompt found stays found.
source /tmp/lib.sh
DROPIN=/etc/systemd/system/polkit-agent-helper@.service.d/ubuntu-hello.conf
KEYS=/etc/ubuntu-hello/tpm-keys

# The hardening polkit applies, taken from its own unit on this system.
unit=$(systemctl cat polkit-agent-helper@.service 2>/dev/null | grep -E '^(DevicePolicy|ProtectSystem|ProtectHome|PrivateDevices|NoNewPrivileges)=' | sed 's/^/--property=/' | tr '\n' ' ')
note polkit_hardening "$unit"
check polkit_unit_is_hardened test -n "$unit"

# Properties from the drop-in, as the package installed it. Read into an array:
# "DeviceAllow=char-video4linux rw" holds a space, and word-splitting a string
# would hand "rw" to systemd-run as the command to execute.
mapfile -t DROPIN_PROPS < <(grep -E '^(DeviceAllow|ReadWritePaths|BindReadOnlyPaths|ProtectHome|PrivateDevices)=' "$DROPIN" | sed 's/^/--property=/')
note dropin "${DROPIN_PROPS[*]}"

# --pty, not --pipe: --pipe fails to start the transient unit on systemd 259
# (Fedora 44) with "Connection reset by peer". --pty gives the same faithful
# exit code, and anything the command needs to hand back it writes to a file
# under the (drop-in-writable) tpm-keys dir, which the outer shell reads.
sandboxed() {                  # sandboxed <with-dropin: 0|1> <command...>
	local with="$1"; shift
	local props=()
	[ "$with" = 1 ] && props=("${DROPIN_PROPS[@]}")
	sudo systemd-run --quiet --wait --collect --pty --unit "uh-sandbox-$RANDOM" \
		-p DevicePolicy=strict -p ProtectSystem=strict -p ProtectHome=yes -p PrivateDevices=no \
		"${props[@]}" -- "$@" >/dev/null 2>&1
}
fails_sandboxed() { ! sandboxed "$@"; }
# Runs the seal+unseal under the drop-in and confirms the recovered file holds
# the sealed value. A function, so the check calls it in this shell where
# sandboxed() is defined (a bare `sh -c "sandboxed ..."` would not see it).
unseal_recovers_secret() {
	sudo rm -f "$KEYS/recovered"
	sandboxed 1 sh -c "$seal_unseal" && [ "$(sudo cat "$KEYS/recovered" 2>/dev/null)" = secret ]
}

check tpm_present      test -e /dev/tpmrm0
check tpm_tools        command -v tpm2_createprimary
sudo mkdir -p "$KEYS"
sudo rm -f "$KEYS"/vmtest.* "$KEYS"/recovered

# Seal a secret and unseal it in one process (transient TPM handles do not
# survive across tpm2 invocations), writing the recovered value to a file. The
# work touches exactly what the drop-in controls: the TPM character device and
# write access to tpm-keys.
seal_unseal="cd $KEYS && tpm2_createprimary -C o -c vmtest.primary -Q \
	&& printf secret | tpm2_create -C vmtest.primary -u vmtest.pub -r vmtest.priv -i - -Q \
	&& tpm2_load -C vmtest.primary -u vmtest.pub -r vmtest.priv -c vmtest.ctx -Q \
	&& tpm2_unseal -c vmtest.ctx -o recovered"

# Without the drop-in the hardened unit cannot reach the TPM or write tpm-keys:
# this is the bug the drop-in exists for.
sudo rm -f "$KEYS/recovered"
check tpm_blocked_without_dropin fails_sandboxed 0 sh -c "$seal_unseal"
# With the drop-in the same work succeeds and recovers the exact secret.
check tpm_unseal_with_dropin unseal_recovers_secret

# The runtime dir the notifier needs is writable, and the home tree is hidden
# while the user runtime dirs stay visible for the session bus socket.
check runtime_dir_writable_with_dropin sandboxed 1 sh -c 'touch /run/ubuntu-hello/sandbox-probe && rm /run/ubuntu-hello/sandbox-probe'
check home_hidden_but_run_user_visible sandboxed 1 sh -c 'test -z "$(ls -A /home)" && test -d /run/user'
emit
