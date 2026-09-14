import sys
import os
from pathlib import PurePath
from unittest.mock import MagicMock

# Define paths dynamically relative to conftest.py
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UBUNTU_HELLO_SRC = os.path.join(PROJECT_ROOT, "ubuntu-hello", "src")
UBUNTU_HELLO_GTK_SRC = os.path.join(PROJECT_ROOT, "ubuntu-hello-gtk", "src")

# Add to sys.path
if UBUNTU_HELLO_SRC not in sys.path:
	sys.path.insert(0, UBUNTU_HELLO_SRC)
if UBUNTU_HELLO_GTK_SRC not in sys.path:
	sys.path.insert(0, UBUNTU_HELLO_GTK_SRC)

# Set environment variables to bypass root/elevation and onboarding checks
os.environ["BYPASS_ELEVATE"] = "1"

# Real-GTK Settings E2E (compat / xvfb) must not use the global gi mock.
# Invoked as: UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/
UH_REAL_GTK = os.environ.get("UH_REAL_GTK") == "1"

# Create mock for paths module (point at source tree for E2E Glade assets).
mock_paths = MagicMock()
mock_paths.config_dir = PurePath("/etc/ubuntu-hello")
mock_paths.dlib_data_dir = PurePath("/usr/share/ubuntu-hello/dlib-data")
mock_paths.user_models_dir = PurePath("/etc/ubuntu-hello/models")
mock_paths.log_path = PurePath("/var/log/ubuntu-hello")
if UH_REAL_GTK:
	mock_paths.data_dir = PurePath(UBUNTU_HELLO_GTK_SRC)
else:
	mock_paths.data_dir = PurePath("/usr/share/ubuntu-hello")
# VERSION is the single source of truth; tests use the "-dev" suffix.
_version_file = os.path.join(PROJECT_ROOT, "VERSION")
with open(_version_file, encoding="utf-8") as _vf:
	_project_version = _vf.read().strip().splitlines()[0].strip()
mock_paths.version = f"{_project_version}-dev"
sys.modules["paths"] = mock_paths

# i18n.py is meson-configured (i18n.py.in); provide identity gettext for tests.
import types

_i18n = types.ModuleType("i18n")
_i18n.DOMAIN = "ubuntu-hello-gtk" if UH_REAL_GTK else "ubuntu-hello"
_i18n.LOCALEDIR = "/usr/share/locale"
_i18n._ = lambda s: s
_i18n.ngettext = lambda singular, plural, n: singular if n == 1 else plural
_i18n.reload_from_preferences = lambda: None   # real i18n.py rebinds gettext; identity here
sys.modules["i18n"] = _i18n

# Create mock for keyboard module
sys.modules["keyboard"] = MagicMock()

# Create mock for pyv4l2
mock_pyv4l2 = MagicMock()
sys.modules["pyv4l2"] = mock_pyv4l2
sys.modules["pyv4l2.frame"] = MagicMock()

# Create mock for ffmpeg
sys.modules["ffmpeg"] = MagicMock()

if not UH_REAL_GTK:
	# Create mock for cv2
	mock_cv2 = MagicMock()
	mock_cv2.CAP_PROP_FRAME_WIDTH = 3
	mock_cv2.CAP_PROP_FRAME_HEIGHT = 4
	mock_cv2.CAP_PROP_FPS = 5
	mock_cv2.CAP_PROP_FOURCC = 6
	mock_cv2.CAP_V4L = 200
	mock_cv2.COLOR_BGR2GRAY = 6
	sys.modules["cv2"] = mock_cv2
	sys.modules["cv2.cv2"] = mock_cv2

	# Create mock for cairo
	sys.modules["cairo"] = MagicMock()

	# Create mock for gi and gi.repository
	mock_gi = MagicMock()
	sys.modules["gi"] = mock_gi

	mock_gtk = MagicMock()
	# Set ResponseType constants
	mock_gtk.ResponseType.OK = 1
	mock_gtk.ResponseType.CANCEL = 2
	mock_gtk.ResponseType.YES = 3
	mock_gtk.ResponseType.NO = 4

	# Gtk.Window and Gtk.Dialog - use a class that allows any attribute/method access
	class MockGtkWidget(MagicMock):
		"""Mock class for GTK widgets that allows subclassing and arbitrary method calls."""

		def __init__(self, *args, **kwargs):
			# Intercept and remove Gtk keyword arguments to prevent MagicMock interference
			kwargs.pop("parent", None)
			kwargs.pop("title", None)
			kwargs.pop("flags", None)
			super().__init__(*args, **kwargs)

		def _get_child_mock(self, /, **kw):
			return MagicMock(**kw)

	mock_gtk.Window = MockGtkWidget
	mock_gtk.Dialog = MockGtkWidget
	# Real (mock) classes so isinstance() checks in the app work under the mock.
	for _cls in ("Popover", "Native", "Revealer", "Label", "Button", "Entry", "DropDown", "Box",
	             "SearchEntry", "Picture", "Image", "Notebook", "ScrolledWindow", "CheckButton", "Switch"):
		setattr(mock_gtk, _cls, type(_cls, (MockGtkWidget,), {}))

	mock_gdk = MagicMock()
	mock_gobject = MagicMock()
	mock_pango = MagicMock()
	mock_glib = MagicMock()
	mock_gio = MagicMock()

	mock_repository = MagicMock()
	mock_repository.Gtk = mock_gtk
	mock_repository.Gdk = mock_gdk
	mock_repository.GObject = mock_gobject
	mock_repository.Pango = mock_pango
	mock_repository.GLib = mock_glib
	mock_repository.Gio = mock_gio

	sys.modules["gi.repository"] = mock_repository
	sys.modules["gi.repository.Gtk"] = mock_gtk
	sys.modules["gi.repository.Gdk"] = mock_gdk
	sys.modules["gi.repository.GObject"] = mock_gobject
	sys.modules["gi.repository.Pango"] = mock_pango
	sys.modules["gi.repository.GLib"] = mock_glib
	sys.modules["gi.repository.Gio"] = mock_gio

	# GdkPixbuf mock for tab_video
	mock_pixbuf = MagicMock()
	sys.modules["gi.repository.GdkPixbuf"] = mock_pixbuf
	mock_repository.GdkPixbuf = mock_pixbuf

	# GTK 4 has no Gtk.Dialog.run(); the app blocks via gtk4compat.run_dialog().
	# Under the gi mock, route that back to dialog.run() so unit tests keep
	# scripting answers with `dialog.run.return_value = ResponseType.X`.
	sys.path.insert(0, UBUNTU_HELLO_GTK_SRC)
	import gtk4compat as _gtk4compat
	_gtk4compat._real_run_dialog = _gtk4compat.run_dialog
	_gtk4compat._real_quit_main = _gtk4compat.quit_main
	_gtk4compat._real_dropdown = _gtk4compat.dropdown
	_gtk4compat.run_dialog = lambda dialog: dialog.run()
	_gtk4compat.quit_main = mock_gtk.main_quit
	# Combo adapters wrap a real Gtk.DropDown; under the mock, hand the widget
	# mock back so tests keep asserting remove_all/append_text/set_active on it.
	_gtk4compat.dropdown = lambda widget, on_changed=None: widget

# Create mock for dlib
sys.modules["dlib"] = MagicMock()
