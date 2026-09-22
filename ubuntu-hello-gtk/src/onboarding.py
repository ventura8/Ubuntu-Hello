import sys
import os
# Set secure umask for all created files/directories (0o077 ensures only owner has access)
os.umask(0o077)

import re
import time
import subprocess
import threading
import paths_factory
import auth_helper
import config_edit


from i18n import _
from wallet_backend import wallet_backend_label, wallet_unlock_phrase

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk as gtk
import gtk4compat
import camera_names
import enroll
from gi.repository import Gdk as gdk
from gi.repository import GObject as gobject
from gi.repository import Pango as pango
from gi.repository import GLib
from gi.repository import GdkPixbuf as pixbuf

# How long scan_cameras_thread()'s watchdog waits for a single device's
# cv2.VideoCapture().read() before giving up on it. A module-level constant
# (rather than a literal in the loop) so tests can patch it to a small value
# and exercise the real timeout/abandonment path instead of faking it out.
CAMERA_PROBE_TIMEOUT = 5

# Last wizard page. `certainty` is a Euclidean distance on dlib's 128-d face
# descriptor (x10); LOWER is stricter -- dlib's own "same person" reference is 6.0,
# so every level here is well inside it. Secure is the default.
# These numbers are NOT fitted to one measurement. With a single enrolled model the
# distance to the same face moves by about 0.4 between lighting conditions on real
# IR hardware, so a threshold placed just under one session's numbers rejects that
# same person in the next one; each level keeps margin for that drift and takes its
# strictness from SECURITY_CONFIRMATIONS instead.
# The nod liveness check is a switch on the same page, off by default because it
# adds a step to every login. The wizard shows the current value, so a re-run does
# not turn a choice made in Settings back off.
SECURITY_PRESETS = {
	"radiofast": 3.5,
	"radiobalanced": 3.0,
	"radiosecure": 2.6,
}
# Separate frames that must agree before a match counts, one rung per level. The
# levels tighten here rather than through a lower certainty, because a lower
# threshold buys strictness by making the app stop recognising its own user in
# changed light, whereas another agreeing frame costs a fraction of a second.
# Mirrored in tab_security.
SECURITY_CONFIRMATIONS = {
	"radiofast": 2,
	"radiobalanced": 3,
	"radiosecure": 4,
}
DEFAULT_PRESET = "radiosecure"


from tab_keyring import KeyringPasswordDialog
from tab_security import LIVENESS_RULE


_USERNAME_RE = r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$"


def _user_from_pkexec():
	pkexec_uid = os.environ.get("PKEXEC_UID")
	if not pkexec_uid:
		return None
	try:
		import pwd
		return pwd.getpwuid(int(pkexec_uid)).pw_name
	except Exception:
		return None


def _user_from_login():
	try:
		return os.getlogin()
	except Exception:
		return None


def _user_from_loginctl():
	"""First non-root session owner reported by loginctl."""
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


def _capture_device_entries():
	"""One entry per real capture node (aliases and metadata nodes collapsed)."""
	try:
		os.listdir("/dev/v4l/by-path")
		return camera_names.list_capture_devices()
	except Exception:
		return []


def _is_infrared(np, frame):
	"""A grayscale / IR frame has identical R, G and B channels."""
	try:
		return bool(np.all(frame[:, :, 0] == frame[:, :, 1])
					and np.all(frame[:, :, 1] == frame[:, :, 2]))
	except (IndexError, ValueError):
		# Not a three-channel frame, so it cannot be probed this way.
		return False


def _probe_camera(cv2, real_path):
	"""Open *real_path* and grab one frame, giving up after CAMERA_PROBE_TIMEOUT.

	Returns ``(capture, frame)``, or ``(None, None)`` when the device could not
	be read in time. The caller owns the returned capture and must release it.

	cv2.VideoCapture().read() has no timeout of its own and cannot be cancelled
	once called; a misbehaving driver can block it forever, which would
	otherwise freeze the whole scan on this one device. Run it on a watchdog
	thread instead: if it doesn't finish in time, treat the device as unopenable
	and move on.

	`state`/`lock` are created fresh per call and passed in as default arguments
	(not captured by reference) so a worker that is still running when the scan
	moves on to the next device can never observe -- or write into -- a later
	call's state; Python closures capture variables like `real_path` by
	reference, so without this binding a late-finishing worker would see
	whatever the *current* call happened to be.

	`lock` makes the timeout/completion handoff atomic: whichever side reaches
	`state["status"] == "pending"` first -- the worker finishing its read, or
	the caller's post-join check -- is the one responsible for the capture
	(consume it or release it); the other side is guaranteed to see it already
	changed and do nothing, so a capture is claimed exactly once and never
	silently discarded unreleased.
	"""
	state = {"status": "pending"}
	lock = threading.Lock()

	def _open_and_read(real_path=real_path, state=state, lock=lock):
		cap = None
		try:
			cap = cv2.VideoCapture(real_path)
			ok, img = cap.read()
		except Exception:
			ok, img = False, None
		with lock:
			if state["status"] != "pending":
				# The caller already gave up on this device; nobody else will
				# release this capture.
				_release_quietly(cap)
				return
			state["status"] = "done"
			if ok:
				state["capture"] = cap
				state["frame"] = img
			else:
				_release_quietly(cap)

	opener = threading.Thread(target=_open_and_read, daemon=True)
	opener.start()
	opener.join(timeout=CAMERA_PROBE_TIMEOUT)

	with lock:
		if state["status"] == "pending":
			state["status"] = "abandoned"
			return None, None

	if "capture" not in state:
		return None, None
	return state["capture"], state["frame"]


