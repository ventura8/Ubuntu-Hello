# Opens and controls main ui window
import gi
import signal
import sys
import os
import subprocess

from i18n import _
import paths_factory

# Restore GUI environment variables passed from the parent process
env_prefix = "--env-"
for arg in list(sys.argv):
	if arg.startswith(env_prefix):
		parts = arg[len(env_prefix):].split("=", 1)
		if len(parts) == 2:
			key, val = parts
			os.environ[key] = val
		sys.argv.remove(arg)

# Make sure we have the libs we need
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

# Import them
from gi.repository import Gtk as gtk
from gi.repository import Gio


class MainWindow(gtk.Window):
	def __init__(self):
		"""Initialize the sticky window"""
		# Make the class a GTK window
		gtk.Window.__init__(self)

		self.builder = gtk.Builder()
		self.builder.add_from_file(paths_factory.main_window_wireframe_path())
		self.builder.connect_signals(self)

		self.window = self.builder.get_object("mainwindow")
		self.userlist = self.builder.get_object("userlist")
		self.modellistbox = self.builder.get_object("modellistbox")
		self.opencvimage = self.builder.get_object("opencvimage")

		self.keyring_status_label = self.builder.get_object("keyring_status_label")
		self.keyring_enable_button = self.builder.get_object("keyring_enable_button")
		self.keyring_disable_button = self.builder.get_object("keyring_disable_button")

		self.window.connect("destroy", self.exit)
		self.window.connect("delete_event", self.exit)

		# Init capture for video tab
		self.capture = None

		# Create a treeview that will list the model data
		self.treeview = gtk.TreeView()
		self.treeview.set_vexpand(True)

		# Set the columns
		for i, column in enumerate([_("ID"), _("Created"), _("Label")]):
			col = gtk.TreeViewColumn(column, gtk.CellRendererText(), text=i)
			self.treeview.append_column(col)

		# Add the treeview
		self.modellistbox.add(self.treeview)

		# Get all potential system users from Ubuntu
		import pwd
		users = set()
		for u in pwd.getpwall():
			if (u.pw_uid == 0 or 1000 <= u.pw_uid < 60000) and u.pw_name != "nobody":
				if u.pw_shell not in ("/usr/sbin/nologin", "/bin/false", "/usr/bin/false", "/sbin/nologin"):
					users.add(u.pw_name)

		# Add any users who already have models saved, in case they are not in the list above
		try:
			model_dir = paths_factory.user_models_dir_path()
			if os.path.exists(model_dir):
				for file in os.listdir(model_dir):
					if file.endswith(".dat"):
						users.add(file[:-4])
		except Exception:
			pass

		sorted_users = sorted(list(users))
		self.active_user = ""
		self.userlist.items = 0

		for user in sorted_users:
			self.userlist.append_text(user)
			self.userlist.items += 1

		# Select the logged-in user as active by default if they are in the list.
		# Otherwise, choose the first user who has a saved model.
		# If none have models, choose the first user in the sorted list.
		real_user = get_real_user()
		default_user = ""
		if real_user in sorted_users:
			default_user = real_user
		else:
			# Fallback to the first user with a model
			users_with_models = []
			try:
				model_dir = paths_factory.user_models_dir_path()
				if os.path.exists(model_dir):
					for file in os.listdir(model_dir):
						if file.endswith(".dat") and file[:-4] in sorted_users:
							users_with_models.append(file[:-4])
			except Exception:
				pass

			if users_with_models:
				default_user = sorted(users_with_models)[0]
			elif sorted_users:
				default_user = sorted_users[0]

		if default_user:
			self.active_user = default_user
			self.userlist.set_active(sorted_users.index(default_user))
		else:
			self.userlist.set_active(-1)

		self.load_model_list()
		self.update_keyring_status()

		self.window.show_all()

		# Start GTK main loop
		gtk.main()

	def load_model_list(self):
		"""(Re)load the model list"""

		# Get username and default to none if there are no models at all yet
		user = 'none'
		if self.active_user: user = self.active_user

		# Execute the list command to get the models
		res = subprocess.run(["ubuntu-hello", "list", "--plain", "-U", user], capture_output=True, text=True)
		status = res.returncode
		output = res.stdout + res.stderr

		# Create a datamodel
		self.listmodel = gtk.ListStore(str, str, str)

		# If there was no error
		if status == 0:
			# Split the output per line
			lines = output.split("\n")

			# Add the models to the datamodel
			for i in range(len(lines)):
				items = lines[i].split(",")
				if len(items) < 3: continue
				self.listmodel.append(items)

		self.treeview.set_model(self.listmodel)

	def on_about_link(self, label, uri):
		"""Open links on about page as a non-root user"""
		try:
			user = os.getlogin()
		except Exception:
			user = os.environ.get("SUDO_USER")

		import re
		if user and re.match(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$", user):
			subprocess.run(["sudo", "-u", user, "timeout", "10", "xdg-open", uri], capture_output=True)
		return True

	def exit(self, widget=None, context=None):
		"""Cleanly exit"""
		if self.capture is not None:
			self.capture.release()

		gtk.main_quit()
		sys.exit(0)


# Make sure we quit on a SIGINT
signal.signal(signal.SIGINT, signal.SIG_DFL)

def elevate():
	"""Elevate privileges to root using pkexec or sudo"""
	if os.geteuid() == 0 or os.environ.get("BYPASS_ELEVATE") == "1":
		return
	try:
		extra_args = []
		for var in ["DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS"]:
			val = os.environ.get(var)
			if val:
				extra_args.append(f"--env-{var}={val}")
		args = ["pkexec", sys.executable] + sys.argv + extra_args
		os.execvp("pkexec", args)
	except Exception:
		args = ["sudo", sys.executable] + sys.argv
		os.execvp("sudo", args)


# Make sure we run as sudo
elevate()


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


def get_user_theme_preference():
	user = get_real_user()
	if not user or user == "root":
		return "light"

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


def setup_theme():
	try:
		if os.geteuid() == 0:
			prefer_dark = (get_user_theme_preference() == "dark")
			gtk_settings = gtk.Settings.get_default()
			if gtk_settings:
				gtk_settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)
		else:
			# Check if the schema exists
			schemas = Gio.SettingsSchemaSource.get_default().list_schemas(True)
			all_schemas = schemas[0] + schemas[1]
			if "org.gnome.desktop.interface" not in all_schemas:
				return

			settings = Gio.Settings.new("org.gnome.desktop.interface")

			def update_theme(settings, key=None):
				try:
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

					gtk_settings = gtk.Settings.get_default()
					if gtk_settings:
						gtk_settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)
				except Exception as e:
					print(f"Error updating theme: {e}", file=sys.stderr)

			settings.connect("changed", update_theme)
			update_theme(settings)
	except Exception as e:
		print(f"Error setting up theme tracking: {e}", file=sys.stderr)


# Setup theme tracking to follow system dark/light theme
setup_theme()

# If no models have been created yet or when it is forced, start the onboarding
model_dir = paths_factory.user_models_dir_path()
if "--force-onboarding" in sys.argv or not os.path.exists(model_dir) or not os.listdir(model_dir):
	import onboarding
	ob = onboarding.OnboardingWindow()
	if not getattr(ob, "completed", False):
		sys.exit(0)

# Class is split so it isn't too long, import split functions
import tab_models
MainWindow.on_user_add = tab_models.on_user_add
MainWindow.on_user_change = tab_models.on_user_change
MainWindow.on_model_add = tab_models.on_model_add
MainWindow.on_model_delete = tab_models.on_model_delete
import tab_video
MainWindow.on_page_switch = tab_video.on_page_switch
MainWindow.capture_frame = tab_video.capture_frame
MainWindow.on_camera_change = tab_video.on_camera_change
import tab_keyring
MainWindow.update_keyring_status = tab_keyring.update_keyring_status
MainWindow.on_keyring_enable = tab_keyring.on_keyring_enable
MainWindow.on_keyring_disable = tab_keyring.on_keyring_disable

# Open the GTK window
window = MainWindow()
