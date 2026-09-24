# Shows a floating window when authenticating (GTK 4)
import cairo
import gi
import signal
import sys
import paths_factory
import real_user
import os

from i18n import _

# Make sure we have the libs we need
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

# Import them
from gi.repository import Gtk as gtk
from gi.repository import Gdk as gdk
from gi.repository import GLib
from gi.repository import Gio


def get_real_user():
	"""The desktop user behind a root process, or "root" if none can be found."""
	return real_user.resolve((
		real_user.from_sudo,
		real_user.from_pkexec,
		real_user.from_login,
		real_user.from_user_env,
		real_user.from_loginctl,
	))


def get_theme_preference():
	"""Prefer multi-DE detection; overlay defaults to dark on hard failures."""
	try:
		import theme_detect
		if os.geteuid() == 0:
			user = get_real_user()
			if not user or user == "root":
				return "dark"
			return theme_detect.get_theme_preference(user=user, default="light")

		# Non-root: probe current session DE tools/configs first
		detected = theme_detect.get_theme_preference(default="")
		if detected in ("dark", "light"):
			return detected

		# GNOME Gio live schema when DE probe found nothing
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

		if color_scheme == "prefer-dark":
			return "dark"
		if gtk_theme and "dark" in gtk_theme.lower():
			return "dark"
		if color_scheme == "prefer-light" or gtk_theme:
			return "light"
		return "dark"
	except Exception:
		return "dark"

# Set window size constants
windowWidth = 400
windowHeight = 100

# Transparent window background: the drawing area paints the translucent panel.
_CSS = b"window.uh-overlay { background-color: transparent; }"


class StickyWindow(gtk.Window):
	# Set default messages to show in the popup
	message = _("Loading...  ")
	subtext = ""

	def __init__(self, run_main_loop=True):
		"""Initialize the sticky window"""
		# Make the class a GTK window
		gtk.Window.__init__(self)
		self.run_main_loop = run_main_loop
		self.loop = GLib.MainLoop() if run_main_loop else None

		# Get the absolute or relative path to the logo file
		logo_path = paths_factory.logo_path()

		# Create image and calculate scale size based on image size
		self.logo_surface = cairo.ImageSurface.create_from_png(logo_path)
		self.logo_ratio = float(windowHeight - 20) / float(self.logo_surface.get_height())

		# Set the title of the window
		self.set_title(_("Ubuntu Hello Authentication"))

		# GTK 4 / Wayland: no keep-above, gravity or explicit placement; the
		# compositor positions the undecorated, fixed-size overlay.
		self.set_resizable(False)
		self.set_decorated(False)
		self.set_default_size(windowWidth, windowHeight)
		self.add_css_class("uh-overlay")
		provider = gtk.CssProvider()
		provider.load_from_data(_CSS)
		gtk.StyleContext.add_provider_for_display(
			gdk.Display.get_default(), provider, gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

		# Listen for a force close or click event and exit
		self.connect("close-request", self.exit)

		# Create a drawing area, restricts the window size
		darea = gtk.DrawingArea()
		darea.set_size_request(windowWidth, windowHeight)
		darea.set_draw_func(self.draw)
		darea.set_cursor(gdk.Cursor.new_from_name("pointer"))
		click = gtk.GestureClick()
		click.connect("released", lambda *_a: self.exit())
		darea.add_controller(click)
		self.darea = darea
		self.set_child(darea)

		# Show window
		self.present()

		# Redraw on every line compare.py writes to our stdin (non-blocking)
		GLib.io_add_watch(sys.stdin, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, self.catch_stdin)

		# Start the main loop
		if run_main_loop:
			self.loop.run()

	def draw(self, widget, ctx, width, height):
		"""Draw the UI"""
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
		ctx.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
		ctx.show_text(self.message)

		# Draw the subtext if there is one
		if self.subtext:
			ctx.move_to(380, 210)
			if theme == "dark":
				ctx.set_source_rgba(230, 230, 230, .8)
			else:
				ctx.set_source_rgba(50, 50, 50, .85)
			ctx.set_font_size(40)
			ctx.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
			ctx.show_text(self.subtext)

	def handle_line(self, comm):
		"""Apply one protocol line from compare.py (M=message, S=subtext)."""
		if not comm:
			return
		if comm[0] == "M":
			self.message = comm[2:].strip()
		if comm[0] == "S":
			self.subtext = comm[2:].strip()

	def catch_stdin(self, source=None, condition=None):
		"""Catch input from stdin and redraw"""
		line = sys.stdin.readline()
		if not line:
			# EOF: compare.py went away, so should the overlay
			self.exit()
			return False
		self.handle_line(line.rstrip("\n"))
		self.darea.queue_draw()
		return True

	def exit(self, *args):
		"""Cleanly exit"""
		if self.loop is not None and self.loop.is_running():
			self.loop.quit()
		return True


# Make sure we quit on a SIGINT
signal.signal(signal.SIGINT, signal.SIG_DFL)

# Open the GTK window (module is executed by init.py for --start-auth-ui)
window = StickyWindow()
