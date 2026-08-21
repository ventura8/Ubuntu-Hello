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


from i18n import _
from wallet_backend import wallet_backend_label, wallet_unlock_phrase

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk as gtk
from gi.repository import Gdk as gdk
from gi.repository import GObject as gobject
from gi.repository import Pango as pango
from gi.repository import GLib


class KeyringPasswordDialog(gtk.Dialog):
	def __init__(self, parent, user):
		gtk.Dialog.__init__(self, title=_("Enable Keyring/KWallet Unlocking"), parent=parent, flags=gtk.DialogFlags.MODAL)
		self.add_buttons(gtk.STOCK_CANCEL, gtk.ResponseType.CANCEL, gtk.STOCK_OK, gtk.ResponseType.OK)
		self.set_default_response(gtk.ResponseType.OK)
		self.set_resizable(False)

		box = self.get_content_area()
		box.set_spacing(10)
		box.set_margin_left(15)
		box.set_margin_right(15)
		box.set_margin_top(15)
		box.set_margin_bottom(15)

		label = gtk.Label()
		label.set_markup(_("Enter password for user <b>{}</b> to unlock {} ({}):").format(
			user, wallet_unlock_phrase(), wallet_backend_label()))
		label.set_alignment(0.0, 0.5)
		box.pack_start(label, False, False, 0)

		self.entry1 = gtk.Entry()
		self.entry1.set_visibility(False)
		self.entry1.set_placeholder_text(_("Password"))
		# Connect the 'activate' signal of the entry to respond with OK when user hits Enter
		self.entry1.connect("activate", lambda entry: self.response(gtk.ResponseType.OK))
		box.pack_start(self.entry1, False, False, 0)

		self.show_all()


