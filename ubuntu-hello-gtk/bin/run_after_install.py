#!/usr/bin/env python3
"""Launch the Ubuntu Hello setup wizard after a live system install.

Meson / dpkg post-install hooks run as root. On Wayland (Ubuntu default) the
GUI needs the real user's session bus and runtime dir — DISPLAY/XAUTHORITY
alone is not enough, so older launches failed silently.
"""

from __future__ import annotations

import glob
import os
import pwd
import stat
import subprocess
import sys
from typing import Optional

LOG_PATH = "/tmp/ubuntu-hello-postinstall.log"
LOCK_PATH = "/tmp/ubuntu-hello-postinstall.lock"


def _open_secure(path: str, flags: int, mode: int = 0o644) -> int:
	"""Open *path* for privileged use without following symlinks.

	Rejects symlinks and non-regular files. If a leftover regular file is owned
	by another uid (common when a prior user-space test wrote under /tmp),
	replace it after ``lstat`` confirms it is not a symlink, then create a
	fresh euid-owned file with ``O_NOFOLLOW``.
	"""
	nofollow = getattr(os, "O_NOFOLLOW", 0)
	try:
		existing = os.lstat(path)
	except FileNotFoundError:
		existing = None

	if existing is not None:
		if stat.S_ISLNK(existing.st_mode):
			raise OSError(f"{path}: refuses symlink")
		if not stat.S_ISREG(existing.st_mode):
			raise OSError(f"{path}: not a regular file")
		if existing.st_uid != os.geteuid():
			# Drop foreign leftovers so root postinst can recreate safely.
			os.unlink(path)

	fd = os.open(path, flags | nofollow, mode)
	try:
		st = os.fstat(fd)
		if not stat.S_ISREG(st.st_mode):
			raise OSError(f"{path}: not a regular file")
		if st.st_uid != os.geteuid():
			raise OSError(f"{path}: unexpected owner uid {st.st_uid}")
	except Exception:
		os.close(fd)
		raise
	return fd


def _log(message: str) -> None:
	"""Append a line to the post-install log and echo to stdout."""
	line = message.rstrip() + "\n"
	try:
		fd = _open_secure(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
		try:
			os.write(fd, line.encode("utf-8"))
		finally:
			os.close(fd)
	except OSError:
		pass
	print(line, end="", flush=True)


def _acquire_single_flight_lock() -> Optional[int]:
	"""Return a lock fd if we should launch; None if another launch is in flight.

	Prevents double wizards when meson + install.sh (or two postinst hooks)
	both invoke this script within the same install.
	"""
	try:
		import fcntl
	except ImportError:
		return -1  # non-POSIX: skip locking

	try:
		fd = _open_secure(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
		fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		os.ftruncate(fd, 0)
		os.write(fd, f"{os.getpid()}\n".encode())
		return fd
	except OSError as exc:
		_log(f"post-install: lock unavailable ({exc}); skip launch")
		return None


def resolve_install_user() -> Optional[str]:
	"""Return the non-root user who should own the post-install GUI session."""
	real_user = os.environ.get("SUDO_USER")
	if real_user and real_user != "root":
		return real_user

	try:
		output = subprocess.check_output(
			["loginctl", "list-sessions", "--no-legend"],
			text=True,
			stderr=subprocess.DEVNULL,
		)
		for line in output.splitlines():
			parts = line.split()
			# Columns: SESSION UID USER SEAT TTY …
			if len(parts) >= 3 and parts[2] not in ("USER", "root"):
				return parts[2]
	except (OSError, subprocess.SubprocessError):
		pass

	try:
		for name in sorted(os.listdir("/home")):
			if name in ("lost+found",) or name.startswith("."):
				continue
			home = os.path.join("/home", name)
			if os.path.isdir(home):
				return name
	except OSError:
		pass

	return None


def _first_existing(*paths: str) -> Optional[str]:
	for path in paths:
		if path and os.path.exists(path):
			return path
	return None


def detect_wayland_display(runtime_dir: str) -> Optional[str]:
	"""Pick a usable WAYLAND_DISPLAY name under the user's runtime dir."""
	candidates = []
	env_name = os.environ.get("WAYLAND_DISPLAY")
	if env_name:
		candidates.append(env_name)
	candidates.extend(["wayland-0", "wayland-1"])

	seen = set()
	for name in candidates:
		if not name or name in seen:
			continue
		seen.add(name)
		# WAYLAND_DISPLAY may be a bare name or an absolute path.
		path = name if os.path.isabs(name) else os.path.join(runtime_dir, name)
		if os.path.exists(path):
			return os.path.basename(path) if os.path.isabs(name) else name

	# Fall back to any wayland-* socket in the runtime dir.
	for path in sorted(glob.glob(os.path.join(runtime_dir, "wayland-*"))):
		base = os.path.basename(path)
		if not base.endswith(".lock"):
			return base
	return None


def detect_xauthority(user: str, home: str, runtime_dir: str) -> Optional[str]:
	"""Locate an Xauthority file usable for X11 / XWayland clients."""
	env_path = os.environ.get("XAUTHORITY")
	candidates = [
		env_path,
		os.path.join(home, ".Xauthority"),
		os.path.join(runtime_dir, "gdm", "Xauthority"),
		os.path.join(runtime_dir, ".mutter-Xwaylandauth"),
	]
	# Mutter writes a random suffix: .mutter-Xwaylandauth.XXXXXX
	candidates.extend(sorted(glob.glob(os.path.join(runtime_dir, ".mutter-Xwaylandauth*"))))
	found = _first_existing(*[c for c in candidates if c])
	return found


def build_user_gui_env(user: str) -> dict[str, str]:
	"""Build an environment that can open GTK on X11 or Wayland for *user*."""
	try:
		pw = pwd.getpwnam(user)
	except KeyError as exc:
		raise ValueError(f"unknown user: {user}") from exc

	home = pw.pw_dir
	runtime_dir = f"/run/user/{pw.pw_uid}"
	env: dict[str, str] = {
		"HOME": home,
		"USER": user,
		"LOGNAME": user,
		"SHELL": pw.pw_shell or "/bin/bash",
		"PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
		"LANG": os.environ.get("LANG", "C.UTF-8"),
	}

	# Prefer the installing sudo session's locale/desktop hints when present.
	for key in (
		"LANGUAGE",
		"LC_ALL",
		"LC_MESSAGES",
		"LC_CTYPE",
		"XDG_CURRENT_DESKTOP",
		"DESKTOP_SESSION",
		"XDG_SESSION_TYPE",
		"XDG_SESSION_CLASS",
		"XDG_CONFIG_HOME",
	):
		val = os.environ.get(key)
		if val:
			env[key] = val

	if os.path.isdir(runtime_dir):
		env["XDG_RUNTIME_DIR"] = runtime_dir
		bus = os.path.join(runtime_dir, "bus")
		if os.path.exists(bus):
			env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"

	wayland = detect_wayland_display(runtime_dir) if os.path.isdir(runtime_dir) else None
	if wayland:
		env["WAYLAND_DISPLAY"] = wayland
		# Many sessions still expose an XWayland DISPLAY even on Wayland.
		env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))
	else:
		env["DISPLAY"] = os.environ.get("DISPLAY", ":0")

	xauth = detect_xauthority(user, home, runtime_dir)
	if xauth:
		env["XAUTHORITY"] = xauth

	return env


