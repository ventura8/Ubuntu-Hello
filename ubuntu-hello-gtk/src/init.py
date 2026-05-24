# Opens auth ui if requested, otherwise starts normal ui
import sys
import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

# Set the application name and program name so GNOME Shell maps the window to the desktop file.
GLib.set_prgname("ubuntu-hello-gtk")
GLib.set_application_name("Ubuntu Hello")

if "--start-auth-ui" in sys.argv:
	import authsticky
else:
	import window

