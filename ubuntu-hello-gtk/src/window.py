# Opens and controls main ui window
import gi
import signal
import sys
import os
# Set secure umask for all created files/directories (0o077 ensures only owner has access)
os.umask(0o077)

import subprocess

from i18n import _
import i18n
import languages
import paths_factory
import preferences
from search_fuzzy import fuzzy_match, fuzzy_score

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
	def __init__(self, run_main_loop=True):
		"""Initialize the Settings window."""
		# Load the custom CSS theme stylesheet
		paths_factory.load_custom_css()

		# Make the class a GTK window
		gtk.Window.__init__(self)

		self.capture = None
		self._sorted_users = []
		self.active_user = ""
		self._language_combo_ready = False
		self._search_row_baselines = []
		self._rebuilding = False
		self._build_ui(initial=True)

		if run_main_loop:
			gtk.main()

	def _build_ui(self, initial=True, restore=None):
		"""Load Glade UI (also used for instant language rebuild)."""
		restore = restore or {}

		self.builder = gtk.Builder()
		self.builder.set_translation_domain("ubuntu-hello-gtk")
		self.builder.add_from_file(paths_factory.main_window_wireframe_path())
		self.builder.connect_signals(self)

		self.window = self.builder.get_object("mainwindow")
		self.userlist = self.builder.get_object("userlist")
		self.modellistbox = self.builder.get_object("modellistbox")
		self.opencvimage = self.builder.get_object("opencvimage")

		self.keyring_status_label = self.builder.get_object("keyring_status_label")
		self.keyring_enable_button = self.builder.get_object("keyring_enable_button")
		self.keyring_disable_button = self.builder.get_object("keyring_disable_button")

		self.version_label = self.builder.get_object("version_label")
		if self.version_label:
			self.version_label.set_text(self.get_display_version())

		self.notebook = self.builder.get_object("notebook")
		self.settings_search = self.builder.get_object("settings_search")
		self.language_combo = self.builder.get_object("language_combo")

		self.window.connect("destroy", self.exit)
		self.window.connect("delete_event", self.exit)

		# Create a treeview that will list the model data
		self.treeview = gtk.TreeView()
		self.treeview.set_vexpand(True)

		# Set the columns (Python _() follows reloaded catalog after language switch)
		for i, column in enumerate([i18n._("ID"), i18n._("Created"), i18n._("Label")]):
			col = gtk.TreeViewColumn(column, gtk.CellRendererText(), text=i)
			self.treeview.append_column(col)

		# Add the treeview
		self.modellistbox.add(self.treeview)

		self._populate_users(restore.get("active_user"))
		self._language_combo_ready = False
		self._setup_language_combo(restore.get("language"))
		self._search_row_baselines = []
		self._collect_search_rows()

		self.load_model_list()
		self.update_keyring_status()

		# Restore notebook / search / geometry after rebuild
		if restore.get("page") is not None and self.notebook is not None:
			page = restore["page"]
			if 0 <= page < self.notebook.get_n_pages():
				self.notebook.set_current_page(page)
		if restore.get("search") and self.settings_search is not None:
			self.settings_search.set_text(restore["search"])
		if restore.get("width") and restore.get("height"):
			try:
				self.window.resize(restore["width"], restore["height"])
			except Exception:
				pass

		self.window.show_all()
		# Re-apply fuzzy filter after show (haystacks use displayed labels)
		if self.settings_search is not None and (self.settings_search.get_text() or "").strip():
			self.on_settings_search_changed(self.settings_search)

	def _populate_users(self, preferred_user=None):
		"""Fill user combo; prefer preferred_user, else real user / first with models."""
		import pwd
		users = set()
		for u in pwd.getpwall():
			if (u.pw_uid == 0 or 1000 <= u.pw_uid < 60000) and u.pw_name != "nobody":
				if u.pw_shell not in ("/usr/sbin/nologin", "/bin/false", "/usr/bin/false", "/sbin/nologin"):
					users.add(u.pw_name)

		try:
			model_dir = paths_factory.user_models_dir_path()
			if os.path.exists(model_dir):
				for file in os.listdir(model_dir):
					if file.endswith(".dat"):
						users.add(file[:-4])
		except Exception:
			pass

		sorted_users = sorted(list(users))
		self._sorted_users = sorted_users
		self.active_user = ""
		self.userlist.items = 0
		self.userlist.remove_all()

		for user in sorted_users:
			self.userlist.append_text(user)
			self.userlist.items += 1

		default_user = ""
		if preferred_user and preferred_user in sorted_users:
			default_user = preferred_user
		else:
			real_user = get_real_user()
			if real_user in sorted_users:
				default_user = real_user
			else:
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

	def _setup_language_combo(self, preferred=None):
		"""Populate Language tab: Automatic (always first) + English + locales."""
		combo = self.language_combo
		if combo is None:
			return
		# Ignore changed signals while rebuilding the model so Automatic stays.
		self._language_combo_ready = False
		combo.remove_all()
		combo.append(preferences.AUTO, i18n._("Automatic"))
		ui_lang = preferred if preferred is not None else preferences.read_language()
		for code in languages.COMBO_CODES:
			combo.append(code, languages.language_combo_label(code, ui_lang))

		current = preferred if preferred is not None else preferences.read_language()
		if current != preferences.AUTO and not languages.is_known_language(current):
			current = preferences.AUTO
		if hasattr(combo, "set_active_id"):
			combo.set_active_id(current)
			if combo.get_active() < 0:
				combo.set_active_id(preferences.AUTO)
			if combo.get_active() < 0:
				combo.set_active(0)
		else:
			combo.set_active(0)
		self._language_combo_ready = True

	def _session_snapshot(self):
		"""Capture UI state to restore across language rebuild."""
		snap = {
			"active_user": self.active_user,
			"language": preferences.read_language(),
			"page": self.notebook.get_current_page() if self.notebook else 0,
			"search": self.settings_search.get_text() if self.settings_search else "",
			"width": None,
			"height": None,
		}
		try:
			w, h = self.window.get_size()
			snap["width"], snap["height"] = w, h
		except Exception:
			pass
		if self.language_combo is not None and hasattr(self.language_combo, "get_active_id"):
			aid = self.language_combo.get_active_id()
			if aid:
				snap["language"] = aid
		return snap

	def _apply_language_rebuild(self, code):
		"""Write preference, reload gettext, rebuild Settings UI in-process (no restart)."""
		if self._rebuilding:
			return
		self._rebuilding = True
		try:
			preferences.write_language(code)
		except OSError as exc:
			print(f"Could not save language preference: {exc}", file=sys.stderr)
			self._rebuilding = False
			return

		snap = self._session_snapshot()
		snap["language"] = code

		# Release camera before tearing down video tab widgets
		if self.capture is not None:
			try:
				self.capture.release()
			except Exception:
				pass
			self.capture = None

		old_window = self.window
		# Prevent destroy handler from quitting during rebuild
		try:
			old_window.disconnect_by_func(self.exit)
		except Exception:
			pass

		i18n.reload_from_preferences()

		try:
			old_window.destroy()
		except Exception:
			pass

		self._build_ui(initial=False, restore=snap)
		self._rebuilding = False

	def on_language_changed(self, combo):
		"""Persist language and instantly rebuild Settings UI."""
		if not self._language_combo_ready or self._rebuilding:
			return
		code = None
		if hasattr(combo, "get_active_id"):
			code = combo.get_active_id()
		if not code:
			idx = combo.get_active()
			if idx == 0:
				code = preferences.AUTO
			elif idx > 0 and idx <= len(languages.COMBO_CODES):
				code = languages.COMBO_CODES[idx - 1]
			else:
				code = preferences.AUTO
		if code != preferences.AUTO and not languages.is_known_language(code):
			code = preferences.AUTO
		# Skip no-op (same language)
		if code == preferences.read_language() and not self._rebuilding:
			# Still ensure preference file exists for auto
			try:
				preferences.write_language(code)
			except OSError:
				pass
			return
		self._apply_language_rebuild(code)

	def _widget_display_text(self, widget):
		"""Collect currently displayed (translated) text from a widget subtree."""
		parts = []

		def walk(node):
			if node is None:
				return
			try:
				if isinstance(node, gtk.Label):
					text = node.get_text() or ""
					if not text and hasattr(node, "get_label"):
						raw = node.get_label() or ""
						if "<" in raw:
							try:
								from gi.repository import Pango
								_, text, _ = Pango.parse_markup(raw, -1, "\0")
							except Exception:
								text = raw
						else:
							text = raw
					if text:
						parts.append(text)
				elif isinstance(node, gtk.Button):
					label = node.get_label()
					if label:
						parts.append(label)
					child = node.get_child()
					if child is not None:
						walk(child)
					return
				elif isinstance(node, gtk.Entry):
					pass
				elif isinstance(node, gtk.ComboBoxText):
					# Include all entries (Automatic + locales), not only the active one,
					# so search still finds Language after the selection changes.
					model = node.get_model()
					if model is not None:
						for i in range(len(model)):
							try:
								parts.append(str(model[i][0]))
							except Exception:
								pass
					active = node.get_active_text()
					if active:
						parts.append(active)
			except Exception:
				pass
			if isinstance(node, gtk.Container):
				try:
					for child in node.get_children():
						walk(child)
				except Exception:
					pass

		walk(widget)
		return " ".join(parts)

	def _collect_search_rows(self):
		"""Remember filterable rows under each notebook tab (Models/Video/Keyring/Language/About)."""
		self._search_row_baselines = []
		notebook = self.notebook
		if notebook is None:
			return
		# Whole-page tabs: a match shows every child (About/Language/Video).
		# Video must be atomic — the preview EventBox has no label text, so
		# row-level filtering would hide the camera UI while the device still opens.
		atomic_pages = set()
		for obj_id in ("box5", "language_page", "box2"):
			obj = self.builder.get_object(obj_id)
			if obj is not None:
				atomic_pages.add(obj)

		n_pages = notebook.get_n_pages()
		for page_index in range(n_pages):
			page = notebook.get_nth_page(page_index)
			if page is None:
				continue
			candidates = []
			if isinstance(page, gtk.Box):
				candidates = list(page.get_children())
			elif isinstance(page, gtk.Container):
				candidates = list(page.get_children())
			else:
				candidates = [page]

			expanded = []
			for child in candidates:
				if isinstance(child, gtk.Box) and child.get_orientation() == gtk.Orientation.VERTICAL:
					expanded.extend(child.get_children())
				else:
					expanded.append(child)
			if not expanded:
				expanded = candidates

			rows = list(expanded)
			tab_label = notebook.get_tab_label(page)
			self._search_row_baselines.append({
				"page_index": page_index,
				"page": page,
				"tab_label": tab_label,
				"rows": rows,
				"atomic": page in atomic_pages,
			})

	def on_settings_search_changed(self, entry):
		"""Fuzzy-filter Settings rows by currently displayed translated label text."""
		query = (entry.get_text() or "").strip()
		best_tab = None
		best_score = -1.0

		for page_info in self._search_row_baselines:
			page_index = page_info["page_index"]
			tab_text = self._widget_display_text(page_info["tab_label"])
			rows = page_info["rows"]
			atomic = page_info.get("atomic", False)

			if not query:
				for row in rows:
					row.set_visible(True)
				page_info["page"].set_visible(True)
				continue

			page_best = fuzzy_score(query, tab_text)
			page_match = fuzzy_match(query, tab_text)

			if atomic:
				for row in rows:
					text = self._widget_display_text(row)
					if fuzzy_match(query, text):
						page_match = True
						page_best = max(page_best, fuzzy_score(query, text))
				for row in rows:
					row.set_visible(page_match)
			else:
				any_row = False
				for row in rows:
					text = self._widget_display_text(row)
					score = max(fuzzy_score(query, text), fuzzy_score(query, tab_text))
					match = fuzzy_match(query, text) or fuzzy_match(query, tab_text)
					row.set_visible(match)
					if match:
						any_row = True
						page_best = max(page_best, score)
				page_match = any_row or page_match

			page_info["page"].set_visible(True)

			if page_match and page_best > best_score:
				best_score = page_best
				best_tab = page_index

		if query and best_tab is not None and self.notebook is not None:
			if self.notebook.get_current_page() != best_tab:
				self.notebook.set_current_page(best_tab)

	def reveal_search_page(self, page_index):
		"""Force-show all widgets on a notebook page (undo stale search hides).

		Row-level fuzzy search can leave Video/Models children invisible after the
		user clicks another tab; camera startup must not run against a blank page.
		"""
		if self.notebook is None or page_index is None:
			return
		page = self.notebook.get_nth_page(page_index)
		if page is None:
			return

		def show_tree(widget):
			try:
				widget.set_visible(True)
			except Exception:
				return
			if isinstance(widget, gtk.Container):
				try:
					for child in widget.get_children():
						show_tree(child)
				except Exception:
					pass

		show_tree(page)

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
		if self._rebuilding:
			return True
		if self.capture is not None:
			self.capture.release()

		gtk.main_quit()
		sys.exit(0)

	def get_display_version(self):
		"""Return UI version from VERSION / paths (never older git tags)."""
		from version_display import get_display_version as _display_version

		return _display_version(os.path.dirname(os.path.abspath(__file__)))