def _release_quietly(cap):
	if cap is None:
		return
	try:
		cap.release()
	except Exception:
		pass


class OnboardingWindow(gtk.Window):
	def __init__(self, run_main_loop=True):
		"""Initialize the sticky window"""
		# Load the custom CSS theme stylesheet
		paths_factory.load_custom_css()
		try:
			import preferences
			import i18n as _i18n
			code = preferences.read_language()
			if not code or code == preferences.AUTO:
				getter = getattr(_i18n, "effective_language", None)
				code = getter() if callable(getter) else "en"
			gtk4compat.apply_text_direction(code)
		except Exception:
			pass

		# Make the class a GTK window
		gtk.Window.__init__(self)

		self.completed = False
		self.run_main_loop = run_main_loop

		self.builder = gtk4compat.builder(self, paths_factory.onboarding_wireframe_path(), "ubuntu-hello-gtk")

		self.window = self.builder.get_object("onboardingwindow")
		self.slidecontainer = self.builder.get_object("slidecontainer")
		paths_factory.set_picture_file(self.builder.get_object("image2"), paths_factory.about_logo_path())
		self.nextbutton = self.builder.get_object("nextbutton")
		self.version_label = self.builder.get_object("version_label")
		if self.version_label:
			self.version_label.set_text(self.get_display_version())

		self.window.set_icon_name("ubuntu-hello-gtk")
		self.window.connect("close-request", self.exit)

		self.slides = [
			self.builder.get_object("slide0"),
			self.builder.get_object("slide1"),
			self.builder.get_object("slide2"),
			self.builder.get_object("slide3"),
			self.builder.get_object("slide4"),
			self.builder.get_object("slide5"),
			self.builder.get_object("slide6"),
			self.builder.get_object("slide7")
		]

		self.preview_image = self.builder.get_object("preview_image")
		# Fixed preview height on the camera page so the device list below keeps its space.
		self.preview_image.set_size_request(400, 300)
		self.slide4_preview_image = self.builder.get_object("slide4_preview_image")
		self.slide4_preview_image.set_size_request(600, 350)
		self.slide4_instruction_label = self.builder.get_object("slide4_instruction_label")
		self.preview_capture = None
		self.current_preview_path = None
		self.preview_thread = None
		# Slide 4 enrolls two models: pass 1 in the current light (required),
		# pass 2 after the user changes the lighting (may be skipped) -- see
		# prepare_second_scan for why. The why/how is explained up front on
		# the slide so the second pass only needs a short instruction.
		self.scan_pass = 1
		self.models_enrolled = 0
		self.slide4_device_path = None
		# Bumped on every entry to / exit from the camera page: a scan thread
		# from an earlier visit stops at its next device and never posts a list.
		self.scan_generation = 0

		# GTK 4 / Wayland: the compositor places the window (no move/centre API).
		self.window.set_default_size(800, 680)
		# Only the first slide is visible until Next is pressed.
		for index, slide in enumerate(self.slides):
			if slide is not None:
				slide.set_visible(index == 0)
		self.window.present()

		# Hide the finish button initially
		self.builder.get_object("finishbutton").set_visible(False)

		self.window.current_slide = 0
		# First page shows Cancel only (Back appears from the second page on).
		self.update_navigation_buttons()

		# Start GTK main loop if requested
		if run_main_loop:
			gtk4compat.run_main()

	def go_next_slide(self, button=None):
		if self.window.current_slide == 6 and not self.validate_and_save_keyring():
			self.enable_next()
			return

		self.nextbutton.set_sensitive(False)

		# Stop camera preview if moving away from slide 2 or 4
		if self.window.current_slide in (2, 4):
			self.stop_preview()
		if self.window.current_slide == 2:
			self.cancel_camera_scan()

		self.slides[self.window.current_slide].set_visible(False)
		self.slides[self.window.current_slide + 1].set_visible(True)
		self.window.current_slide += 1
		# the shown child may have zero/wrong dimensions
		self.slidecontainer.queue_resize()
		self.update_navigation_buttons()

		if self.window.current_slide == 1:
			self.execute_slide1()
		elif self.window.current_slide == 2:
			gobject.timeout_add(10, self.execute_slide2)
		elif self.window.current_slide == 3:
			self.execute_slide3()
		elif self.window.current_slide == 4:
			self.execute_slide4()
		elif self.window.current_slide == 5:
			self.execute_slide5()
		elif self.window.current_slide == 6:
			self.execute_slide6()
		elif self.window.current_slide == 7:
			self.execute_slide7()

	def update_navigation_buttons(self):
		"""Cancel only on the first page; every later page offers Back."""
		on_first = self.window.current_slide == 0
		cancel = self.builder.get_object("cancelbutton")
		back = self.builder.get_object("backbutton")
		if cancel:
			cancel.set_visible(on_first)
		if back:
			back.set_visible(not on_first)

	def reset_slide4(self):
		"""Put the face-scan slide's widgets back into their first-pass state."""
		self.scan_pass = 1
		heading = self.builder.get_object("label4")
		if heading:
			heading.set_text(_("Adding your face models"))
		description = self.builder.get_object("label5")
		if description:
			description.set_text(_("Ubuntu Hello records your face from a few angles in about a minute: keep your face inside the preview and follow the prompts (look straight, turn slightly left and right, chin up and down). Afterwards a second, optional scan — with glasses, different facial hair or other lighting — makes recognition even more reliable at any time of day."))
		instruction = self.builder.get_object("slide4_instruction_label")
		if instruction:
			instruction.set_text(_("Please look directly into the camera"))
		scanbutton = self.builder.get_object("scanbutton")
		if scanbutton:
			scanbutton.set_label(_("Start face scan"))
			scanbutton.set_sensitive(True)
		skipbutton = self.builder.get_object("skipsecondbutton")
		if skipbutton:
			skipbutton.set_visible(False)

	def _leave_slide(self, current):
		"""Undo whatever the page being left had started."""
		if current in (2, 4):
			self.stop_preview(clear_image=True)
		if current == 2:
			self.cancel_camera_scan()
		if current == 3 and getattr(self, "capture", None) is not None:
			try:
				self.capture.release()
			except Exception:
				pass
			self.capture = None
		if current == 7:
			# The last page swapped Next for Finish; undo that.
			finish = self.builder.get_object("finishbutton")
			if finish:
				finish.set_visible(False)
			self.nextbutton.set_visible(True)

	def _reenter_slide(self, target):
		"""Re-run the entry hook of the page being returned to."""
		if target == 1:
			self.execute_slide1()
		elif target == 2:
			gobject.timeout_add(10, self.execute_slide2)
		elif target == 4:
			self.execute_slide4()
		elif target == 5:
			self.execute_slide5()
		elif target == 6:
			self.execute_slide6()

	def go_prev_slide(self, button=None):
		"""Go back one page, undoing what the current page started.

		Models already saved on the face-scan page stay saved: coming back to
		that page resumes at the second scan instead of recording a third.
		"""
		current = self.window.current_slide
		if current <= 0:
			return

		self._leave_slide(current)

		target = current - 1
		# The IR-emitter question is skipped automatically for non-IR cameras:
		# never land the user on a page that would bounce them forward again.
		if target == 3 and not getattr(self, "slide3_shown", False):
			target = 2

		self.slides[current].set_visible(False)
		self.slides[target].set_visible(True)
		self.window.current_slide = target
		self.slidecontainer.queue_resize()
		self.update_navigation_buttons()
		self.enable_next()

		self._reenter_slide(target)

	def execute_slide1(self):
		self.downloadoutputlabel = self.builder.get_object("downloadoutputlabel")

		# NOTE: presence of the landmark file is the cheapest signal that the
		# download step already ran; there is no manifest to consult.
		if os.path.exists(paths_factory.dlib_data_dir_path() / "shape_predictor_5_face_landmarks.dat"):
			self.downloadoutputlabel.set_text(_("Datafiles have already been downloaded!\nClick Next to continue"))
			self.enable_next()
			return

		# Came back while a download is still running: just watch it again.
		proc = getattr(self, "proc", None)
		if proc is not None and getattr(self, "download_queue", None) is not None and proc.poll() is None:
			self.nextbutton.set_sensitive(False)
			gobject.timeout_add(50, self.update_download_gui)
			return

		self.proc = subprocess.Popen(["./install.sh"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=paths_factory.dlib_data_dir_path())

		self.download_lines = []
		import queue
		self.download_queue = queue.Queue()
		threading.Thread(target=self.read_download_thread, daemon=True).start()
		gobject.timeout_add(50, self.update_download_gui)

	def read_download_thread(self):
		for line in iter(self.proc.stdout.readline, b''):
			self.download_queue.put(line.decode("utf-8", errors="replace"))
		self.download_queue.put(None)

	def update_download_gui(self):
		import queue
		updated = False
		while True:
			try:
				line = self.download_queue.get_nowait()
				if line is None:
					# Process finished
					try:
						status = self.proc.wait(5)
					except Exception:
						status = -1
					if status != 0:
						self.show_error(_("Error while downloading datafiles"), " ".join(self.download_lines))
						return False

					self.downloadoutputlabel.set_text(_("Done!\nClick Next to continue"))
					self.enable_next()
					return False

				self.download_lines.append(line)
				updated = True
			except queue.Empty:
				break

		if updated:
			if len(self.download_lines) > 10:
				self.download_lines = self.download_lines[-10:]
			self.downloadoutputlabel.set_text("".join(self.download_lines))

		return True

	def execute_slide2(self):
		self.loadinglabel = self.builder.get_object("loadinglabel")
		self.devicelistbox = self.builder.get_object("devicelistbox")

		# Re-entered via Back: drop the previous device list before rescanning,
		# otherwise every visit stacks another list under the preview. Next
		# stays disabled until the new scan has posted its list.
		self.clear_camera_list()
		self.loadinglabel.set_visible(True)
		self.nextbutton.set_sensitive(False)

		self.scan_generation += 1
		threading.Thread(target=self.scan_cameras_thread, args=(self.scan_generation,), daemon=True).start()

	def clear_camera_list(self):
		"""Remove the device list (if any) from the camera page."""
		previous = getattr(self, "scrolled_window", None)
		if previous is not None and getattr(self, "devicelistbox", None) is not None:
			try:
				self.devicelistbox.remove(previous)
			except Exception:
				pass
		self.scrolled_window = None
		self.cameras = None

	def cancel_camera_scan(self):
		"""Stop a running camera scan (user left the page with Back or Next)."""
		self.scan_generation += 1

	def scan_cameras_thread(self, generation=None):
		import numpy as np
		if generation is None:
			generation = self.scan_generation
		try:
			import cv2
		except Exception:
			GLib.idle_add(self.show_error, _("Error while importing OpenCV2"), _("Try reinstalling cv2"))
			return

		entries = _capture_device_entries()
		if not entries:
			GLib.idle_add(self.show_error, _("No webcams found on system"), _("Please configure your camera yourself if you are sure a compatible camera is connected"))
			return

		device_rows = []
		for device_path, device_name in entries:
			if generation != self.scan_generation:
				return  # the user left the page: stop probing, post nothing
			time.sleep(.5)

			capture, frame = _probe_camera(cv2, os.path.realpath(device_path))
			if capture is None:
				device_rows.append([device_name, device_path, -9, _("No, camera can't be opened")])
				continue

			if _is_infrared(np, frame):
				device_rows.append([device_name, device_path, 5, _("Yes, compatible infrared camera")])
			else:
				device_rows.append([device_name, device_path, -5, _("No, not an infrared camera")])
			capture.release()

		device_rows = sorted(device_rows, key=lambda k: -k[2])

		if generation != self.scan_generation:
			return
		GLib.idle_add(self.update_camera_list_gui, device_rows, generation)

	def update_camera_list_gui(self, device_rows, generation=None):
		# A scan that finished after the user left (or re-entered) the page
		# must not add a second list.
		if generation is not None and generation != self.scan_generation:
			return False
		self.clear_camera_list()
		# Columns: name, recommendation; hidden values: device path, is_gray.
		# (Cells ellipsize: device names / by-path strings can be very long.)
		self.cameras = gtk4compat.ColumnList([_("Camera identifier or path"), _("Recommended")])
		for device in device_rows:
			is_gray = device[2] == 5
			self.cameras.append([device[0], device[3], device[1], is_gray])

		self.scrolled_window = gtk.ScrolledWindow()
		self.scrolled_window.set_policy(gtk.PolicyType.NEVER, gtk.PolicyType.AUTOMATIC)
		self.scrolled_window.set_has_frame(True)
		self.scrolled_window.set_vexpand(True)
		self.scrolled_window.set_hexpand(True)
		# Without an explicit minimum, a freshly-created ScrolledWindow with no
		# natural size request of its own can be allocated ~0 height by its
		# parent box, silently collapsing the whole device list out of view
		# (GTK logs "Negative content width/height" warnings when this
		# happens) even though the widget tree and Next-button enabling are
		# otherwise correct. v1.1.2 set this; a later rewrite dropped it.
		self.scrolled_window.set_min_content_height(160)
		self.scrolled_window.set_margin_bottom(15)
		self.loadinglabel.set_visible(False)
		self.scrolled_window.set_child(self.cameras.widget)
		self.devicelistbox.append(self.scrolled_window)
		self.enable_next()

		self.cameras.connect_selection_changed(self.on_camera_selection_changed)
		# Keep the camera the user already chose (this run, or the one saved
		# in the config) selected; fall back to the best-ranked device.
		default_index = 0
		remembered = self.remembered_device_path()
		if remembered:
			for index, row in enumerate(device_rows):
				if row[1] == remembered:
					default_index = index
					break
		if len(device_rows) > 0:
			self.cameras.select(default_index)
			# Scroll once the list is laid out so the selected camera is visible.
			GLib.idle_add(self.scroll_camera_row_into_view, default_index)

		# Ensure preview is started for the selected device if selection did not trigger it
		if device_rows and not self.current_preview_path and not self.preview_thread:
			default_path = device_rows[default_index][1]
			self.preview_image = self.builder.get_object("preview_image")
			self.current_preview_path = default_path
			self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(default_path,), daemon=True)
			self.preview_thread.start()

	def scroll_camera_row_into_view(self, index):
		cameras = getattr(self, "cameras", None)
		if cameras is not None:
			cameras.scroll_to(index)
		return False

	def selected_camera(self):
		"""Row [name, recommendation, device_path, is_gray] selected on the camera page, or None."""
		cameras = getattr(self, "cameras", None)
		if cameras is None:
			return None
		return cameras.selected_row()

	def remembered_device_path(self):
		"""The camera to preselect: chosen earlier in this run, else the configured one."""
		chosen = getattr(self, "slide4_device_path", None)
		if chosen:
			return chosen
		try:
			import configparser
			parser = configparser.ConfigParser()
			parser.read(paths_factory.config_file_path())
			return parser.get("video", "device_path", fallback="") or ""
		except Exception:
			return ""

	def execute_slide3(self):
		try:
			import cv2
		except Exception:
			self.show_error(_("Error while importing OpenCV2"), _("Try reinstalling cv2"))

		row = self.selected_camera()
		if row is None:
			self.show_error(_("Error selecting camera"))
			return

		device_path = row[2]
		is_gray = row[3]

		self.slide3_shown = bool(is_gray)
		if is_gray:
			# test if linux-enable-ir-emitter help should be displayed, 
			# the user must click on the yes/no button which calls the method slide3_button_yes|no
			import os
			real_path = os.path.realpath(device_path)
			self.capture = cv2.VideoCapture(real_path)
			if not self.capture.isOpened():
				self.show_error(_("The selected camera cannot be opened"), _("Try to select another one"))
				return
			self.capture.read()
		else:  
			# skip, the selected camera is not infrared
			self.go_next_slide()

	def slide3_button_yes(self, button):
		if hasattr(self, "capture") and self.capture is not None:
			self.capture.release()
		self.go_next_slide()

	def slide3_button_no(self, button):
		if hasattr(self, "capture") and self.capture is not None:
			self.capture.release()
		self.builder.get_object("leiestatus").set_markup(_("Please visit\n<a href=\"https://github.com/EmixamPP/linux-enable-ir-emitter\">https://github.com/EmixamPP/linux-enable-ir-emitter</a>\nto enable your ir emitter"))
		self.builder.get_object("leieyesbutton").set_visible(False)
		self.builder.get_object("leienobutton").set_visible(False)
		hint = self.builder.get_object("leiehint")
		if hint is not None:
			hint.set_visible(False)

	def execute_slide4(self):
		row = self.selected_camera()
		if row is None:
			self.show_error(_("Error selecting camera"))
			return

		device_path = row[2]
		try:
			self.proc = subprocess.Popen(["ubuntu-hello", "set", "device_path", device_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		except Exception:
			self.proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

		self.window.set_focus(self.builder.get_object("scanbutton"))
		skipbutton = self.builder.get_object("skipsecondbutton")
		if skipbutton:
			skipbutton.set_visible(False)  # only the second scan can be skipped

		# Start preview on slide 4 preview image
		self.slide4_device_path = device_path
		self.preview_image = self.slide4_preview_image
		self.current_preview_path = None
		self.stop_preview()
		self.current_preview_path = device_path
		self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(device_path,), daemon=True)
		self.preview_thread.start()

		# Back to this page after the first scan: resume at the second one.
		if self.models_enrolled >= 1:
			self.scan_pass = 2
			self.prepare_second_scan()
		else:
			self.reset_slide4()

	def on_scanbutton_click(self, button):
		# Stop camera preview to avoid device-busy conflict during face scan, but keep the image visible
		self.stop_preview(clear_image=False)

		if self.scan_pass == 1:
			proc = getattr(self, "proc", None)
			if proc is not None:
				try:
					proc.wait(2)
				except Exception:
					pass

		# Change button label to instruction text and disable it
		scanbutton = button or self.builder.get_object("scanbutton")
		if scanbutton:
			scanbutton.set_label(_("Please look directly into the camera"))
			scanbutton.set_sensitive(False)
		skipbutton = self.builder.get_object("skipsecondbutton")
		if skipbutton:
			skipbutton.set_sensitive(False)

		# Wait a bit to allow the user to read the message
		gobject.timeout_add(600, self.run_add)

	# Model labels are passed explicitly so `ubuntu-hello list` / Settings show
	# which lighting each wizard model was recorded in (max 24 chars, no commas).
	def scan_model_label(self):
		if self.models_enrolled == 0:
			return _("Setup lighting 1")
		return _("Setup lighting 2")

	def run_add(self):
		"""Start `ubuntu-hello add` on a worker thread with live guidance (see enroll.py)."""
		self.on_scan_guide("center")
		enroll.run_add(
			["ubuntu-hello", "-y", "add", self.scan_model_label()],
			self.on_scan_guide, self.on_scan_progress, self.on_add_finished)
		return False

	def on_scan_guide(self, key):
		"""Show the pose `add` asks for ("Turn your head slightly to the left"…)."""
		instruction = self.builder.get_object("slide4_instruction_label")
		text = enroll.guide_text(key)
		if instruction and text:
			instruction.set_text(text)
			instruction.set_visible(True)
		return False

	def on_scan_progress(self, count, total):
		scanbutton = self.builder.get_object("scanbutton")
		if scanbutton:
			scanbutton.set_label(enroll.progress_text(count, total))
		return False

	def _handle_first_scan_failure(self, scanbutton, output):
		"""First (required) scan failed: restore the button; show_error exits."""
		if scanbutton:
			scanbutton.set_label(_("Start face scan"))
			scanbutton.set_sensitive(True)
		self.show_error(_("Can't save face model"), output)

	def _handle_second_scan_failure(self, scanbutton, skipbutton, output):
		"""The first model is already saved, so a failed second scan must not
		kill the wizard. Explain, and let the user retry or skip.
		"""
		if scanbutton:
			scanbutton.set_label(_("Scan second model"))
			scanbutton.set_sensitive(True)
		if skipbutton:
			skipbutton.set_sensitive(True)

		reason = output.strip().splitlines()[-1] if output.strip() else ""
		self.show_warning(
			_("Couldn't record the second model"),
			_("Your first model is saved, so face login already works. The room must still be bright enough to see your face — change the light rather than turning it off — then try again, or skip and add a model later from Settings.")
			+ ("\n\n" + reason if reason else ""),
		)
		self.restart_slide4_preview()

	def on_add_finished(self, status, output):
		print("ubuntu-hello add output:")
		print(output)

		scanbutton = self.builder.get_object("scanbutton")
		instruction = self.builder.get_object("slide4_instruction_label")
		if instruction:
			instruction.set_visible(False)

		if status != 0:
			if self.models_enrolled == 0:
				self._handle_first_scan_failure(scanbutton, output)
			else:
				self._handle_second_scan_failure(
					scanbutton, self.builder.get_object("skipsecondbutton"), output)
			return False

		self.models_enrolled += 1

		if self.scan_pass == 1:
			self.scan_pass = 2
			self.prepare_second_scan()
			return False

		gobject.timeout_add(10, self.go_next_slide)
		return False

	def prepare_second_scan(self):
		"""Turn slide 4 into the second pass.

		Why a second model: a face match is a distance to the stored model, and
		lighting moves that distance a lot. A single model recorded in daylight
		often lands just past the certainty threshold in the evening under lamps
		(or the other way round), which shows up as intermittent "timeout
		reached" failures rather than a clean error. A second model recorded in
		different light gives the matcher a close neighbour for both cases.
		The user was told this up front on the slide, so the copy here is short.
		"""
		heading = self.builder.get_object("label4")
		description = self.builder.get_object("label5")
		instruction = self.builder.get_object("slide4_instruction_label")
		scanbutton = self.builder.get_object("scanbutton")
		skipbutton = self.builder.get_object("skipsecondbutton")

		if heading:
			heading.set_text(_("Second scan: glasses, facial hair or different lighting"))
		if description:
			description.set_text(_("First model saved: face login already works. A second model makes recognition more reliable when something changes: put on the glasses you sometimes wear (or take them off), or record it with different facial hair, and change the light — turn a lamp on or off, move away from the window. Keep your face clearly visible and press Scan again; the same short guided capture follows."))
		if instruction:
			instruction.set_text(_("Please look directly into the camera"))
		if scanbutton:
			scanbutton.set_label(_("Scan second model"))
			scanbutton.set_sensitive(True)
		if skipbutton:
			skipbutton.set_sensitive(True)
			skipbutton.set_visible(True)
		self.restart_slide4_preview()
		if scanbutton:
			self.window.set_focus(scanbutton)

	def restart_slide4_preview(self):
		"""Re-open the live preview on slide 4 after `ubuntu-hello add` released the camera."""
		device_path = getattr(self, "slide4_device_path", None)
		if not device_path:
			return
		self.preview_image = self.slide4_preview_image
		self.stop_preview(clear_image=False)
		self.current_preview_path = device_path
		self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(device_path,), daemon=True)
		self.preview_thread.start()

	def on_skipsecondbutton_click(self, button=None):
		"""Skip the second scan (only offered once the first model is saved)."""
		if self.models_enrolled == 0:
			return
		self.stop_preview(clear_image=False)
		gobject.timeout_add(10, self.go_next_slide)

	def show_warning(self, text, secondary=""):
		"""Non-fatal dialog (show_error exits the wizard)."""
		gtk4compat.alert(self.window, text, secondary)

	def execute_slide5(self):
		self.enable_next()

	def execute_slide6(self):
		self.builder.get_object("keyring_password_box").set_visible(False)
		wallet = wallet_unlock_phrase()
		backend = wallet_backend_label()
		self.builder.get_object("keyring_desc_label").set_markup(_("Ubuntu Hello can automatically unlock your {} using face authentication (detected wallet: {}).\n\n<b>TPM Status:</b> Checking TPM hardware and tools...").format(wallet, backend))
		
		import threading
		threading.Thread(target=self.detect_tpm_thread, daemon=True).start()
		self.enable_next()

	def detect_tpm_thread(self):
		import os
		import shutil
		import subprocess
		from gi.repository import GLib

		tpm_dev_exists = os.path.exists("/dev/tpmrm0") or os.path.exists("/dev/tpm0")
		tpm_tools_exist = shutil.which("tpm2_createprimary") is not None and shutil.which("tpm2_unseal") is not None
		wallet = wallet_unlock_phrase()
		backend = wallet_backend_label()

		if tpm_dev_exists and not tpm_tools_exist:
			# Hardware exists, but tools missing. Let's try to install them automatically!
			GLib.idle_add(lambda: self.builder.get_object("keyring_desc_label").set_markup(
				_("Ubuntu Hello can automatically unlock your {} using face authentication (detected wallet: {}).\n\n<b>TPM Status:</b> TPM hardware detected. Installing <i>tpm2-tools</i> automatically...").format(wallet, backend))
			)
			try:
				subprocess.run(["apt-get", "install", "-y", "-qq", "tpm2-tools"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
				tpm_tools_exist = shutil.which("tpm2_createprimary") is not None and shutil.which("tpm2_unseal") is not None
			except Exception:
				pass

		# Update the status label on the main GTK thread
		def update_ui():
			desc_label = self.builder.get_object("keyring_desc_label")
			if tpm_dev_exists and tpm_tools_exist:
				desc_label.set_markup(_("Ubuntu Hello can automatically unlock your {} using face authentication (detected wallet: {}).\n\n<b>TPM Status:</b> Hardware TPM 2.0 active. Your password will be securely sealed inside the TPM.").format(wallet, backend))
			elif tpm_dev_exists:
				desc_label.set_markup(_("Ubuntu Hello can automatically unlock your {} using face authentication (detected wallet: {}).\n\n<b>TPM Status:</b> TPM hardware detected, but automatic installation of <i>tpm2-tools</i> failed. Run <code>sudo apt install tpm2-tools</code>. Falling back to software-based credential caching.").format(wallet, backend))
			else:
				desc_label.set_markup(_("Ubuntu Hello can automatically unlock your {} using face authentication (detected wallet: {}).\n\n<b>TPM Status:</b> No TPM hardware detected. Using software-based credential caching (AES-256-GCM).").format(wallet, backend))

		GLib.idle_add(update_ui)

	def on_keyring_checkbox_toggled(self, checkbutton):
		# Wired from onboarding.ui so Builder can resolve the handler, but the
		# checkbox is read on slide submit (validate_and_save_keyring), not on
		# toggle — there is deliberately nothing to do here.
		pass

	def get_real_user(self):
		"""The desktop user behind this root process, or "root" if none found."""
		import re

		# Order matters and differs from window.py: loginctl is consulted before
		# $USER here. Each source is only tried if the ones above came up empty
		# or returned root -- loginctl spawns a subprocess.
		for source in (
			lambda: os.environ.get("SUDO_USER"),
			_user_from_pkexec,
			_user_from_login,
			_user_from_loginctl,
			lambda: os.environ.get("USER"),
		):
			candidate = source()
			if candidate and candidate != "root":
				user = candidate
				break
		else:
			return "root"

		if re.match(_USERNAME_RE, user):
			return user
		return "root"

	def _disable_keyring_for_user(self):
		"""Skip/disable: drop the pending marker and any stored keys."""
		user = self.get_real_user()
		if not user or user == "root":
			return True

		paths = (
			os.path.join(paths_factory.keyring_pending_dir_path(), user),
			os.path.join(paths_factory.keyring_keys_dir_path(), user),
			os.path.join(paths_factory.tpm_keys_dir_path(), f"{user}.pub"),
			os.path.join(paths_factory.tpm_keys_dir_path(), f"{user}.priv"),
		)

		unlink_errors = []
		for path in paths:
			if not os.path.exists(path):
				continue
			try:
				os.unlink(path)
			except Exception as e:
				unlink_errors.append(f"{path}: {e}")

		if unlink_errors:
			self.show_keyring_error(_("Failed to disable keyring unlocking: {}").format("; ".join(unlink_errors)))
			return False
		return True

	def _ask_and_verify_password(self, user):
		"""Prompt for the login password and check it. None means "give up"."""
		dialog = KeyringPasswordDialog(self.window, user)
		response = gtk4compat.run_dialog(dialog)
		passwd1 = dialog.entry1.get_text()
		dialog.destroy()

		if response != gtk.ResponseType.OK:
			return None

		if not passwd1:
			self.show_keyring_error(_("Password cannot be empty"))
			return None

		if not auth_helper.verify_user_password(user, passwd1):
			self.show_keyring_error(_("Incorrect password for user {}").format(user))
			return None

		return passwd1

	def _enable_keyring_for_user(self, user, password):
		"""Hand the password to `ubuntu-hello keyring enable` for sealing."""
		try:
			res = subprocess.run(
				["ubuntu-hello", "keyring", "enable", "-U", user],
				input=password + "\n",
				capture_output=True,
				text=True,
				timeout=120,
			)
		except FileNotFoundError:
			self.show_keyring_error(_("Failed to enable keyring unlocking: ubuntu-hello executable not found"))
			return False
		except subprocess.TimeoutExpired:
			self.show_keyring_error(_("Failed to enable keyring unlocking: timed out waiting for keyring enable"))
			return False
		except Exception as e:
			self.show_keyring_error(_("Failed to enable keyring unlocking: {}").format(str(e)))
			return False

		if res.returncode != 0:
			detail = (res.stderr or res.stdout or "").strip() or _("unknown error")
			self.show_keyring_error(_("Failed to enable keyring unlocking: {}").format(detail))
			return False
		return True

	def validate_and_save_keyring(self):
		checkbox = self.builder.get_object("keyring_checkbox")
		if not checkbox.get_active():
			return self._disable_keyring_for_user()

		user = self.get_real_user()
		if not user or user == "root":
			self.show_keyring_error(_("Could not identify non-root system user for keyring unlocking"))
			return False

		password = self._ask_and_verify_password(user)
		if password is None:
			return False

		return self._enable_keyring_for_user(user, password)

	def show_keyring_error(self, message):
		gtk4compat.alert(self.window, _("Keyring Unlocking Error"), message)

	def execute_slide7(self):
		"""Last page: show the current level and the nod switch, and offer Finish.

		Nothing is written here. The values are saved by on_finishbutton_click,
		because this used to write the level the moment the page appeared: picking
		Balanced after arriving and clicking Finish left the config on Secure, and
		the nod switch would have been lost the same way.
		"""
		if self._selected_security_radio() is None:
			self.show_error(_("Error reading radio buttons"))
		self.load_security_settings()

		self.nextbutton.set_visible(False)
		self.builder.get_object("cancelbutton").set_visible(False)

		finishbutton = self.builder.get_object("finishbutton")
		finishbutton.set_visible(True)
		self.builder.get_object("navigationbar").queue_resize()
		self.window.queue_resize()
		self.window.set_focus(finishbutton)

	def _selected_security_radio(self):
		# GTK 4 radio buttons are grouped GtkCheckButtons without a group list API.
		for name in SECURITY_PRESETS:
			button = self.builder.get_object(name)
			if button is not None and button.get_active():
				return name
		return None

	def load_security_settings(self):
		"""Reflect the config in the nod switch.

		The nod is a choice the user may already have made in Settings; a re-run
		of the wizard must show it as it is rather than silently turning it off.
		"""
		switch = self.builder.get_object("liveness_switch")
		if switch is None:
			return
		try:
			enabled = config_edit.get_bool(paths_factory.config_file_path(), "rubberstamps", "enabled", False)
		except Exception:
			enabled = False
		switch.set_active(bool(enabled))

	def save_security_settings(self):
		"""Write the strictness level and the nod choice. Returns False if saving failed.

		Written through the same comment-preserving editor the Settings tab uses,
		rather than `ubuntu-hello set`: that command can only replace a key the
		file already has, so on a config written before `confirmations` existed it
		would fail and leave the level half applied.
		"""
		radio_selected = self._selected_security_radio() or DEFAULT_PRESET
		certainty = SECURITY_PRESETS[radio_selected]
		confirmations = SECURITY_CONFIRMATIONS[radio_selected]
		switch = self.builder.get_object("liveness_switch")
		liveness = bool(switch is not None and switch.get_active())
		try:
			path = paths_factory.config_file_path()
			config_edit.set_option(path, "video", "certainty", str(certainty))
			config_edit.set_option(path, "video", "confirmations", str(confirmations))
			if liveness:
				# The shipped rule is fail-open on timeout; write the fail-closed one,
				# exactly as the Settings tab does, or a user who never nods gets in.
				config_edit.set_option(path, "rubberstamps", "stamp_rules", LIVENESS_RULE)
			config_edit.set_option(path, "rubberstamps", "enabled", "true" if liveness else "false")
			return True
		except (OSError, ValueError):
			return False

	def on_finishbutton_click(self, button):
		if not self.save_security_settings():
			# Non-fatal: the rest of setup is complete, so warn rather than exit.
			gtk4compat.alert(self.window, _("Could not save the security settings, but setup is otherwise complete."))
		self.completed = True
		self.window.destroy()
		if getattr(self, "run_main_loop", True):
			gtk4compat.quit_main()

	def enable_next(self):
		self.nextbutton.set_sensitive(True)
		self.window.set_focus(self.nextbutton)

	def show_error(self, error, secon=""):
		gtk4compat.alert(self.window, error, secon)
		self.exit()

	def on_camera_selection_changed(self, selection=None):
		"""Selection changed in the camera list (gtk4compat.ColumnList): switch the live preview."""
		row = self.selected_camera()
		if row is None:
			self.stop_preview(clear_image=False)
			return
		device_path = row[2]

		if self.current_preview_path == device_path:
			return

		# "preview_image" is slide 2's own preview widget (see __init__); a
		# prior slide (slide4's face-scan step) may have pointed
		# self.preview_image at slide4_preview_image instead, so reset it
		# here. There is no "slide2_preview_image" object in the Glade
		# file -- referencing self.slide2_preview_image raised an
		# uncaught AttributeError on every camera selection, which the
		# unit test suite never caught because it runs against a mocked
		# Gtk.Window that auto-vivifies any missing attribute instead of
		# raising.
		self.preview_image = self.builder.get_object("preview_image")
		self.stop_preview()
		self.current_preview_path = device_path
		self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(device_path,), daemon=True)
		self.preview_thread.start()

	def _open_preview_capture(self, cv2, time, device_path):
		"""Open the preview camera, retrying briefly. None if it never opens.

		device_path is a /dev/v4l/by-path/... symlink; resolve it to the real
		/dev/videoN node like every other camera-opening call in this file
		(scan_cameras_thread, execute_slide3). Opening the symlink directly was
		a regression from a later preview-thread rewrite -- some
		V4L2/GStreamer driver + kernel combinations bind the wrong underlying
		node (or none at all) when handed a by-path symlink instead of the
		canonical device, which can leave the caller spinning on failed reads
		with no visible preview and no error ("stuck testing webcams").

		The device also needs a moment to settle after `ubuntu-hello add`
		released it, hence the retries.
		"""
		real_path = os.path.realpath(device_path)
		for _attempt in range(6):
			cap = cv2.VideoCapture(real_path)
			if cap.isOpened():
				return cap
			_release_quietly(cap)
			if self.current_preview_path != device_path:
				return None
			time.sleep(0.4)
		return None

	def _preview_scaling_factor(self, cv2, cap):
		"""Scale that fits the frame into the slide's preview box."""
		try:
			width = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
		except Exception:
			width = 640.0
		try:
			height = float(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
		except Exception:
			height = 480.0

		if self.preview_image == self.slide4_preview_image:
			preview_max_height, preview_max_width = 350, 780
		else:
			preview_max_height, preview_max_width = 200, 780

		scaling_factor = (preview_max_height / height) or 1
		if width * scaling_factor > preview_max_width:
			scaling_factor = (preview_max_width / width) or 1
		return scaling_factor

	def _post_preview_frame(self, cv2, frame, scaling_factor, device_path):
		try:
			frame = cv2.resize(frame, None, fx=scaling_factor, fy=scaling_factor, interpolation=cv2.INTER_AREA)
			retval, buffer = cv2.imencode(".png", frame)
			if retval and buffer is not None:
				raw_bytes = buffer.tobytes() if hasattr(buffer, "tobytes") else bytes(buffer)
				GLib.idle_add(self.update_preview_image_widget, device_path, raw_bytes)
		except Exception as e:
			print("Error processing preview frame:", e)

	def open_camera_for_preview(self, device_path):
		cap = None
		try:
			import cv2
			import time

			cap = self._open_preview_capture(cv2, time, device_path)
			if cap is None or self.current_preview_path != device_path:
				return

			self.preview_capture = cap
			scaling_factor = self._preview_scaling_factor(cv2, cap)

			while self.current_preview_path == device_path:
				ret, frame = cap.read()
				if ret and frame is not None:
					self._post_preview_frame(cv2, frame, scaling_factor, device_path)
				time.sleep(0.03)
		except Exception as e:
			print("Error in camera preview thread:", e)
		finally:
			_release_quietly(cap)

	def update_preview_image_widget(self, device_path, data):
		if self.current_preview_path == device_path and getattr(self, "preview_image", None) and data is not None:
			try:
				if not isinstance(data, (bytes, bytearray)):
					pix = data
				else:
					loader = pixbuf.PixbufLoader()
					loader.write(data)
					loader.close()
					pix = loader.get_pixbuf()
				if pix is not None:
					self.preview_image.set_paintable(gtk4compat.pixbuf_to_texture(pix))
			except Exception:
				pass
		return False

	def stop_preview(self, clear_image=False):
		self.current_preview_path = None
		self.preview_capture = None
		if hasattr(self, 'preview_thread') and self.preview_thread is not None:
			try:
				self.preview_thread.join(timeout=2.0)
			except Exception:
				pass
			self.preview_thread = None

	def exit(self, widget=None, context=None):
		"""Cleanly exit"""
		self.stop_preview()
		if getattr(self, "run_main_loop", True):
			gtk4compat.quit_main()
			if not self.completed:
				sys.exit(0)
		return False

	def get_display_version(self):
		"""Return UI version from VERSION / paths (never older git tags)."""
		from version_display import get_display_version as _display_version

		return _display_version(os.path.dirname(os.path.abspath(__file__)))
