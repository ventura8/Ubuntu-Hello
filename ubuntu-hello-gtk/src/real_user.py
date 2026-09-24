"""Find the desktop user behind a process running as root (pkexec / sudo).

Shared by window.py, onboarding.py and authsticky.py. Each caller keeps its own
source *order* -- they deliberately differ (onboarding.py consults loginctl
before $USER; the others the reverse) -- and passes it to resolve().

os, pwd and subprocess are looked up at call time rather than imported by name,
so tests that patch those modules keep reaching these helpers.
"""
import os
import re

# A username as it may safely be passed to sudo/pkexec/loginctl. The optional
# trailing "$" admits Samba machine accounts.
USERNAME_RE = r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$"


def from_sudo():
	return os.environ.get("SUDO_USER")


def from_user_env():
	return os.environ.get("USER")


def from_pkexec():
	pkexec_uid = os.environ.get("PKEXEC_UID")
	if not pkexec_uid:
		return None
	try:
		import pwd
		return pwd.getpwuid(int(pkexec_uid)).pw_name
	except Exception:
		return None


def from_login():
	try:
		return os.getlogin()
	except Exception:
		return None


def from_loginctl():
	"""First non-root session owner reported by loginctl.

	timeout: a wedged logind must not block the caller -- for authsticky.py that
	is the authentication window itself.
	"""
	try:
		import subprocess
		out = subprocess.check_output(["loginctl", "list-sessions", "--no-legend"], text=True, timeout=5)
	except Exception:
		return None

	for line in out.strip().split("\n"):
		parts = line.split()
		if len(parts) >= 3 and parts[2] != "root":
			return parts[2]
	return None


def resolve(sources):
	"""First non-root user any of *sources* reports, or "root".

	Sources are called lazily, in order: each is only consulted if every one
	before it came up empty or returned root, since loginctl spawns a
	subprocess. A name that fails USERNAME_RE is rejected rather than handed on.
	"""
	for source in sources:
		candidate = source()
		if candidate and candidate != "root":
			return candidate if re.match(USERNAME_RE, candidate) else "root"
	return "root"