def launch_setup_wizard(user: str, env: dict[str, str]) -> subprocess.Popen:
	"""Start ubuntu-hello-gtk as *user* so onboarding can elevate via pkexec."""
	gtk_bin = "/usr/bin/ubuntu-hello-gtk"
	if not os.path.isfile(gtk_bin):
		# Fresh meson install may still be on PATH before the symlink settles.
		gtk_bin = "ubuntu-hello-gtk"

	cmd = ["sudo", "-u", user, "-H", "--"]
	# Clear then set a clean GUI env (sudo -u keeps a minimal root-ish env otherwise).
	cmd.append("env")
	cmd.append("-i")
	for key, value in sorted(env.items()):
		cmd.append(f"{key}={value}")
	# Force the setup wizard after install even if leftover model dirs exist.
	cmd.extend([gtk_bin, "--force-onboarding"])

	log_fd = _open_secure(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
	log_handle = os.fdopen(log_fd, "a", encoding="utf-8")
	log_handle.write(f"exec: {' '.join(cmd)}\n")
	log_handle.flush()
	return subprocess.Popen(
		cmd,
		stdout=log_handle,
		stderr=subprocess.STDOUT,
		start_new_session=True,
	)


def main() -> int:
	# Packaging/staging must never pop a GUI on the build host.
	if os.environ.get("DESTDIR"):
		return 0

	# Explicit opt-out (e.g. CI / ninja install without a session).
	if os.environ.get("UH_SKIP_POSTINSTALL_GUI") == "1":
		_log("post-install: UH_SKIP_POSTINSTALL_GUI=1; skip setup wizard launch")
		return 0

	lock_fd = _acquire_single_flight_lock()
	if lock_fd is None:
		return 0

	user = resolve_install_user()
	if not user or user == "root":
		_log("post-install: no graphical user found; skip setup wizard launch")
		return 0

	try:
		env = build_user_gui_env(user)
	except ValueError as exc:
		_log(f"post-install: {exc}")
		return 0

	if "XDG_RUNTIME_DIR" not in env and "DISPLAY" not in env:
		_log(f"post-install: no display session for {user}; skip setup wizard launch")
		return 0

	try:
		proc = launch_setup_wizard(user, env)
	except OSError as exc:
		_log(f"post-install: failed to launch setup wizard: {exc}")
		return 1

	session = env.get("WAYLAND_DISPLAY") or env.get("DISPLAY", "?")
	_log(
		f"Launched Ubuntu Hello setup wizard for user {user} "
		f"(pid {proc.pid}, session {session}); log: {LOG_PATH}"
	)
	# Keep lock_fd open for the lifetime of this process so a near-simultaneous
	# second caller still sees the lock; the OS releases it on exit.
	if lock_fd is not None and lock_fd >= 0:
		globals()["_POSTINSTALL_LOCK_FD"] = lock_fd
	return 0


if __name__ == "__main__":
	sys.exit(main())
