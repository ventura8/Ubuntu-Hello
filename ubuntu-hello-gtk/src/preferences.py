# User UI preferences (~/.config/ubuntu-hello/preferences.ini).
# Language override is Python-only (CLI/GTK/compare); PAM stays on system locale.

from __future__ import annotations

import configparser
import os
import pwd
from typing import Optional

PREFS_DIR_NAME = "ubuntu-hello"
PREFS_FILE_NAME = "preferences.ini"
SECTION_UI = "ui"
KEY_LANGUAGE = "language"
AUTO = "auto"


def _real_user_home() -> Optional[str]:
	"""Home of the elevating user when Settings/CLI run as root via pkexec/sudo."""
	for env_key in ("SUDO_USER",):
		user = os.environ.get(env_key)
		if user and user != "root":
			try:
				return pwd.getpwnam(user).pw_dir
			except KeyError:
				pass
	pkexec_uid = os.environ.get("PKEXEC_UID")
	if pkexec_uid:
		try:
			return pwd.getpwuid(int(pkexec_uid)).pw_dir
		except (ValueError, KeyError, OverflowError):
			pass
	return None


def preferences_path() -> str:
	"""Path to preferences.ini (user-scoped; honors elevated real user)."""
	override = os.environ.get("UH_PREFERENCES_FILE")
	if override:
		return override

	home = _real_user_home()
	if home:
		return os.path.join(home, ".config", PREFS_DIR_NAME, PREFS_FILE_NAME)

	xdg = os.environ.get("XDG_CONFIG_HOME")
	if xdg:
		return os.path.join(xdg, PREFS_DIR_NAME, PREFS_FILE_NAME)

	return os.path.join(os.path.expanduser("~"), ".config", PREFS_DIR_NAME, PREFS_FILE_NAME)


def read_language() -> str:
	"""Return language code or 'auto' (default). Invalid/missing → auto."""
	path = preferences_path()
	if not os.path.isfile(path):
		return AUTO
	parser = configparser.ConfigParser()
	try:
		parser.read(path, encoding="utf-8")
	except (configparser.Error, OSError):
		return AUTO
	if not parser.has_section(SECTION_UI):
		return AUTO
	value = parser.get(SECTION_UI, KEY_LANGUAGE, fallback=AUTO).strip().lower()
	if not value or value == AUTO:
		return AUTO
	return value


def write_language(code: str) -> None:
	"""Persist language preference (auto or ISO code). Creates dir as needed."""
	code = (code or AUTO).strip().lower() or AUTO
	path = preferences_path()
	directory = os.path.dirname(path)
	os.makedirs(directory, mode=0o700, exist_ok=True)

	parser = configparser.ConfigParser()
	if os.path.isfile(path):
		try:
			parser.read(path, encoding="utf-8")
		except (configparser.Error, OSError):
			parser = configparser.ConfigParser()
	if not parser.has_section(SECTION_UI):
		parser.add_section(SECTION_UI)
	parser.set(SECTION_UI, KEY_LANGUAGE, code)

	with open(path, "w", encoding="utf-8") as handle:
		parser.write(handle)
	try:
		os.chmod(path, 0o600)
	except OSError:
		pass

	# When elevated, own the prefs as the real user so the session can update them.
	home = _real_user_home()
	if home and os.geteuid() == 0:
		try:
			st = os.stat(home)
			os.chown(directory, st.st_uid, st.st_gid)
			os.chown(path, st.st_uid, st.st_gid)
		except OSError:
			pass
