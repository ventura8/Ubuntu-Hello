import configparser
import os
import glob

from i18n import _
import paths_factory

from gi.repository import Gtk as gtk
from gi.repository import Gdk as gdk
from gi.repository import GdkPixbuf as pixbuf
from gi.repository import GObject as gobject

MAX_HEIGHT = 300
MAX_WIDTH = 300


def get_camera_devices():
	devices = []
	if os.path.exists("/dev/v4l/by-path"):
		try:
			for dev in os.listdir("/dev/v4l/by-path"):
				devices.append("/dev/v4l/by-path/" + dev)
		except Exception:
			pass
	for dev in glob.glob("/dev/video*"):
		if dev not in devices:
			devices.append(dev)
	return sorted(devices)


def on_page_switch(self, notebook, page, page_num):
	if page_num == 1:

		try:
			self.config = configparser.ConfigParser()
			self.config.read(paths_factory.config_file_path())
		except Exception:
			print(_("Can't open camera"))

		path = self.config.get("video", "device_path")

		try:
			import cv2
			self.cv2 = cv2
		except Exception:
			print(_("Can't import OpenCV2"))

		# Populate the camera list
		self.populating_cameras = True
		cameraselect = self.builder.get_object("cameraselect")
		cameraselect.remove_all()

		devices = get_camera_devices()
		active_index = -1

		for idx, dev in enumerate(devices):
			cameraselect.append_text(dev)
			if dev == path:
				active_index = idx

		if path != "none" and path not in devices:
			cameraselect.append_text(path)
			active_index = len(devices)

		if active_index != -1:
			cameraselect.set_active(active_index)
		elif devices:
			cameraselect.set_active(0)
			path = devices[0]

		self.populating_cameras = False

		try:
			self.capture = self.cv2.VideoCapture(path)
		except Exception:
			print(_("Can't open camera"))

		opencvbox = self.builder.get_object("opencvbox")
		opencvbox.modify_bg(gtk.StateType.NORMAL, gdk.Color(red=0, green=0, blue=0))

		height = self.capture.get(self.cv2.CAP_PROP_FRAME_HEIGHT) or 1
		width = self.capture.get(self.cv2.CAP_PROP_FRAME_WIDTH) or 1

		self.scaling_factor = (MAX_HEIGHT / height) or 1

		if width * self.scaling_factor > MAX_WIDTH:
			self.scaling_factor = (MAX_WIDTH / width) or 1

		config_height = self.config.getfloat("video", "max_height", fallback=320.0)
		config_scaling = (config_height / height) or 1

		self.builder.get_object("videores").set_text(str(int(width)) + "x" + str(int(height)))
		self.builder.get_object("videoresused").set_text(str(int(width * config_scaling)) + "x" + str(int(height * config_scaling)))
		self.builder.get_object("videorecorder").set_text(self.config.get("video", "recording_plugin", fallback=_("Unknown")))

		gobject.timeout_add(10, self.capture_frame)

	elif self.capture is not None:
		self.capture.release()
		self.capture = None


def on_camera_change(self, combo):
	if getattr(self, "populating_cameras", False):
		return

	path = combo.get_active_text()
	if not path:
		return

	if self.capture is not None:
		self.capture.release()
		self.capture = None

	try:
		self.config.set("video", "device_path", path)
		with open(paths_factory.config_file_path(), "w") as f:
			self.config.write(f)
	except Exception as e:
		print("Error saving config:", e)

	try:
		self.capture = self.cv2.VideoCapture(path)
		height = self.capture.get(self.cv2.CAP_PROP_FRAME_HEIGHT) or 1
		width = self.capture.get(self.cv2.CAP_PROP_FRAME_WIDTH) or 1

		self.scaling_factor = (MAX_HEIGHT / height) or 1
		if width * self.scaling_factor > MAX_WIDTH:
			self.scaling_factor = (MAX_WIDTH / width) or 1

		config_height = self.config.getfloat("video", "max_height", fallback=320.0)
		config_scaling = (config_height / height) or 1

		self.builder.get_object("videores").set_text(str(int(width)) + "x" + str(int(height)))
		self.builder.get_object("videoresused").set_text(str(int(width * config_scaling)) + "x" + str(int(height * config_scaling)))
	except Exception as e:
		print("Error opening camera:", e)


def capture_frame(self):
	if self.capture is None:
		return

	ret, frame = self.capture.read()
	if not ret or frame is None:
		gobject.timeout_add(20, self.capture_frame)
		return

	frame = self.cv2.resize(frame, None, fx=self.scaling_factor, fy=self.scaling_factor, interpolation=self.cv2.INTER_AREA)

	retval, buffer = self.cv2.imencode(".png", frame)

	loader = pixbuf.PixbufLoader()
	loader.write(buffer)
	loader.close()
	buffer = loader.get_pixbuf()

	self.opencvimage.set_from_pixbuf(buffer)

	gobject.timeout_add(20, self.capture_frame)
