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
# too instead of stacking a new one per auth. Privileged (PAM/root) compare
# writes under /run/ubuntu-hello/notify/<uid>/ (root-owned 0700, O_NOFOLLOW)
# so a user cannot plant a symlink for root to follow. Unprivileged dry runs
# keep the XDG runtime-dir files.
#
# Body layout: ONE line. GNOME Shell replaces newlines with spaces and shows
# the collapsed banner as a single ellipsized line, so the essentials come
# first and diagnostics last; only <b>/<i>/<u> markup is rendered.

import html
import os
import pwd
import stat
import subprocess
import sys
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

# Unprivileged dry-run file holding the last notification id (XDG runtime
# dir, gone on logout). Privileged PAM compare uses RUN_DIR instead.
ID_FILE = "ubuntu-hello-notify.id"
# Root-owned runtime tree shared with face-skip / postinstall (0700).
RUN_DIR = "/run/ubuntu-hello"
NOTIFY_SUBDIR = "notify"

# Detached root watchdog: sleep, lstat the done marker without following a
# symlink, then drop to the user before CloseNotification. Used when PAM
# compare cannot let the user helper [ -e ] a 0700 /run/ubuntu-hello path.
_CLOSE_WATCHDOG = """
import os
import stat
import sys
import time
delay = float(sys.argv[1])
marker = sys.argv[2]
uid = int(sys.argv[3])
gid = int(sys.argv[4])
bus_path = sys.argv[5]
nid = sys.argv[6]
gdbus = sys.argv[7]
time.sleep(delay)
try:
	if stat.S_ISREG(os.lstat(marker).st_mode):
		raise SystemExit(0)
except OSError:
	pass
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + bus_path
os.environ["XDG_RUNTIME_DIR"] = os.path.dirname(bus_path)
os.environ["PATH"] = "/usr/bin:/bin"
if os.geteuid() == 0:
	os.setgroups([])
	os.setgid(gid)
	os.setuid(uid)
os.execv(gdbus, [gdbus, "call", "--session",
	"--dest", "org.freedesktop.Notifications",
	"--object-path", "/org/freedesktop/Notifications",
	"--method", "org.freedesktop.Notifications.CloseNotification", nid])
"""

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


def _privileged():
	"""True when compare is running as root (PAM), not a user dry run."""
	return os.geteuid() == 0


def _nofollow_flag():
	flag = getattr(os, "O_NOFOLLOW", 0)
	if not flag:
		raise OSError("O_NOFOLLOW is required")
	return flag


def _ensure_euid_dir(path):
	"""Create *path* as a 0700 euid-owned directory; refuse symlinks."""
	try:
		os.mkdir(path, 0o700)
	except FileExistsError:
		pass
	st = os.lstat(path)
	if stat.S_ISLNK(st.st_mode):
		raise OSError("%s: refuses symlink" % path)
	if not stat.S_ISDIR(st.st_mode):
		raise OSError("%s: not a directory" % path)
	if st.st_uid != os.geteuid():
		raise OSError("%s: unexpected owner uid %d" % (path, st.st_uid))
	os.chmod(path, 0o700)


def _open_nofollow(path, flags, mode=0o600):
	"""Open *path* without following a final-component symlink.

	Refuses non-regular files (O_NOFOLLOW + fstat). Parent directories are
	not created here.
	"""
	cloexec = getattr(os, "O_CLOEXEC", 0)
	fd = os.open(path, flags | _nofollow_flag() | cloexec, mode)
	try:
		st = os.fstat(fd)
		if not stat.S_ISREG(st.st_mode):
			raise OSError("%s: not a regular file" % path)
	except Exception:
		os.close(fd)
		raise
	return fd


def notify_state_paths(uid):
	"""Return (id_path, done_path) for *uid*.

	Root PAM compare uses /run/ubuntu-hello/notify/<uid>/ so marker writes
	cannot follow a user-controlled symlink under /run/user/<uid>/. User
	dry runs keep ID_FILE beside the session bus.
	"""
	uid = int(uid)
	if _privileged():
		base = os.path.join(RUN_DIR, NOTIFY_SUBDIR, str(uid))
		_ensure_euid_dir(RUN_DIR)
		_ensure_euid_dir(os.path.join(RUN_DIR, NOTIFY_SUBDIR))
		_ensure_euid_dir(base)
		return os.path.join(base, "id"), os.path.join(base, "done")
	runtime = "/run/user/%d" % uid
	id_path = os.path.join(runtime, ID_FILE)
	return id_path, id_path + ".done"


def read_saved_id(path):
	"""Return the persisted notification id at *path*, or 0."""
	fd = -1
	try:
		fd = _open_nofollow(path, os.O_RDONLY)
		data = os.read(fd, 64)
		return int(data.decode("ascii").strip() or 0)
	except (OSError, ValueError, UnicodeDecodeError):
		return 0
	finally:
		if fd >= 0:
			try:
				os.close(fd)
			except OSError:
				pass


def write_saved_id(path, notification_id, owner=None):
	"""Persist *notification_id* at *path* (best-effort, atomic).

	Opens the pid-suffixed tmp with O_NOFOLLOW|O_EXCL so a planted symlink
	is not followed. *owner* is (uid, gid) for fchown on the tmp fd when
	running as root; privileged PAM paths stay root-owned (no chown).
	"""
	tmp = "%s.%d.tmp" % (path, os.getpid())
	payload = ("%d\n" % notification_id).encode("ascii")
	fd = -1
	try:
		for attempt in (0, 1):
			try:
				fd = _open_nofollow(
					tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
				)
				break
			except FileExistsError:
				if attempt:
					raise
				os.unlink(tmp)
		os.write(fd, payload)
		os.fchmod(fd, 0o600)
		if owner is not None and os.geteuid() == 0:
			os.fchown(fd, owner[0], owner[1])
		os.close(fd)
		fd = -1
		os.replace(tmp, path)
	except OSError:
		if fd >= 0:
			try:
				os.close(fd)
			except OSError:
				pass
		try:
			os.unlink(tmp)
		except OSError:
			pass


