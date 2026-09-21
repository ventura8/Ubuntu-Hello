#!/usr/bin/env bash
# The greeter hard rule, without a display manager. Under the gdm-password
# service with `workaround = off`, the module must never ask for a password
# itself: forcing pam_get_authtok there aborts GDM's user-selection → login
# (the account list bounces back). pam_probe.py runs the real PAM stack for a
# service and counts the prompts the conversation receives, which is exactly
# where that failure would show. `sudo` is the control: same stack, same rule.
source /tmp/lib.sh
CFG=/etc/ubuntu-hello/config.ini
PROBE="sudo python3 /tmp/pam_probe.py"

# GDM's own service file, as shipped in gdm, minus the lines that need a session
# (only `auth` matters to pam_authenticate); the include of the distro's common
# stack is what differs. Written only if absent.
if [ ! -f /etc/pam.d/gdm-password ]; then
	printf '%s\n' 'auth    requisite       pam_nologin.so' \
	              'auth    required        pam_succeed_if.so user != root quiet_success' \
	              "$PAM_INCLUDE_LINE" | sudo tee /etc/pam.d/gdm-password >/dev/null
	note wrote_gdm_service_file true
fi

sudo sed -i 's|^device_path = .*|device_path = /dev/video-none|; s/^timeout = .*/timeout = 3/' "$CFG"
since="$(date '+%Y-%m-%d %H:%M:%S')"
probe() { $PROBE "$1" alice "$2"; }          # -> JSON line
field() { python3 -c "import json,sys; print(json.loads(sys.stdin.readline())['$1'])"; }

# 1. workaround=off (the shipped default): one password prompt, from pam_unix, and success.
sudo sed -i 's/^workaround = .*/workaround = off/' "$CFG"
out=$(probe gdm-password alice-pass); note greeter_off "$out"
check greeter_off_succeeds       test "$(printf '%s' "$out" | field result)" = 0
check greeter_off_single_prompt  test "$(printf '%s' "$out" | field prompts)" = 1
out=$(probe gdm-password wrong-pass); note greeter_off_wrong "$out"
check greeter_off_wrong_refused  test "$(printf '%s' "$out" | field result)" != 0

# 2. The same under sudo, as the control.
out=$(probe sudo alice-pass); note sudo_off "$out"
check sudo_off_succeeds          test "$(printf '%s' "$out" | field result)" = 0
check sudo_off_single_prompt     test "$(printf '%s' "$out" | field prompts)" = 1

# 3. workaround=input: the module may ask concurrently, but a correct password
#    must still get through and a wrong one must still be refused.
sudo sed -i 's/^workaround = .*/workaround = input/' "$CFG"
out=$(probe gdm-password alice-pass); note greeter_input "$out"
check greeter_input_succeeds     test "$(printf '%s' "$out" | field result)" = 0
out=$(probe gdm-password wrong-pass); note greeter_input_wrong "$out"
check greeter_input_wrong_refused test "$(printf '%s' "$out" | field result)" != 0
sudo sed -i 's/^workaround = .*/workaround = off/' "$CFG"

# 4. A greeter must never be skipped by the face-skip marker meant for legacy
#    screensaver services: after a failed attempt, gdm-password still runs.
out=$(probe gdm-password alice-pass); note greeter_after_failure "$out"
check greeter_still_runs_after_failure test "$(printf '%s' "$out" | field result)" = 0
# The single prompt must be pam_unix's, with the module having actually run for
# the gdm-password service and not been skipped as an ignored service: its own
# journal lines during the probe window prove it was invoked.
note module_journal "$(sudo journalctl -b --no-pager -o short --since "$since" -t pam_ubuntu_hello 2>/dev/null | tail -8)"
check module_ran_for_the_greeter sh -c "test \"\$(sudo journalctl -b --no-pager -o cat --since '$since' -t pam_ubuntu_hello | grep -c .)\" -ge 3"
emit
