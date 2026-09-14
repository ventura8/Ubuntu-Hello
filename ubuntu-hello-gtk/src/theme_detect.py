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


def _gtk_settings_ini_theme(user: Optional[str]) -> str:
	"""``~/.config/gtk-4.0/settings.ini`` (KDE's kde-gtk-config, LXQt, manual setups)."""
	home = _user_home(user) if user else os.path.expanduser("~")
	for version in ("gtk-4.0", "gtk-3.0"):
		content = _read_file_text(os.path.join(home, ".config", version, "settings.ini"), user=user)
		for line in content.splitlines():
			key, _, value = line.partition("=")
			if key.strip().lower() == "gtk-theme-name" and value.strip():
				return value.strip().strip("'\"")
	return ""


def _gsettings_gtk_theme(user: Optional[str], schema: str) -> str:
	for getter in (
		["dconf", "read", f"/{schema.replace('.', '/')}/gtk-theme"],
		["gsettings", "get", schema, "gtk-theme"],
	):
		val = _run_cmd(getter, user=user)
		if val:
			return val
	return ""


def get_gtk_theme_name(user: Optional[str] = None, environ: Optional[dict] = None) -> str:
	"""The desktop's GTK theme name (e.g. ``Yaru-red-dark``) for *user*, or ''.

	Per DE, same sources as the dark/light probes: gsettings/dconf on GNOME,
	Budgie, Cinnamon and MATE; xfconf on XFCE; ``gtk-4.0/settings.ini`` on
	KDE/Plasma and LXQt (both write the GTK theme there), which is also the
	generic fallback for every desktop.
	"""
	desktop = detect_desktop(environ)
	name = ""
	if desktop in ("gnome", "budgie", "unknown"):
		name = _gsettings_gtk_theme(user, "org.gnome.desktop.interface")
	elif desktop == "cinnamon":
		name = _gsettings_gtk_theme(user, "org.cinnamon.desktop.interface") or _gsettings_gtk_theme(user, "org.gnome.desktop.interface")
	elif desktop == "mate":
		name = _gsettings_gtk_theme(user, "org.mate.interface")
	elif desktop == "xfce":
		name = _run_cmd(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"], user=user)
	elif desktop in ("kde", "lxqt"):
		name = _gtk_settings_ini_theme(user)
	if not name:
		name = _gtk_settings_ini_theme(user)
	if not name and desktop not in ("gnome", "budgie", "unknown"):
		name = _gsettings_gtk_theme(user, "org.gnome.desktop.interface")
	return name


def get_icon_theme_name(user: Optional[str] = None, environ: Optional[dict] = None, icons_dir: str = "/usr/share/icons") -> str:
	"""The desktop's icon theme (e.g. ``Yaru``) for *user* if installed system-wide, else ''."""
	desktop = detect_desktop(environ)
	schema = {"cinnamon": "org.cinnamon.desktop.interface", "mate": "org.mate.interface"}.get(desktop, "org.gnome.desktop.interface")
	name = ""
	if desktop == "xfce":
		name = _run_cmd(["xfconf-query", "-c", "xsettings", "-p", "/Net/IconThemeName"], user=user)
	else:
		for getter in (
			["dconf", "read", f"/{schema.replace('.', '/')}/icon-theme"],
			["gsettings", "get", schema, "icon-theme"],
		):
			name = _run_cmd(getter, user=user)
			if name:
				break
	if not name:
		home = _user_home(user) if user else os.path.expanduser("~")
		for version in ("gtk-4.0", "gtk-3.0"):
			for line in _read_file_text(os.path.join(home, ".config", version, "settings.ini"), user=user).splitlines():
				key, _, value = line.partition("=")
				if key.strip().lower() == "gtk-icon-theme-name" and value.strip():
					name = value.strip().strip("'\"")
					break
			if name:
				break
	if name and os.path.isfile(os.path.join(icons_dir, name, "index.theme")):
		return name
	return ""


def resolve_gtk4_theme(name: str, prefer_dark: bool, themes_dir: str = "/usr/share/themes") -> Optional[str]:
	"""Pick the installed GTK 4 variant of *name* matching *prefer_dark*.

	GTK 4 running as root (pkexec) never sees the user's theme, so the app sets
	``gtk-theme-name`` itself. Yaru ships accent variants (``Yaru-red``,
	``Yaru-red-dark``); keep the accent and swap the ``-dark`` suffix to match
	the light/dark preference. Returns None when no GTK 4 theme dir exists.
	"""
	if not name:
		return None
	base = name[:-5] if name.endswith("-dark") else name
	candidates = [f"{base}-dark", base] if prefer_dark else [base, f"{base}-dark"]
	for candidate in candidates:
		if os.path.isdir(os.path.join(themes_dir, candidate, "gtk-4.0")):
			return candidate
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
