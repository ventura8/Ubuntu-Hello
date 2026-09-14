# Desktop notifications for face authentication (org.freedesktop.Notifications)
#
# compare.py runs as root with an empty environment during PAM auth, so a
# notification cannot simply be sent on "the" session bus: it is delivered on
# the *target user's* session bus (/run/user/<uid>/bus) by dropping privileges
# to that user for the gdbus call. Everything here is best-effort -- a greeter
# login (no user session yet), a missing gdbus, or a headless host must never
# slow down or break authentication, so every failure degrades to "no
# notification" and the reason is kept for the end report.
#
# One notification is created when the scan starts and then *updated in place*
# (replaces_id) with the outcome, so the user sees a single card change from
# "Looking for your face..." to "Face recognized" / "Face not recognized" with
# the numbers that explain it (certainty vs threshold, frames, fps, elapsed).
# The id is persisted per user so the *next* attempt updates the same card
# too instead of stacking a new one per auth. As root (PAM) that state lives
# in a root-owned directory under /run/ubuntu-hello -- never in the user's
# own /run/user/<uid>, where a planted symlink would let root's open() /
# chown() be redirected at arbitrary files. Every file open uses O_NOFOLLOW.
#
# Body layout: ONE line. GNOME Shell replaces newlines with spaces and shows
# the collapsed banner as a single ellipsized line, so the essentials come
# first and diagnostics last; only <b>/<i>/<u> markup is rendered.

import html
import os
import pwd
import subprocess
import threading
import time

from i18n import _

# Absolute path: never resolve via an inherited PATH (see compare.py header).
GDBUS_PATH = "/usr/bin/gdbus"

APP_NAME = "Ubuntu Hello"
# The app icon (pixmap from the GTK package, cropped to its drawn bounds so it
# renders large) is used on every card; the server falls back to a generic
# icon when it is not installed.
APP_ICON = "ubuntu-hello-gtk"
# Same value on every card so Ubuntu/GNOME-style servers coalesce them into a
# single synchronous "status" bubble instead of stacking a new one per auth.
SYNCHRONOUS_TAG = "ubuntu-hello-auth"

# Where the last notification id per user is kept so consecutive attempts
# replace the same card. Root (PAM): ROOT_STATE_DIR/<uid>.id (root-only dir,
# gone on reboot). A user's own dry run: $XDG_RUNTIME_DIR/ID_FILE.
ROOT_STATE_DIR = "/run/ubuntu-hello/notify"
ID_FILE = "ubuntu-hello-notify.id"

URGENCY_LOW = 0
URGENCY_NORMAL = 1
URGENCY_CRITICAL = 2

# How long the final card stays (ms). 0 = until updated, -1 = server default.
# GNOME ignores these for its banner timing, so the success card is removed
# explicitly after `success_linger` seconds (see AuthNotifier._close_later).
EXPIRE_PROGRESS = 0
EXPIRE_SUCCESS = 5000
EXPIRE_FAILURE = 10000
SH_PATH = "/bin/sh"


def gvariant_string(value):
	"""Quote *value* as a GVariant text-format string literal."""
	value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
	return '"' + value + '"'


def gvariant_hints(hints):
	"""Render a Python dict as a GVariant a{sv} literal for gdbus.

	Supported value types: bool, int (int32), bytes-like 'byte' via
	("byte", n), and str. Unknown types are skipped rather than sent wrong.
	"""
	parts = []
	for key, value in hints.items():
		if isinstance(value, tuple) and len(value) == 2 and value[0] == "byte":
			rendered = "<byte %d>" % int(value[1])
		elif isinstance(value, bool):
			rendered = "<true>" if value else "<false>"
		elif isinstance(value, int):
			rendered = "<int32 %d>" % value
		elif isinstance(value, str):
			rendered = "<%s>" % gvariant_string(value)
		else:
			continue
		parts.append("%s: %s" % (gvariant_string(key), rendered))
	return "{" + ", ".join(parts) + "}"


