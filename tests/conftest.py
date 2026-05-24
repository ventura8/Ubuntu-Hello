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

# Create mock for paths module
mock_paths = MagicMock()
mock_paths.config_dir = PurePath("/etc/ubuntu-hello")
mock_paths.dlib_data_dir = PurePath("/usr/share/ubuntu-hello/dlib-data")
mock_paths.user_models_dir = PurePath("/etc/ubuntu-hello/models")
mock_paths.log_path = PurePath("/var/log/ubuntu-hello")
mock_paths.data_dir = PurePath("/usr/share/ubuntu-hello")
sys.modules['paths'] = mock_paths

# Create mock for keyboard module
sys.modules['keyboard'] = MagicMock()

# Create mock for pyv4l2
mock_pyv4l2 = MagicMock()
sys.modules['pyv4l2'] = mock_pyv4l2
sys.modules['pyv4l2.frame'] = MagicMock()

# Create mock for ffmpeg
sys.modules['ffmpeg'] = MagicMock()

# Create mock for cv2
mock_cv2 = MagicMock()
mock_cv2.CAP_PROP_FRAME_WIDTH = 3
mock_cv2.CAP_PROP_FRAME_HEIGHT = 4
mock_cv2.CAP_PROP_FPS = 5
mock_cv2.CAP_PROP_FOURCC = 6
mock_cv2.CAP_V4L = 200
mock_cv2.COLOR_BGR2GRAY = 6
sys.modules['cv2'] = mock_cv2
sys.modules['cv2.cv2'] = mock_cv2

# Create mock for cairo
sys.modules['cairo'] = MagicMock()

# Create mock for gi and gi.repository
mock_gi = MagicMock()
sys.modules['gi'] = mock_gi

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
        kwargs.pop('parent', None)
        kwargs.pop('title', None)
        kwargs.pop('flags', None)
        super().__init__(*args, **kwargs)

    def _get_child_mock(self, /, **kw):
        return MagicMock(**kw)

mock_gtk.Window = MockGtkWidget
mock_gtk.Dialog = MockGtkWidget




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

sys.modules['gi.repository'] = mock_repository
sys.modules['gi.repository.Gtk'] = mock_gtk
sys.modules['gi.repository.Gdk'] = mock_gdk
sys.modules['gi.repository.GObject'] = mock_gobject
sys.modules['gi.repository.Pango'] = mock_pango
sys.modules['gi.repository.GLib'] = mock_glib
sys.modules['gi.repository.Gio'] = mock_gio

# GdkPixbuf mock for tab_video
mock_pixbuf = MagicMock()
sys.modules['gi.repository.GdkPixbuf'] = mock_pixbuf
mock_repository.GdkPixbuf = mock_pixbuf

# Create mock for dlib
sys.modules['dlib'] = MagicMock()