class OnboardingWindow(gtk.Window):
	def __init__(self, run_main_loop=True):
		"""Initialize the sticky window"""
		# Load the custom CSS theme stylesheet
		paths_factory.load_custom_css()

		# Make the class a GTK window
		gtk.Window.__init__(self)

		self.completed = False
		self.run_main_loop = run_main_loop

		self.builder = gtk.Builder()
		self.builder.set_translation_domain("ubuntu-hello-gtk")
		self.builder.add_from_file(paths_factory.onboarding_wireframe_path())
		self.builder.connect_signals(self)

		self.window = self.builder.get_object("onboardingwindow")
		self.slidecontainer = self.builder.get_object("slidecontainer")
		self.nextbutton = self.builder.get_object("nextbutton")
		self.version_label = self.builder.get_object("version_label")
		if self.version_label:
			self.version_label.set_text(self.get_display_version())

		self.window.connect("destroy", self.exit)
		self.window.connect("delete_event", self.exit)

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
		self.preview_image.set_size_request(400, 200)
		self.slide4_preview_image = self.builder.get_object("slide4_preview_image")
		self.slide4_preview_image.set_size_request(600, 350)
		self.slide4_instruction_label = self.builder.get_object("slide4_instruction_label")
		self.preview_capture = None
		self.current_preview_path = None
		self.preview_thread = None

		self.window.set_default_size(800, 680)
		self.window.set_position(gtk.WindowPosition.CENTER)
		self.window.show_all()

		try:
			screen = self.window.get_screen()
			display = screen.get_display() if screen else gdk.Display.get_default()
			monitor = display.get_primary_monitor() if display else None
			if monitor:
				geometry = monitor.get_geometry()
				x = geometry.x + (geometry.width - 800) // 2
				y = geometry.y + (geometry.height - 680) // 2
				self.window.move(x, y)
			elif screen:
				x = (screen.get_width() - 800) // 2
				y = (screen.get_height() - 680) // 2
				self.window.move(x, y)
		except Exception as e:
			print("Error centering window:", e)

		# Initialize preview images with blank pixbuf to avoid empty GTK3 style crash
		try:
			from gi.repository import GdkPixbuf
			blank = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
			for img_id in ("preview_image", "slide4_preview_image"):
				img = self.builder.get_object(img_id)
				if img:
					img.set_from_pixbuf(blank)
		except Exception:
			pass

		# Hide the finish button initially
		self.builder.get_object("finishbutton").hide()

		self.window.current_slide = 0

		# Start GTK main loop if requested
		if run_main_loop:
			gtk.main()

	def go_next_slide(self, button=None):
		if self.window.current_slide == 6:
			if not self.validate_and_save_keyring():
				self.enable_next()
				return

		self.nextbutton.set_sensitive(False)

		# Stop camera preview if moving away from slide 2
		if self.window.current_slide == 2:
			self.stop_preview()

		self.slides[self.window.current_slide].hide()
		self.slides[self.window.current_slide + 1].show()
		self.window.current_slide += 1
		# the shown child may have zero/wrong dimensions
		self.slidecontainer.queue_resize()

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

	def execute_slide1(self):
		self.downloadoutputlabel = self.builder.get_object("downloadoutputlabel")
		eventbox = self.builder.get_object("downloadeventbox")

		# TODO: Better way to do this?
		if os.path.exists(paths_factory.dlib_data_dir_path() / "shape_predictor_5_face_landmarks.dat"):
			self.downloadoutputlabel.set_text(_("Datafiles have already been downloaded!\nClick Next to continue"))
			self.enable_next()
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

		threading.Thread(target=self.scan_cameras_thread, daemon=True).start()

	def scan_cameras_thread(self):
		import numpy as np
		try:
			import cv2
		except Exception:
			GLib.idle_add(self.show_error, _("Error while importing OpenCV2"), _("Try reinstalling cv2"))
			return

		device_rows = []
		try:
			device_ids = os.listdir("/dev/v4l/by-path")
		except Exception:
			GLib.idle_add(self.show_error, _("No webcams found on system"), _("Please configure your camera yourself if you are sure a compatible camera is connected"))
			return

		# Loop though all devices
		for dev in device_ids:
			time.sleep(.5)

			# The full path to the device is the default name
			device_path = "/dev/v4l/by-path/" + dev
			device_name = dev

			# Get the udevadm details to try to get a better name
			try:
				udevadm = subprocess.check_output(["udevadm", "info", "-r", "--query=all", "-n", device_path]).decode("utf-8")

				# Loop though udevadm to search for a better name
				for line in udevadm.split("\n"):
					# Match it and encase it in quotes
					re_name = re.search('product.*=(.*)$', line, re.IGNORECASE)
					if re_name:
						device_name = re_name.group(1)
			except Exception:
				pass

			real_path = os.path.realpath(device_path)
			capture = cv2.VideoCapture(real_path)
			is_open, frame = capture.read()
			if not is_open:
				device_rows.append([device_name, device_path, -9, _("No, camera can't be opened")])
				continue

			try:
				# Use numpy to check if grayscale / infrared extremely quickly
				is_gray = np.all(frame[:, :, 0] == frame[:, :, 1]) and np.all(frame[:, :, 1] == frame[:, :, 2])
				if not is_gray:
					raise Exception()
			except Exception:
				device_rows.append([device_name, device_path, -5, _("No, not an infrared camera")])
				capture.release()
				continue

			device_rows.append([device_name, device_path, 5, _("Yes, compatible infrared camera")])
			capture.release()

		device_rows = sorted(device_rows, key=lambda k: -k[2])

		GLib.idle_add(self.update_camera_list_gui, device_rows)

	def update_camera_list_gui(self, device_rows):
		self.treeview = gtk.TreeView()
		self.treeview.set_vexpand(True)

		self.listmodel = gtk.ListStore(str, str, str, bool)
		for device in device_rows:
			is_gray = device[2] == 5
			self.listmodel.append([device[0], device[3], device[1], is_gray])

		self.treeview.set_model(self.listmodel)

		for i, column in enumerate([_("Camera identifier or path"), _("Recommended")]):
			cell = gtk.CellRendererText()
			col = gtk.TreeViewColumn(column, cell, text=i)
			self.treeview.append_column(col)

		self.scrolled_window = gtk.ScrolledWindow()
		self.scrolled_window.set_policy(gtk.PolicyType.NEVER, gtk.PolicyType.AUTOMATIC)
		self.scrolled_window.set_shadow_type(gtk.ShadowType.IN)
		self.scrolled_window.set_vexpand(True)
		self.scrolled_window.set_hexpand(True)
		self.loadinglabel.hide()
		self.scrolled_window.add(self.treeview)
		self.devicelistbox.add(self.scrolled_window)
		self.scrolled_window.show_all()
		self.enable_next()

		self.treeview.get_selection().connect("changed", self.on_camera_selection_changed)
		if len(device_rows) > 0:
			self.treeview.set_cursor(0)

		# Ensure preview is started for the first device if selection did not trigger it
		if device_rows and not self.current_preview_path and not self.preview_thread:
			default_path = device_rows[0][1]
			self.preview_image = self.builder.get_object("preview_image")
			self.current_preview_path = default_path
			self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(default_path,), daemon=True)
			self.preview_thread.start()

	def execute_slide3(self):
		try:
			import cv2
		except Exception:
			self.show_error(_("Error while importing OpenCV2"), _("Try reinstalling cv2"))

		selection = self.treeview.get_selection()
		model, treeiter = selection.get_selected()
		if treeiter is None:
			model, rowlist = selection.get_selected_rows()
			if len(rowlist) == 1:
				try:
					treeiter = model.get_iter(rowlist[0])
				except Exception:
					pass
		
		if treeiter is None:
			self.show_error(_("Error selecting camera"))
			return
   
		device_path = model.get_value(treeiter, 2)
		is_gray = model.get_value(treeiter, 3)

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
		self.builder.get_object("leieyesbutton").hide()
		self.builder.get_object("leienobutton").hide()

	def execute_slide4(self):
		selection = self.treeview.get_selection()
		model, treeiter = selection.get_selected()
		if treeiter is None:
			model, rowlist = selection.get_selected_rows()
			if len(rowlist) == 1:
				try:
					treeiter = model.get_iter(rowlist[0])
				except Exception:
					pass

		if treeiter is None:
			self.show_error(_("Error selecting camera"))
			return

		device_path = model.get_value(treeiter, 2)
		try:
			self.proc = subprocess.Popen(["ubuntu-hello", "set", "device_path", device_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		except Exception:
			self.proc = subprocess.Popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

		self.window.set_focus(self.builder.get_object("scanbutton"))

		# Start preview on slide 4 preview image
		self.preview_image = self.slide4_preview_image
		self.current_preview_path = None
		self.stop_preview()
		self.current_preview_path = device_path
		self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(device_path,), daemon=True)
		self.preview_thread.start()

	def on_scanbutton_click(self, button):
		# Stop camera preview to avoid device-busy conflict during face scan, but keep the image visible
		self.stop_preview(clear_image=False)

		status = self.proc.wait(2)

		# Change button label to instruction text and disable it
		scanbutton = button or self.builder.get_object("scanbutton")
		if scanbutton:
			scanbutton.set_label(_("Please look directly into the camera"))
			scanbutton.set_sensitive(False)

		# Wait a bit to allow the user to read the message
		gobject.timeout_add(600, self.run_add)

	def run_add(self):
		res = subprocess.run(["ubuntu-hello", "-y", "add"], capture_output=True, text=True)
		status, output = res.returncode, res.stdout + res.stderr

		print("ubuntu-hello add output:")
		print(output)

		if status != 0:
			# Restore button state in case of error (though exit will close the app)
			scanbutton = self.builder.get_object("scanbutton")
			if scanbutton:
				scanbutton.set_label(_("Start face scan"))
				scanbutton.set_sensitive(True)
			self.show_error(_("Can't save face model"), output)

		gobject.timeout_add(10, self.go_next_slide)
		return False

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
		pass

	def get_real_user(self):
		import re
		user = os.environ.get("SUDO_USER")
		if not user or user == "root":
			pkexec_uid = os.environ.get("PKEXEC_UID")
			if pkexec_uid:
				try:
					import pwd
					user = pwd.getpwuid(int(pkexec_uid)).pw_name
				except Exception:
					pass
		if not user or user == "root":
			try:
				user = os.getlogin()
			except Exception:
				pass
		if not user or user == "root":
			try:
				import subprocess
				out = subprocess.check_output(["loginctl", "list-sessions", "--no-legend"], text=True)
				for line in out.strip().split("\n"):
					parts = line.split()
					if len(parts) >= 3 and parts[2] != "root":
						user = parts[2]
						break
			except Exception:
				pass
		if not user or user == "root":
			user = os.environ.get("USER")
		if user and re.match(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$", user):
			return user
		return "root"

	def validate_and_save_keyring(self):
		tpm_keys_dir = paths_factory.tpm_keys_dir_path()
		keyring_keys_dir = paths_factory.keyring_keys_dir_path()
		pending_dir = paths_factory.keyring_pending_dir_path()

		checkbox = self.builder.get_object("keyring_checkbox")
		if not checkbox.get_active():
			# Disable / skip: delete pending file and any saved keys for user if identified
			user = self.get_real_user()
			if user and user != "root":
				key_file = os.path.join(keyring_keys_dir, user)
				pub_file = os.path.join(tpm_keys_dir, f"{user}.pub")
				priv_file = os.path.join(tpm_keys_dir, f"{user}.priv")
				pending_file = os.path.join(pending_dir, user)
				unlink_errors = []
				for path in (pending_file, key_file, pub_file, priv_file):
					if os.path.exists(path):
						try:
							os.unlink(path)
						except Exception as e:
							unlink_errors.append(f"{path}: {e}")
				if unlink_errors:
					self.show_keyring_error(_("Failed to disable keyring unlocking: {}").format("; ".join(unlink_errors)))
					return False
			return True

		user = self.get_real_user()
		if not user or user == "root":
			self.show_keyring_error(_("Could not identify non-root system user for keyring unlocking"))
			return False

		# Ask user for password immediately using the secure GTK dialog popup!
		dialog = KeyringPasswordDialog(self.window, user)
		response = dialog.run()
		passwd1 = dialog.entry1.get_text()
		dialog.destroy()

		if response != gtk.ResponseType.OK:
			return False

		if not passwd1:
			self.show_keyring_error(_("Password cannot be empty"))
			return False

		if not auth_helper.verify_user_password(user, passwd1):
			self.show_keyring_error(_("Incorrect password for user {}").format(user))
			return False

		try:
			res = subprocess.run(
				["ubuntu-hello", "keyring", "enable", "-U", user],
				input=passwd1 + "\n",
				capture_output=True,
				text=True,
				timeout=120,
			)
			if res.returncode != 0:
				detail = (res.stderr or res.stdout or "").strip() or _("unknown error")
				self.show_keyring_error(_("Failed to enable keyring unlocking: {}").format(detail))
				return False
		except FileNotFoundError:
			self.show_keyring_error(_("Failed to enable keyring unlocking: ubuntu-hello executable not found"))
			return False
		except subprocess.TimeoutExpired:
			self.show_keyring_error(_("Failed to enable keyring unlocking: timed out waiting for keyring enable"))
			return False
		except (OSError, Exception) as e:
			self.show_keyring_error(_("Failed to enable keyring unlocking: {}").format(str(e)))
			return False

		return True

	def show_keyring_error(self, message):
		dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		dialog.set_title(_("Keyring Unlocking Error"))
		dialog.props.text = message
		dialog.run()
		dialog.destroy()

	def execute_slide7(self):
		radio_buttons = self.builder.get_object("radiobalanced").get_group()
		radio_selected = False
		radio_certanty = 5.0

		for button in radio_buttons:
			if button.get_active():
				radio_selected = gtk.Buildable.get_name(button)

		if not radio_selected:
			self.show_error(_("Error reading radio buttons"))
		elif radio_selected == "radiofast":
			radio_certanty = 4.2
		elif radio_selected == "radiobalanced":
			radio_certanty = 3.5
		elif radio_selected == "radiosecure":
			radio_certanty = 2.2

		self.proc = subprocess.Popen(["ubuntu-hello", "set", "certainty", str(radio_certanty)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

		self.nextbutton.hide()
		self.builder.get_object("cancelbutton").hide()

		finishbutton = self.builder.get_object("finishbutton")
		finishbutton.show()
		self.builder.get_object("navigationbar").queue_resize()
		self.window.queue_resize()
		self.window.set_focus(finishbutton)

		try:
			status = self.proc.wait(2)
		except Exception:
			status = -1

		if status != 0:
			# Non-fatal warning instead of exiting the wizard. Show dialog but do not call self.show_error which exits!
			dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.WARNING, buttons=gtk.ButtonsType.CLOSE)
			dialog.set_title(_("Ubuntu Hello Setup Warning"))
			dialog.props.text = _("Could not save certainty preference, but setup is otherwise complete.")
			dialog.run()
			dialog.destroy()

	def on_finishbutton_click(self, button):
		self.completed = True
		self.window.destroy()
		if getattr(self, "run_main_loop", True):
			try:
				gtk.main_quit()
			except RuntimeError:
				pass

	def enable_next(self):
		self.nextbutton.set_sensitive(True)
		self.window.set_focus(self.nextbutton)

	def show_error(self, error, secon=""):
		dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		dialog.set_title(_("Ubuntu Hello Error"))
		dialog.props.text = error
		dialog.format_secondary_text(secon)

		dialog.run()

		dialog.destroy()
		self.exit()

	def on_camera_selection_changed(self, selection):
		model, treeiter = selection.get_selected()
		if treeiter is None:
			model, rowlist = selection.get_selected_rows()
			if len(rowlist) == 1:
				try:
					treeiter = model.get_iter(rowlist[0])
				except Exception:
					pass

		if treeiter is None:
			self.stop_preview(clear_image=False)
			return

		try:
			device_path = model.get_value(treeiter, 2)
		except Exception:
			self.stop_preview(clear_image=False)
			return

		if self.current_preview_path == device_path:
			return

		self.preview_image = self.slide2_preview_image
		self.stop_preview()
		self.current_preview_path = device_path
		self.preview_thread = threading.Thread(target=self.open_camera_for_preview, args=(device_path,), daemon=True)
		self.preview_thread.start()

	def open_camera_for_preview(self, device_path):
		cap = None
		try:
			import cv2
			import time
			from gi.repository import GLib, GdkPixbuf as pixbuf

			cap = cv2.VideoCapture(device_path)
			if not cap.isOpened():
				return

			if self.current_preview_path != device_path:
				return

			self.preview_capture = cap

			try:
				width = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
			except Exception:
				width = 640.0
			try:
				height = float(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
			except Exception:
				height = 480.0

			if self.preview_image == self.slide4_preview_image:
				preview_max_height = 350
				preview_max_width = 780
			else:
				preview_max_height = 200
				preview_max_width = 780

			scaling_factor = (preview_max_height / height) or 1
			if width * scaling_factor > preview_max_width:
				scaling_factor = (preview_max_width / width) or 1

			while self.current_preview_path == device_path:
				ret, frame = cap.read()
				if not ret or frame is None:
					time.sleep(0.03)
					continue

				try:
					frame = cv2.resize(frame, None, fx=scaling_factor, fy=scaling_factor, interpolation=cv2.INTER_AREA)
					retval, buffer = cv2.imencode(".png", frame)
					if retval and buffer is not None:
						raw_bytes = buffer.tobytes() if hasattr(buffer, "tobytes") else bytes(buffer)
						GLib.idle_add(self.update_preview_image_widget, device_path, raw_bytes)
				except Exception as e:
					print("Error processing preview frame:", e)

				time.sleep(0.03)
		except Exception as e:
			print("Error in camera preview thread:", e)
		finally:
			if cap is not None:
				try:
					cap.release()
				except Exception:
					pass

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
					self.preview_image.set_from_pixbuf(pix)
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
			try:
				gtk.main_quit()
			except RuntimeError:
				pass
			if not self.completed:
				sys.exit(0)

	def get_display_version(self):
		"""Return UI version from VERSION / paths (never older git tags)."""
		from version_display import get_display_version as _display_version

		return _display_version(os.path.dirname(os.path.abspath(__file__)))