def parse_notification_id(output):
	"""Extract the uint32 id from gdbus output like '(uint32 42,)'."""
	text = (output or "").strip()
	marker = "uint32 "
	start = text.find(marker)
	if start == -1:
		return 0
	digits = ""
	for ch in text[start + len(marker):]:
		if ch.isdigit():
			digits += ch
		else:
			break
	return int(digits) if digits else 0


def session_bus_for_user(user):
	"""Return (uid, gid, bus_path) for *user*, or None if they have no session bus."""
	try:
		pw = pwd.getpwnam(user)
	except KeyError:
		return None
	bus_path = "/run/user/%d/bus" % pw.pw_uid
	if not os.path.exists(bus_path):
		return None
	return pw.pw_uid, pw.pw_gid, bus_path


def _private_dir(path):
	"""Create *path* (and parents) as a root-only directory; True if usable.

	Refuses anything that is not a real directory owned by the caller with no
	group/other bits, so a pre-planted symlink or a foreign directory can
	never redirect the state files.
	"""
	try:
		os.makedirs(path, mode=0o700, exist_ok=True)
		st = os.lstat(path)
	except OSError:
		return False
	import stat
	return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and (st.st_mode & 0o077) == 0


def state_paths(uid, runtime_dir):
	"""Return the id-file path for this process, or None if it cannot be kept safely."""
	if os.geteuid() == 0:
		if not _private_dir(ROOT_STATE_DIR):
			return None
		return os.path.join(ROOT_STATE_DIR, "%d.id" % uid)
	return os.path.join(runtime_dir, ID_FILE)


def read_saved_id(path):
	"""Return the persisted notification id at *path*, or 0."""
	if not path:
		return 0
	try:
		fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
	except OSError:
		return 0
	try:
		with os.fdopen(fd, "r", encoding="ascii") as fh:
			return int(fh.read().strip() or 0)
	except (OSError, ValueError):
		return 0


def write_saved_id(path, notification_id):
	"""Persist *notification_id* at *path* (best-effort, never following symlinks)."""
	if not path:
		return
	try:
		fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
		with os.fdopen(fd, "w", encoding="ascii") as fh:
			fh.write("%d\n" % notification_id)
	except OSError:
		pass


