# Shows a floating window when authenticating
import cairo
import gi
import signal
import sys
import paths_factory
import os

from i18n import _

# Make sure we have the libs we need
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

# Import them
from gi.repository import Gtk as gtk
from gi.repository import Gdk as gdk
from gi.repository import GObject as gobject
from gi.repository import Gio


def get_real_user():
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
		user = os.environ.get("USER")
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
	if user and re.match(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$", user):
		return user
	return "root"


def get_theme_preference():
	try:
		if os.geteuid() == 0:
			user = get_real_user()
			if not user or user == "root":
				return "dark"

			import subprocess
			try:
				cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "dconf", "read", "/org/gnome/desktop/interface/color-scheme"]
				color_scheme = subprocess.check_output(cmd, text=True).strip().strip("'\"")
				if color_scheme == "prefer-dark":
					return "dark"
				elif color_scheme == "prefer-light":
					return "light"
			except Exception:
				pass

			try:
				cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "dconf", "read", "/org/gnome/desktop/interface/gtk-theme"]
				gtk_theme = subprocess.check_output(cmd, text=True).strip().strip("'\"")
				if "dark" in gtk_theme.lower():
					return "dark"
			except Exception:
				pass

			try:
				cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]
				color_scheme = subprocess.check_output(cmd, text=True).strip().strip("'\"")
				if color_scheme == "prefer-dark":
					return "dark"
				elif color_scheme == "prefer-light":
					return "light"
			except Exception:
				pass

			try:
				cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]
				gtk_theme = subprocess.check_output(cmd, text=True).strip().strip("'\"")
				if "dark" in gtk_theme.lower():
					return "dark"
			except Exception:
				pass

			return "light"
		else:
			# Check if the schema exists
			schemas = Gio.SettingsSchemaSource.get_default().list_schemas(True)
			all_schemas = schemas[0] + schemas[1]
			if "org.gnome.desktop.interface" not in all_schemas:
				return "dark"

			settings = Gio.Settings.new("org.gnome.desktop.interface")
			color_scheme = ""
			try:
				color_scheme = settings.get_string("color-scheme")
			except Exception:
				pass

			gtk_theme = ""
			try:
				gtk_theme = settings.get_string("gtk-theme")
			except Exception:
				pass

			prefer_dark = False
			if color_scheme == "prefer-dark":
				prefer_dark = True
			elif gtk_theme and "dark" in gtk_theme.lower():
				prefer_dark = True
			else:
				prefer_dark = False

			return "dark" if prefer_dark else "light"
	except Exception:
		return "dark"

# Set window size constants
windowWidth = 400
windowHeight = 100


class StickyWindow(gtk.Window):
	# Set default messages to show in the popup
	message = _("Loading...  ")
	subtext = ""

	def __init__(self):
		"""Initialize the sticky window"""
		# Make the class a GTK window
		gtk.Window.__init__(self)

		# Get the absolute or relative path to the logo file
		logo_path = paths_factory.logo_path()

		# Create image and calculate scale size based on image size
		self.logo_surface = cairo.ImageSurface.create_from_png(logo_path)
		self.logo_ratio = float(windowHeight - 20) / float(self.logo_surface.get_height())

		# Set the title of the window
		self.set_title(_("Ubuntu Hello Authentication"))

		# Set a bunch of options to make the window stick and be on top of everything
		self.stick()
		self.set_gravity(gdk.Gravity.STATIC)
		self.set_resizable(False)
		self.set_keep_above(True)
		self.set_app_paintable(True)
		self.set_skip_pager_hint(True)
		self.set_skip_taskbar_hint(True)
		self.set_can_focus(False)
		self.set_can_default(False)
		self.set_focus(None)
		self.set_type_hint(gdk.WindowTypeHint.NOTIFICATION)
		self.set_decorated(False)

		# Listen for a window redraw
		self.connect("draw", self.draw)
		# Listen for a force close or click event and exit
		self.connect("destroy", self.exit)
		self.connect("delete_event", self.exit)
		self.connect("button-press-event", self.exit)
		self.connect("button-release-event", self.exit)

		# Create a GDK drawing, restricts the window size
		darea = gtk.DrawingArea()
		darea.set_size_request(windowWidth, windowHeight)
		self.add(darea)

		# Get the default screen
		screen = gdk.Screen.get_default()
		visual = screen.get_rgba_visual()
		self.set_visual(visual)

		# Move the window to the center top of the default window, where a webcam usually is
		self.move((screen.get_width() / 2) - (windowWidth / 2), 0)

		# Show window and force a resize again
		self.show_all()
		self.resize(windowWidth, windowHeight)

		# Add a timeout to catch input passed from compare.py
		gobject.timeout_add(100, self.catch_stdin)

		# Start GTK main loop
		gtk.main()

	def draw(self, widget, ctx):
		"""Draw the UI"""
		# Change cursor to the kill icon
		self.get_window().set_cursor(gdk.Cursor(gdk.CursorType.PIRATE))

		theme = get_theme_preference()

		# Draw a semi transparent background
		if theme == "dark":
			ctx.set_source_rgba(0, 0, 0, .7)
		else:
			ctx.set_source_rgba(0.95, 0.95, 0.95, .85)
		ctx.set_operator(cairo.OPERATOR_SOURCE)
		ctx.paint()
		ctx.set_operator(cairo.OPERATOR_OVER)

		# Position and draw the logo
		ctx.translate(15, 10)
		ctx.scale(self.logo_ratio, self.logo_ratio)
		ctx.set_source_surface(self.logo_surface)
		ctx.paint()

		# Calculate main message positioning, as the text is higher if there's a subtext
		if self.subtext:
			ctx.move_to(380, 145)
		else:
			ctx.move_to(380, 175)

		# Draw the main message
		if theme == "dark":
			ctx.set_source_rgba(255, 255, 255, .9)
		else:
			ctx.set_source_rgba(0, 0, 0, .95)
		ctx.set_font_size(80)
		ctx.select_font_face("Arial", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
		ctx.show_text(self.message)

		# Draw the subtext if there is one
		if self.subtext:
			ctx.move_to(380, 210)
			if theme == "dark":
				ctx.set_source_rgba(230, 230, 230, .8)
			else:
				ctx.set_source_rgba(50, 50, 50, .85)
			ctx.set_font_size(40)
			ctx.select_font_face("Arial", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
			ctx.show_text(self.subtext)

	def catch_stdin(self):
		"""Catch input from stdin and redraw"""
		# Wait for a line on stdin
		comm = sys.stdin.readline()[:-1]

		# If the line is not empty
		if comm:
			# Parse a message
			if comm[0] == "M":
				self.message = comm[2:].strip()
			# Parse subtext
			if comm[0] == "S":
				# self.subtext += " "
				self.subtext = comm[2:].strip()

		# Redraw the ui
		self.queue_draw()

		# Fire this function again in 10ms, as we're waiting on IO in readline anyway
		gobject.timeout_add(10, self.catch_stdin)

	def exit(self, widget, context):
		"""Cleanly exit"""
		gtk.main_quit()
		return True


# Make sure we quit on a SIGINT
signal.signal(signal.SIGINT, signal.SIG_DFL)

# Open the GTK window
window = StickyWindow()
