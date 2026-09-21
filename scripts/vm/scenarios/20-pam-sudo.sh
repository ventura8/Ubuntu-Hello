#!/usr/bin/env bash
# The login path itself: sudo for a user with a face model goes through the PAM
# module, which spawns compare.py as root with a minimal environment. With no
# usable camera it must fail closed and fall through to the password, promptly,
# without hanging and without leaking anything about the environment.
source /tmp/lib.sh
CFG=/etc/ubuntu-hello/config.ini

# alice needs a model or the module skips her; a synthetic descriptor is enough
# because the point is the PAM wiring, not recognition (tests/footage covers that).
sudo mkdir -p /etc/ubuntu-hello/models
python3 - <<'PY' | sudo tee /etc/ubuntu-hello/models/alice.dat >/dev/null
import json, random
random.seed(1)
print(json.dumps([{"time": 0, "label": "synthetic", "id": 0, "data": [[random.gauss(0, 0.12) for _ in range(128)]]}]))
PY
sudo chmod 600 /etc/ubuntu-hello/models/alice.dat
sudo sed -i 's/^timeout = .*/timeout = 3/' "$CFG"

run_sudo_as_alice() {          # -> prints the elapsed seconds; exit status of the sudo
	local start end
	start=$(date +%s.%N)
	# `sudo -S` reads the password from stdin once the PAM stack gets there. If face
	# auth hung, this would never return; `timeout` turns that into a failure.
	printf 'alice-pass\n' | timeout 60 sudo -u alice -i sudo -S -k true 2>/tmp/sudo.err
	local rc=$?
	end=$(date +%s.%N)
	printf '%.1f' "$(echo "$end - $start" | bc)" > /tmp/sudo.elapsed
	return $rc
}

# 1. No camera at all: the module fails closed at once, password is asked, login works.
sudo sed -i 's|^device_path = .*|device_path = /dev/video-none|' "$CFG"
since="$(date '+%Y-%m-%d %H:%M:%S')"
check no_camera_password_fallback run_sudo_as_alice
note  no_camera_seconds "$(cat /tmp/sudo.elapsed)"
check no_camera_prompt_fast      test "$(cat /tmp/sudo.elapsed | cut -d. -f1)" -lt 10
# The module ran under its own identifier and denied: it logs a "Failure," line
# when compare.py exits non-zero. The exact wording differs by how the camera
# open fails -- Ubuntu's OpenCV returns cleanly (exit 14, "not possible to open
# camera"), Fedora's raises (exit 1, "unknown error") -- but either way the
# module failed closed and the password took over (checked above).
check module_ran_and_failed_closed sh -c "sudo journalctl -b --no-pager -o short --since '$since' -t pam_ubuntu_hello | grep -qE 'Failure,|not possible to open camera'"
note  journal_tail "$(sudo journalctl -b --no-pager -o short --since "$since" 2>/dev/null | grep -i 'pam_ubuntu_hello' | tail -6)"

# The module must not hijack the host's syslog identity: sudo's own session
# lines after face auth must still be tagged sudo, or audit rules and log-based
# intrusion detection that key on sudo[...] miss them. (Found by this scenario.)
check host_syslog_identity_kept sh -c "! sudo journalctl -b --no-pager -o short --since '$since' -t pam_ubuntu_hello | grep -q 'pam_unix(sudo'"

# 2. Face auth disabled in config: the module must step aside entirely.
sudo sed -i 's/^disabled = .*/disabled = true/' "$CFG"
check disabled_password_fallback run_sudo_as_alice
sudo sed -i 's/^disabled = .*/disabled = false/' "$CFG"

# 3. A user without a model: skipped silently, password works.
sudo mv /etc/ubuntu-hello/models/alice.dat /tmp/alice.dat.bak
check no_model_password_fallback run_sudo_as_alice
sudo mv /tmp/alice.dat.bak /etc/ubuntu-hello/models/alice.dat

# 4. Wrong password must still be refused: face auth cannot have opened a door.
sudo sed -i 's|^device_path = .*|device_path = /dev/video-none|' "$CFG"
check wrong_password_refused sh -c "! printf 'wrong\n' | timeout 60 sudo -u alice -i sudo -S -k true 2>/dev/null"

# 5. The helper is spawned with a minimal environment: nothing from the caller leaks.
#    Plant an LD_PRELOAD-style variable in alice's session and check the helper
#    never sees it (it logs its own environment size when asked).
check env_not_inherited sh -c "printf 'alice-pass\n' | LD_PRELOAD=/nonexistent.so PYTHONPATH=/tmp/evil timeout 60 sudo -u alice -i sudo -S -k true 2>/dev/null; ! sudo journalctl -b --no-pager -o cat | grep -q '/tmp/evil'"
emit