class AuthNotifier:
	"""Create one desktop notification for a face-auth attempt and update it in place."""

	def __init__(self, user, service="", enabled=True, details=False, sound=True, success_linger=3.0, call_timeout=2.0):
		self.user = user
		# Play the result sounds (service-login / dialog-warning hints).
		self.sound = sound
		# PAM service that is authenticating (sudo, polkit-1, gdm-password, ...).
		self.service = service or ""
		self.enabled = enabled
		self.details = details
		# Seconds the "recognized" card stays after the scan; 0 keeps it.
		self.success_linger = success_linger
		self.call_timeout = call_timeout
		self.notification_id = 0
		self.disabled_reason = None
		self.started_at = time.time()
		self._lock = threading.Lock()
		self._bus = None
		# Set once a result card went out; a late in-progress card must not
		# overwrite it (start() sends asynchronously).
		self._finalized = False
		# Detached watchdog spawned by start(); killed when a result arrives.
		self._watchdog = None
		# Context given to start() (device, resolution, ...) reused on the result card.
		self.context = {}
		if not enabled:
			self.disabled_reason = "disabled in config"
			return
		if not os.path.exists(GDBUS_PATH):
			self.disabled_reason = "gdbus not installed"
			return
		self._bus = session_bus_for_user(user)
		if self._bus is None:
			self.disabled_reason = "no session bus for user"
			return
		self.id_path = state_paths(self._bus[0], os.path.dirname(self._bus[2]))
		self.notification_id = read_saved_id(self.id_path)

	# -- transport -----------------------------------------------------------

	def _spawn_kwargs(self):
		"""Environment + identity for running a helper as the target user.

		Privileges are dropped with subprocess's own user/group/extra_groups
		(applied in C between fork and exec), never with preexec_fn: compare.py
		is multi-threaded and Python code in the forked child can deadlock.
		"""
		uid, gid, bus_path = self._bus
		kwargs = {
			"env": {
				"DBUS_SESSION_BUS_ADDRESS": "unix:path=" + bus_path,
				"XDG_RUNTIME_DIR": os.path.dirname(bus_path),
				"PATH": "/usr/bin:/bin",
			},
			"close_fds": True,
		}
		# Only root can (and needs to) switch identity; a dry run as the
		# user themselves already owns the bus.
		if os.geteuid() == 0:
			kwargs.update(user=uid, group=gid, extra_groups=[])
		return kwargs

	def _run_as_user(self, argv):
		"""Run *argv* on the user's session bus, dropping root to that user."""
		return subprocess.run(
			argv,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			timeout=self.call_timeout,
			**self._spawn_kwargs(),
		)

	def _close_later(self, delay):
		"""Remove our card *delay* seconds from now, from a detached helper.

		compare.py exits (and PAM returns) right after the result, so the
		sleep + CloseNotification run in a separate session as the user: the
		PAM module's process-group SIGTERM on cancel/exit cannot reach it,
		and it needs no privileges the user does not have. Returns the Popen
		(so start()'s watchdog can be cancelled once a real result arrives)
		or None.
		"""
		if self.disabled_reason or not self.notification_id or delay <= 0:
			return None
		script = "sleep %s; exec %s call --session --dest org.freedesktop.Notifications " \
			"--object-path /org/freedesktop/Notifications " \
			"--method org.freedesktop.Notifications.CloseNotification %d" % (
				"%.1f" % delay, GDBUS_PATH, self.notification_id)
		try:
			return subprocess.Popen(
				[SH_PATH, "-c", script],
				start_new_session=True,
				stdin=subprocess.DEVNULL,
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				**self._spawn_kwargs(),
			)
		except OSError:
			return None

	def _cancel_watchdog(self):
		"""A result card replaced the progress card: its watchdog is no longer needed."""
		watchdog, self._watchdog = self._watchdog, None
		if watchdog is None:
			return
		try:
			watchdog.kill()
		except OSError:
			pass

	def _notify(self, summary, body, urgency, expire_ms, hints=None, icon=APP_ICON, final=True):
		"""Send org.freedesktop.Notifications.Notify, reusing our id to update in place.

		Result cards are *final*: once one went out, a late in-progress card
		(start() sends from a thread) is dropped instead of overwriting it.
		"""
		if self.disabled_reason:
			return False
		all_hints = {
			"urgency": ("byte", urgency),
			"category": "presence",
			# No "desktop-entry": with it GNOME Shell files the card under the
			# app and (observed on 26.04) shows no banner at all for it.
			"x-canonical-private-synchronous": SYNCHRONOUS_TAG,
			# Deliberately NOT "transient": the outcome card must stay in the
			# notification list so a failed attempt can be inspected later.
		}
		all_hints.update(hints or {})
		with self._lock:
			if final:
				self._finalized = True
				self._cancel_watchdog()
			elif self._finalized:
				return False
			argv = [
				GDBUS_PATH, "call", "--session",
				"--dest", "org.freedesktop.Notifications",
				"--object-path", "/org/freedesktop/Notifications",
				"--method", "org.freedesktop.Notifications.Notify",
				APP_NAME,
				str(self.notification_id),
				icon,
				summary,
				body,
				"[]",
				gvariant_hints(all_hints),
				str(expire_ms),
			]
			try:
				result = self._run_as_user(argv)
			except (OSError, subprocess.SubprocessError) as err:
				self.disabled_reason = "gdbus failed: %s" % err
				return False
			if result.returncode != 0:
				self.disabled_reason = "notify error: %s" % result.stderr.strip()[:200]
				return False
			new_id = parse_notification_id(result.stdout)
			if new_id and new_id != self.notification_id:
				self.notification_id = new_id
				write_saved_id(self.id_path, new_id)
			return True

	# -- content -------------------------------------------------------------

	# -- formatting ----------------------------------------------------------

	SEP = " · "

	def requester(self):
		"""Human label for the PAM service asking for authentication, or ''.

		A greeter login has no session bus (no card at all), so a
		gdm-password/lightdm/sddm service that *does* reach a session bus is
		a screen unlock.
		"""
		svc = self.service.lower()
		if not svc:
			return ""
		if svc in ("sudo", "sudo-i"):
			return _("sudo")
		if svc in ("su", "su-l"):
			return _("su")
		if svc.startswith("polkit"):
			return _("authorization")
		if svc in ("gdm-password", "gdm-launch-environment", "login", "lightdm", "lightdm-greeter", "sddm", "sddm-greeter", "gnome-screensaver", "xdg-screensaver", "i3lock", "swaylock", "hyprlock"):
			return _("screen unlock")
		return svc

	def title(self, base):
		"""'Face recognized' -> 'Face recognized · sudo' when the service is known."""
		who = self.requester()
		return base + self.SEP + who if who else base

	def display_name(self):
		"""The user's full name from the passwd GECOS field, else the login."""
		try:
			full = pwd.getpwnam(self.user).pw_gecos.split(",")[0].strip()
		except KeyError:
			full = ""
		return full or self.user

	@staticmethod
	def _b(text):
		return "<b>" + html.escape(str(text)) + "</b>"

	def _line(self, *parts):
		return self.SEP.join(p for p in parts if p)

	def _debug_part(self, extra):
		"""Extended diagnostics appended (last) when [notifications] details = true."""
		if not self.details:
			return ""
		merged = dict(self.context)
		merged.update({k: v for k, v in extra.items() if v not in (None, "")})
		parts = ["🔧 pid " + self._b(os.getpid()), "uid " + self._b(os.geteuid())]
		if self.service:
			parts.append(html.escape(self.service))
		if merged.get("device"):
			parts.append(html.escape(os.path.basename(str(merged["device"]))))
		if merged.get("resolution"):
			parts.append(html.escape(str(merged["resolution"]).replace("x", "×")))
		if merged.get("timeout"):
			parts.append(_("timeout") + " " + self._b(merged["timeout"]))
		try:
			parts.append(_("load") + " " + self._b("%.1f" % os.getloadavg()[0]))
		except OSError:
			pass
		return self._line(*parts)

	def _result_hints(self, sound_name):
		hints = {"value": 100}
		if self.sound:
			hints["sound-name"] = sound_name
		else:
			hints["suppress-sound"] = True
		return hints

	def _score(self, certainty, threshold):
		"""'3.41/3.50' -- certainty vs the configured threshold (lower is closer)."""
		if certainty is None or certainty >= 10:
			return ""
		return self._b("%.2f" % certainty) + "/" + self._b("%.2f" % threshold)

	def _frames(self, frames, dark_frames):
		text = _("%s frames") % self._b(frames)
		if dark_frames:
			text += " (" + _("%s dark") % self._b(dark_frames) + ")"
		return text

	def _elapsed(self):
		return self._b("%.1f s" % (time.time() - self.started_at))

	# -- cards -----------------------------------------------------------------

	def start(self, device=None, max_seconds=30.0, **extra):
		"""Create the in-progress card (asynchronously; never blocks the scan).

		*max_seconds* is the longest a scan can take (recognition + acquisition
		timeouts); the watchdog closes a card that never got a result by then.
		"""
		if self.disabled_reason:
			return
		self.context = dict(extra, device=device)
		body = self._line(
			"👀 " + html.escape(_("Looking for your face…")),
			self._debug_part({}),
		)

		def send():
			# CRITICAL on purpose: GNOME Shell keeps a critical banner on screen
			# (no 4 s auto-hide) and shows it expanded, so "Looking for your
			# face…" stays visible for the whole scan. The result update lowers
			# the urgency to NORMAL/LOW, which re-arms the normal auto-hide.
			# LOW would show no banner at all.
			if self._notify(self.title(_("Face authentication")), body, URGENCY_CRITICAL,
							EXPIRE_PROGRESS, {"value": 0}, final=False):
				# Watchdog: a critical banner must not outlive a helper that was
				# SIGKILLed mid-scan. A real result kills the watchdog first.
				with self._lock:
					if not self._finalized:
						self._watchdog = self._close_later(max_seconds + 5.0)
					else:
						self._cancel_watchdog()

		threading.Thread(target=send, daemon=True).start()

	def success(self, certainty, threshold, label, frames, elapsed, dark_frames=0, **extra):
		# Release text is deliberately minimal; the numbers appear with
		# [notifications] details = true.
		body = self._line(
			"✓ " + self._b(self.display_name()),
			self._elapsed(),
			self._b("“%s”" % label) if self.details else "",
			self._score(certainty, threshold) if self.details else "",
			self._frames(frames, dark_frames) if self.details else "",
			self._debug_part(dict(extra, threshold="%.2f" % threshold)),
		)
		if self._notify(
			self.title(_("Face recognized")), body, URGENCY_NORMAL, EXPIRE_SUCCESS,
			self._result_hints("service-login"), icon=APP_ICON,
		):
			# The user is in; the card has done its job. Keep it briefly so the
			# result is readable, then take it down (banner and list).
			self._close_later(self.success_linger)

	def timeout(self, best_certainty, threshold, frames, elapsed, timeout_s, dark_frames=0, **extra):
		# A near-miss is almost always lighting: point at the fix instead of
		# leaving the user to guess why "it worked this morning".
		if best_certainty is not None and best_certainty < threshold * 1.5:
			tip = "💡 " + html.escape(_("add a model in this light"))
		elif frames == 0:
			tip = "💡 " + html.escape(_("check the camera"))
		else:
			tip = ""
		score = self._score(best_certainty, threshold)
		body = self._line(
			"✗ " + _("No match in %s") % self._b("%d s" % timeout_s),
			tip,
			html.escape(_("enroll: sudo ubuntu-hello add")) if self.details else "",
			(_("closest") + " " + score) if (score and self.details) else "",
			self._frames(frames, dark_frames) if self.details else "",
			self._debug_part(dict(extra, threshold="%.2f" % threshold, timeout="%ds" % timeout_s)),
		)
		self._notify(
			self.title(_("Face not recognized")), body, URGENCY_NORMAL, EXPIRE_FAILURE,
			self._result_hints("dialog-warning"), icon=APP_ICON,
		)

	def too_dark(self, average_darkness, dark_threshold, frames, **extra):
		body = self._line(
			"🌙 " + html.escape(_("Too dark")),
			"💡 " + html.escape(_("turn on a light")),
			(_("darkness") + " " + self._b("%.0f" % average_darkness) + "/" + self._b("%.0f" % dark_threshold)) if self.details else "",
			self._frames(frames, 0) if self.details else "",
			self._debug_part(extra),
		)
		self._notify(
			self.title(_("Face not recognized")), body, URGENCY_NORMAL, EXPIRE_FAILURE,
			{"value": 100}, icon=APP_ICON,
		)

	def rejected(self, **extra):
		"""Face matched but a liveness check (rubberstamp) failed: PAM rejects."""
		body = self._line("✗ " + html.escape(_("Liveness check failed")), self._debug_part(extra))
		self._notify(
			self.title(_("Face not recognized")), body, URGENCY_NORMAL, EXPIRE_FAILURE,
			self._result_hints("dialog-warning"), icon=APP_ICON,
		)

	def no_model(self, **extra):
		body = self._line(
			html.escape(_("No face model for %s") % self.display_name()),
			"💡 " + html.escape(_("enroll: sudo ubuntu-hello add")),
			self._debug_part(extra),
		)
		self._notify(
			self.title(_("Face authentication unavailable")), body, URGENCY_LOW, EXPIRE_FAILURE,
			{"value": 100}, icon=APP_ICON,
		)

	def cancelled(self, reason=None, **extra):
		body = self._line("⏹ " + html.escape(reason or _("Cancelled")), self._debug_part(extra))
		self._notify(
			self.title(_("Face authentication cancelled")), body, URGENCY_LOW, EXPIRE_SUCCESS,
			{"value": 100}, icon=APP_ICON,
		)
