"""Multi-desktop dark/light theme detection for Ubuntu Hello GTK.

Probes GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, and LXQt via
XDG_CURRENT_DESKTOP / DESKTOP_SESSION and DE-specific tools. Missing tools
or schemas fall back to light.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional


def detect_desktop(environ: Optional[dict] = None) -> str:
	"""Return a normalized DE id: gnome, kde, xfce, cinnamon, mate, budgie, lxqt, or unknown."""
	env = environ if environ is not None else os.environ
	raw = (env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or "").lower()
	tokens = [t for t in re.split(r"[:\s;,]+", raw) if t]
	joined = " ".join(tokens)

	def has(*names: str) -> bool:
		return any(n in tokens or n in joined for n in names)

	if has("kde", "plasma"):
		return "kde"
	if has("xfce", "xubuntu"):
		return "xfce"
	if has("cinnamon"):
		return "cinnamon"
	if has("mate"):
		return "mate"
	if has("budgie"):
		return "budgie"
	if has("lxqt", "lubuntu"):
		return "lxqt"
	if has("gnome", "ubuntu", "unity", "pop"):
		return "gnome"
	return "unknown"


def _user_home(user: str) -> str:
	try:
		import pwd
		return pwd.getpwnam(user).pw_dir
	except Exception:
		return f"/home/{user}"


def _run_cmd(args: list, user: Optional[str] = None, timeout: float = 2.0) -> str:
	"""Run a command, optionally as *user* when elevated. Returns stripped stdout or ''."""
	try:
		if user and os.geteuid() == 0 and user not in ("", "root"):
			home = _user_home(user)
			cmd = ["sudo", "-u", user, "env", f"HOME={home}", *args]
		else:
			cmd = list(args)
		out = subprocess.check_output(
			cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout
		)
		return out.strip().strip("'\"")
	except Exception:
		return ""


def _name_is_dark(name: str) -> bool:
	return bool(name) and "dark" in name.lower()


def _read_file_text(path: str, user: Optional[str] = None) -> str:
	"""Read a small config file; when elevated, read as *user* via sudo cat."""
	try:
		if user and os.geteuid() == 0 and user not in ("", "root"):
			return _run_cmd(["cat", path], user=user)
		with open(path, "r", encoding="utf-8", errors="ignore") as fh:
			return fh.read()
	except Exception:
		return ""


def _gnome_family_theme(user: Optional[str], schema: str) -> Optional[str]:
	"""GNOME / Budgie / Cinnamon-style gsettings + dconf color-scheme / gtk-theme."""
	# color-scheme (prefer-dark / prefer-light) — GNOME 42+
	for getter in (
		["dconf", "read", f"/{schema.replace('.', '/')}/color-scheme"],
		["gsettings", "get", schema, "color-scheme"],
	):
		val = _run_cmd(getter, user=user)
		if val == "prefer-dark":
			return "dark"
		if val == "prefer-light":
			return "light"

	# gtk-theme name containing "dark"
	for getter in (
		["dconf", "read", f"/{schema.replace('.', '/')}/gtk-theme"],
		["gsettings", "get", schema, "gtk-theme"],
	):
		val = _run_cmd(getter, user=user)
		if _name_is_dark(val):
			return "dark"
		if val:
			return "light"
	return None


def _kde_theme(user: Optional[str]) -> Optional[str]:
	"""Plasma: kreadconfig6/5 ColorScheme / LookAndFeel, else kdeglobals."""
	for key, group in (
		("ColorScheme", "General"),
		("LookAndFeelPackage", "KDE"),
	):
		for tool in ("kreadconfig6", "kreadconfig5"):
			val = _run_cmd(
				[tool, "--file", "kdeglobals", "--group", group, "--key", key],
				user=user,
			)
			if _name_is_dark(val):
				return "dark"
			if val:
				# Explicit light-ish schemes without "dark"
				return "light"

	home = _user_home(user) if user else os.path.expanduser("~")
	content = _read_file_text(os.path.join(home, ".config", "kdeglobals"), user=user)
	for line in content.splitlines():
		if "=" not in line:
			continue
		key, _, value = line.partition("=")
		key = key.strip().lower()
		if key in ("colorscheme", "lookandfeelpackage") and _name_is_dark(value.strip()):
			return "dark"
	if content:
		return "light"
	return None


def _xfce_theme(user: Optional[str]) -> Optional[str]:
	val = _run_cmd(
		["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"],
		user=user,
	)
	if _name_is_dark(val):
		return "dark"
	if val:
		return "light"
	return None


def _mate_theme(user: Optional[str]) -> Optional[str]:
	val = _run_cmd(
		["gsettings", "get", "org.mate.interface", "gtk-theme"],
		user=user,
	)
	if _name_is_dark(val):
		return "dark"
	if val:
		return "light"
	return None


def _lxqt_theme(user: Optional[str]) -> Optional[str]:
	home = _user_home(user) if user else os.path.expanduser("~")
	for rel in (
		os.path.join(".config", "lxqt", "lxqt.conf"),
		os.path.join(".config", "lxqt", "session.conf"),
	):
		content = _read_file_text(os.path.join(home, rel), user=user)
		for line in content.splitlines():
			if "=" not in line:
				continue
			key, _, value = line.partition("=")
			if key.strip().lower() == "theme":
				if _name_is_dark(value.strip()):
					return "dark"
				if value.strip():
					return "light"
	return None


def get_theme_preference(
	user: Optional[str] = None,
	default: str = "light",
	environ: Optional[dict] = None,
) -> str:
	"""Detect dark/light preference for *user* (or current process).

	Returns ``\"dark\"`` or ``\"light\"``. On any failure returns *default*.
	"""
	desktop = detect_desktop(environ)

	result: Optional[str] = None
	if desktop in ("gnome", "budgie", "unknown"):
		result = _gnome_family_theme(user, "org.gnome.desktop.interface")
	elif desktop == "cinnamon":
		result = _gnome_family_theme(user, "org.cinnamon.desktop.interface")
		if result is None:
			result = _gnome_family_theme(user, "org.gnome.desktop.interface")
	elif desktop == "kde":
		result = _kde_theme(user)
	elif desktop == "xfce":
		result = _xfce_theme(user)
	elif desktop == "mate":
		result = _mate_theme(user)
	elif desktop == "lxqt":
		result = _lxqt_theme(user)

	# Last-resort GNOME probe when DE unknown or probe failed
	if result is None and desktop not in ("gnome", "budgie"):
		result = _gnome_family_theme(user, "org.gnome.desktop.interface")

	if result in ("dark", "light"):
		return result
	return default
