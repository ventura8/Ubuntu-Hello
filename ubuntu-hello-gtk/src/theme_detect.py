"""Multi-desktop dark/light theme detection for Ubuntu Hello GTK.

Probes GNOME, KDE/Plasma, XFCE, Cinnamon, MATE, Budgie, and LXQt via
XDG_CURRENT_DESKTOP / DESKTOP_SESSION and DE-specific tools. Missing tools
or schemas fall back to light.
"""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys
import threading
from typing import Optional


# Repeated schema ids, config paths and filenames, named once so a typo cannot
# silently disable one DE's probe while the others keep working.
_XDG_CONFIG_DIR = ".config"
_SETTINGS_INI = "settings.ini"
_GTK3_DIR = "gtk-3.0"
_GTK4_DIR = "gtk-4.0"
_SCHEMA_GNOME = "org.gnome.desktop.interface"
_SCHEMA_CINNAMON = "org.cinnamon.desktop.interface"
_SCHEMA_MATE = "org.mate.interface"



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


def _kde_theme_from_tools(user: Optional[str]) -> Optional[str]:
	"""Ask kreadconfig6/5 for the colour scheme / look-and-feel package."""
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
	return None


def _kdeglobals_names_dark(content: str) -> bool:
	"""True if kdeglobals names a dark colour scheme or look-and-feel package."""
	for line in content.splitlines():
		if "=" not in line:
			continue
		key, _sep, value = line.partition("=")
		if key.strip().lower() in ("colorscheme", "lookandfeelpackage") and _name_is_dark(value.strip()):
			return True
	return False


def _kde_theme(user: Optional[str]) -> Optional[str]:
	"""Plasma: kreadconfig6/5 ColorScheme / LookAndFeel, else kdeglobals."""
	from_tools = _kde_theme_from_tools(user)
	if from_tools:
		return from_tools

	home = _user_home(user) if user else os.path.expanduser("~")
	content = _read_file_text(os.path.join(home, _XDG_CONFIG_DIR, "kdeglobals"), user=user)
	if _kdeglobals_names_dark(content):
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
		["gsettings", "get", _SCHEMA_MATE, "gtk-theme"],
		user=user,
	)
	if _name_is_dark(val):
		return "dark"
	if val:
		return "light"
	return None


def _lxqt_theme_in(path: str, user: Optional[str]) -> Optional[str]:
	"""Classify the "theme=" entry of one LXQt config file."""
	content = _read_file_text(path, user=user)
	for line in content.splitlines():
		if "=" not in line:
			continue
		key, _sep, value = line.partition("=")
		if key.strip().lower() != "theme":
			continue
		value = value.strip()
		if _name_is_dark(value):
			return "dark"
		if value:
			return "light"
	return None


def _lxqt_theme(user: Optional[str]) -> Optional[str]:
	home = _user_home(user) if user else os.path.expanduser("~")
	for rel in (
		os.path.join(_XDG_CONFIG_DIR, "lxqt", "lxqt.conf"),
		os.path.join(_XDG_CONFIG_DIR, "lxqt", "session.conf"),
	):
		theme = _lxqt_theme_in(os.path.join(home, rel), user)
		if theme:
			return theme
	return None