class AuthNotifier:
	"""Create one desktop notification for a face-auth attempt and update it in place."""

	def __init__(self, user, service="", enabled=True, details=False, success_linger=3.0, call_timeout=2.0):
		self.user = user
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
		self.id_path = None
		self.done_path = None
		try:
			self.id_path, self.done_path = notify_state_paths(self._bus[0])
			self.notification_id = read_saved_id(self.id_path)
		except OSError:
			# Cannot create the privileged state dir: still send cards, skip
			# persistence rather than falling back to a user-writable path.
			self.notification_id = 0

	# -- transport -----------------------------------------------------------

	def _run_as_user(self, argv):
		"""Run *argv* on the user's session bus, dropping root to that user."""
		uid, gid, bus_path = self._bus
		env = {
			"DBUS_SESSION_BUS_ADDRESS": "unix:path=" + bus_path,
			"XDG_RUNTIME_DIR": os.path.dirname(bus_path),
			"PATH": "/usr/bin:/bin",
		}

		def demote():
			# Only root can (and needs to) switch identity; a dry run as the
			# user themselves already owns the bus.
			if os.geteuid() == 0:
				os.setgroups([])
				os.setgid(gid)
				os.setuid(uid)

		return subprocess.run(
			argv,
			env=env,
			preexec_fn=demote,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			timeout=self.call_timeout,
		)

	def _close_later(self, delay, unless_marker=None):
		"""Remove our card *delay* seconds from now, from a detached helper.

		compare.py exits (and PAM returns) right after the result, so the
		sleep + CloseNotification run in a separate session as the user: the
		PAM module's process-group SIGTERM on cancel/exit cannot reach it,
		and it needs no privileges the user does not have.

		With *unless_marker* the close is skipped when that file exists: the
		start() watchdog uses it so a critical "looking for your face" banner
		can never outlive a helper that was SIGKILLed before updating it,
		while a real result card (which touches the marker) is left alone.
		"""
		if self.disabled_reason or not self.notification_id or delay <= 0:
			return False
		uid, gid, bus_path = self._bus
		close = "%s call --session --dest org.freedesktop.Notifications " \
			"--object-path /org/freedesktop/Notifications " \
			"--method org.freedesktop.Notifications.CloseNotification %d" % (GDBUS_PATH, self.notification_id)
		env = {
			"DBUS_SESSION_BUS_ADDRESS": "unix:path=" + bus_path,
			"XDG_RUNTIME_DIR": os.path.dirname(bus_path),
			"PATH": "/usr/bin:/bin",
		}

		def demote():
			if os.geteuid() == 0:
				os.setgroups([])
				os.setgid(gid)
				os.setuid(uid)

		# Privileged compare stores the marker under 0700 /run/ubuntu-hello,
		# which the user helper cannot [ -e ]. Stay root for sleep+lstat, then
		# the watchdog drops uid before gdbus. Unprivileged dry runs keep the
		# existing user-shell helper.
		if unless_marker and _privileged():
			argv = [
				sys.executable, "-E", "-s", "-c", _CLOSE_WATCHDOG,
				"%.1f" % delay, unless_marker, str(uid), str(gid),
				bus_path, str(self.notification_id), GDBUS_PATH,
			]
			preexec = None
		else:
			if unless_marker:
				script = "sleep %s; [ -e '%s' ] || exec %s" % ("%.1f" % delay, unless_marker, close)
			else:
				script = "sleep %s; exec %s" % ("%.1f" % delay, close)
			argv = [SH_PATH, "-c", script]
			preexec = demote

		try:
			subprocess.Popen(
				argv,
				env=env,
				preexec_fn=preexec,
				start_new_session=True,
				stdin=subprocess.DEVNULL,
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				close_fds=True,
			)
		except OSError:
			return False
		return True

	def _mark_done(self):
		"""Record that a result card went out (guards the start() watchdog)."""
		if not self.done_path:
			return
		fd = -1
		try:
			fd = _open_nofollow(
				self.done_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
			)
			os.write(fd, b"1\n")
			os.fchmod(fd, 0o600)
			os.close(fd)
			fd = -1
		except OSError:
			if fd >= 0:
				try:
					os.close(fd)
				except OSError:
					pass

	def _notify(self, summary, body, urgency, expire_ms, hints=None, icon=APP_ICON, final=True):
		"""Send org.freedesktop.Notifications.Notify, reusing our id to update in place.

		*final* cards touch the done marker before being sent (see
		_close_later); the in-progress card passes final=False.
		"""
		if self.disabled_reason:
			return False
		if final:
			self._mark_done()
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
				if self.id_path:
					# Privileged PAM files stay root-owned under RUN_DIR.
					owner = None if _privileged() else self._bus[:2]
					write_saved_id(self.id_path, new_id, owner=owner)
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
		if self.done_path:
			try:
				os.unlink(self.done_path)
			except OSError:
				pass
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
				self._close_later(max_seconds + 5.0, unless_marker=self.done_path)

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
			{"value": 100, "sound-name": "service-login"}, icon=APP_ICON,
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
			{"value": 100, "sound-name": "dialog-warning"}, icon=APP_ICON,
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