# Make sure we quit on a SIGINT
signal.signal(signal.SIGINT, signal.SIG_DFL)

def elevate():
	"""Elevate privileges to root using pkexec or sudo"""
	if os.geteuid() == 0 or os.environ.get("BYPASS_ELEVATE") == "1":
		return
	try:
		extra_args = []
		# Forward display + locale so Automatic language and theme keep working
		# after polkit elevation on all supported DEs (GNOME/KDE/XFCE/…).
		for var in [
			"DISPLAY",
			"WAYLAND_DISPLAY",
			"XDG_RUNTIME_DIR",
			"XAUTHORITY",
			"DBUS_SESSION_BUS_ADDRESS",
			"XDG_CURRENT_DESKTOP",
			"DESKTOP_SESSION",
			"XDG_CONFIG_HOME",
			"LANG",
			"LANGUAGE",
			"LC_ALL",
			"LC_MESSAGES",
			"LC_CTYPE",
		]:
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
	import theme_detect
	user = get_real_user()
	if not user or user == "root":
		return "light"
	return theme_detect.get_theme_preference(user=user, default="light")


def get_user_animations_preference():
	user = get_real_user()
	if not user or user == "root":
		return True

	import subprocess
	try:
		cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "gsettings", "get", "org.gnome.desktop.interface", "enable-animations"]
		val = subprocess.check_output(cmd, text=True).strip()
		return val.lower() == "true"
	except Exception:
		pass

	try:
		cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "dconf", "read", "/org/gnome/desktop/interface/enable-animations"]
		val = subprocess.check_output(cmd, text=True).strip()
		return val.lower() == "true"
	except Exception:
		pass

	return True


def setup_theme():
	try:
		if os.geteuid() == 0:
			prefer_dark = (get_user_theme_preference() == "dark")
			enable_animations = get_user_animations_preference()
			gtk_settings = gtk.Settings.get_default()
			if gtk_settings:
				gtk_settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)
				gtk_settings.set_property("gtk-enable-animations", enable_animations)
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

					enable_animations = True
					try:
						enable_animations = settings.get_boolean("enable-animations")
					except Exception:
						pass

					gtk_settings = gtk.Settings.get_default()
					if gtk_settings:
						gtk_settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)
						gtk_settings.set_property("gtk-enable-animations", enable_animations)
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