def _gtk_settings_ini_theme(user: Optional[str]) -> str:
	"""``~/.config/gtk-4.0/settings.ini`` (KDE's kde-gtk-config, LXQt, manual setups)."""
	home = _user_home(user) if user else os.path.expanduser("~")
	for version in (_GTK4_DIR, _GTK3_DIR):
		content = _read_file_text(os.path.join(home, _XDG_CONFIG_DIR, version, _SETTINGS_INI), user=user)
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
		name = _gsettings_gtk_theme(user, _SCHEMA_GNOME)
	elif desktop == "cinnamon":
		name = _gsettings_gtk_theme(user, _SCHEMA_CINNAMON) or _gsettings_gtk_theme(user, _SCHEMA_GNOME)
	elif desktop == "mate":
		name = _gsettings_gtk_theme(user, _SCHEMA_MATE)
	elif desktop == "xfce":
		name = _run_cmd(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"], user=user)
	elif desktop in ("kde", "lxqt"):
		name = _gtk_settings_ini_theme(user)
	if not name:
		name = _gtk_settings_ini_theme(user)
	if not name and desktop not in ("gnome", "budgie", "unknown"):
		name = _gsettings_gtk_theme(user, _SCHEMA_GNOME)
	return name


def _icon_theme_from_settings(user: Optional[str], desktop: str, schema: str) -> str:
	"""Icon theme as the DE's own settings store reports it."""
	if desktop == "xfce":
		return _run_cmd(["xfconf-query", "-c", "xsettings", "-p", "/Net/IconThemeName"], user=user)

	for getter in (
		["dconf", "read", f"/{schema.replace('.', '/')}/icon-theme"],
		["gsettings", "get", schema, "icon-theme"],
	):
		name = _run_cmd(getter, user=user)
		if name:
			return name
	return ""


def _icon_theme_from_gtk_ini(user: Optional[str]) -> str:
	"""Fall back to gtk-icon-theme-name in the user's GTK settings.ini."""
	home = _user_home(user) if user else os.path.expanduser("~")
	for version in (_GTK4_DIR, _GTK3_DIR):
		path = os.path.join(home, _XDG_CONFIG_DIR, version, _SETTINGS_INI)
		for line in _read_file_text(path, user=user).splitlines():
			key, _sep, value = line.partition("=")
			if key.strip().lower() == "gtk-icon-theme-name" and value.strip():
				return value.strip().strip("'\"")
	return ""


def get_icon_theme_name(user: Optional[str] = None, environ: Optional[dict] = None, icons_dir: str = "/usr/share/icons") -> str:
	"""The desktop's icon theme (e.g. ``Yaru``) for *user* if installed system-wide, else ''."""
	desktop = detect_desktop(environ)
	schema = {"cinnamon": _SCHEMA_CINNAMON, "mate": _SCHEMA_MATE}.get(desktop, _SCHEMA_GNOME)

	name = _icon_theme_from_settings(user, desktop, schema) or _icon_theme_from_gtk_ini(user)

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
		if os.path.isdir(os.path.join(themes_dir, candidate, _GTK4_DIR)):
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
		result = _gnome_family_theme(user, _SCHEMA_GNOME)
	elif desktop == "cinnamon":
		result = _gnome_family_theme(user, _SCHEMA_CINNAMON)
		if result is None:
			result = _gnome_family_theme(user, _SCHEMA_GNOME)
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
		result = _gnome_family_theme(user, _SCHEMA_GNOME)

	if result in ("dark", "light"):
		return result
	return default


# -- live theme following ---------------------------------------------------
#
# Elevated (pkexec) the app cannot subscribe to the user's dconf / xfconf: it
# runs as root with no access to the user's session bus, so Gio.Settings
# only ever sees root's (empty) settings. The desktop's own change feed is
# read instead, as the user, through a long-lived monitor process, and every
# line it prints is a "something changed" event for the caller.

def theme_monitor_command(user: Optional[str], environ: Optional[dict] = None) -> Optional[list]:
	"""argv of a process that prints one line per theme setting change, or None.

	GNOME / Budgie / unknown: ``gsettings monitor org.gnome.desktop.interface``
	Cinnamon: ``org.cinnamon.desktop.interface``; MATE: ``org.mate.interface``;
	XFCE: ``xfconf-query -m -c xsettings``. KDE / LXQt write the GTK theme to
	``~/.config/gtk-4.0/settings.ini`` (see :func:`theme_files_to_watch`).
	"""
	desktop = detect_desktop(environ)
	if desktop in ("gnome", "budgie", "unknown"):
		args = ["gsettings", "monitor", _SCHEMA_GNOME]
	elif desktop == "cinnamon":
		args = ["gsettings", "monitor", _SCHEMA_CINNAMON]
	elif desktop == "mate":
		args = ["gsettings", "monitor", _SCHEMA_MATE]
	elif desktop == "xfce":
		args = ["xfconf-query", "-m", "-c", "xsettings"]
	else:
		return None
	if user and os.geteuid() == 0 and user not in ("", "root"):
		# dconf delivers change notifications over the user's session bus:
		# without DBUS_SESSION_BUS_ADDRESS `gsettings monitor` prints nothing.
		return ["sudo", "-u", user, "-H", "env", f"HOME={_user_home(user)}", *_user_bus_env(user), *args]
	return list(args)


def _user_bus_env(user: str) -> list:
	"""``XDG_RUNTIME_DIR=…`` / ``DBUS_SESSION_BUS_ADDRESS=…`` for *user*'s session bus (if it exists)."""
	try:
		import pwd
		uid = pwd.getpwnam(user).pw_uid
	except Exception:
		return []
	runtime_dir = f"/run/user/{uid}"
	if not os.path.exists(os.path.join(runtime_dir, "bus")):
		return []
	return [f"XDG_RUNTIME_DIR={runtime_dir}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime_dir}/bus"]


def theme_files_to_watch(user: Optional[str]) -> list:
	"""Config files whose change means the GTK theme / colour scheme changed (KDE, LXQt, manual)."""
	home = _user_home(user) if user else os.path.expanduser("~")
	return [
		os.path.join(home, _XDG_CONFIG_DIR, _GTK4_DIR, _SETTINGS_INI),
		os.path.join(home, _XDG_CONFIG_DIR, _GTK3_DIR, _SETTINGS_INI),
		os.path.join(home, _XDG_CONFIG_DIR, "kdeglobals"),
		os.path.join(home, _XDG_CONFIG_DIR, "lxqt", "lxqt.conf"),
		os.path.join(home, _XDG_CONFIG_DIR, "lxqt", "session.conf"),
	]


class ThemeWatcher:
	"""Call *on_change()* (on the GLib main loop, debounced) whenever the user's theme changes.

	Runs the DE's monitor command as the user on a daemon thread; the process
	is terminated with :meth:`stop` (also registered with ``atexit`` so no
	``gsettings monitor`` outlives the app). Missing tools / no monitor for the
	desktop simply mean no live updates (startup detection still applies).
	"""

	DEBOUNCE_MS = 250

	def __init__(self, user, on_change, environ=None, popen=None, idle_add=None, timeout_add=None, file_monitor=True):
		self.user = user
		self.on_change = on_change
		self.environ = environ
		self._popen = popen or subprocess.Popen
		self._idle_add = idle_add
		self._timeout_add = timeout_add
		self._file_monitor = file_monitor
		self.process = None
		self.thread = None
		self.monitors = []
		self._pending = False
		self.events = 0

	# -- glue -------------------------------------------------------------
	def _glib(self):
		if self._idle_add is None or self._timeout_add is None:
			from gi.repository import GLib
			self._idle_add = self._idle_add or GLib.idle_add
			self._timeout_add = self._timeout_add or GLib.timeout_add
		return self._idle_add, self._timeout_add

	def _schedule(self, *_args):
		"""Coalesce bursts (a theme switch writes several keys) into one on_change()."""
		self.events += 1
		_idle_add, timeout_add = self._glib()
		if self._pending:
			return
		self._pending = True

		def fire():
			self._pending = False
			try:
				self.on_change()
			except Exception as exc:
				print(f"theme watcher: {exc}", file=sys.stderr)
			return False

		timeout_add(self.DEBOUNCE_MS, fire)

	def _read_loop(self, proc):
		try:
			for _line in proc.stdout:
				self._schedule()
		except Exception:
			pass

	# -- lifecycle --------------------------------------------------------
	def start(self):
		cmd = theme_monitor_command(self.user, self.environ)
		if cmd:
			try:
				self.process = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
				                           stdin=subprocess.DEVNULL, text=True, bufsize=1)
			except OSError:
				self.process = None
			if self.process is not None:
				self.thread = threading.Thread(target=self._read_loop, args=(self.process,), daemon=True)
				self.thread.start()
		if self._file_monitor:
			self._watch_files()
		atexit.register(self.stop)
		return self

	def _watch_files(self):
		try:
			from gi.repository import Gio
		except Exception:
			return
		for path in theme_files_to_watch(self.user):
			try:
				monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
				monitor.connect("changed", self._schedule)
				self.monitors.append(monitor)
			except Exception:
				continue

	def stop(self):
		proc, self.process = self.process, None
		if proc is not None:
			try:
				proc.terminate()
			except Exception:
				pass
		for monitor in self.monitors:
			try:
				monitor.cancel()
			except Exception:
				pass
		self.monitors = []
