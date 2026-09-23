# Opens and controls main ui window
import gi
import signal
import sys
import os
# Set secure umask for all created files/directories (0o077 ensures only owner has access)
os.umask(0o077)

import subprocess
import threading

from i18n import _
import i18n
import languages
import paths_factory
import preferences
from search_fuzzy import fuzzy_match, fuzzy_score

# Display + locale variables elevate() forwards across pkexec so Automatic
# language and theme keep working as root on all supported DEs. This is the
# ONLY set the elevated process will accept back from argv: polkit pins just
# python + the script path, so any extra `--env-*` argument is caller-controlled
# and must never be able to set PATH / LD_* / PYTHON* in a root process.
SESSION_ENV_ALLOWLIST = (
	"DISPLAY",
	"WAYLAND_DISPLAY",
	"XDG_RUNTIME_DIR",
	"XAUTHORITY",
	"DBUS_SESSION_BUS_ADDRESS",
	"XDG_CURRENT_DESKTOP",
	"DESKTOP_SESSION",
	"XDG_CONFIG_HOME",
	"XDG_DATA_HOME",
	"XDG_DATA_DIRS",   # snap/flatpak .desktop dirs: without it xdg-open picks the wrong browser
	"LANG",
	"LANGUAGE",
	"LC_ALL",
	"LC_MESSAGES",
	"LC_CTYPE",
)

# Fixed search path for the root process: every helper we spawn by bare name
# (ubuntu-hello, sudo, gsettings, loginctl, …) resolves from here, never from
# anything inherited or injected.
ROOT_SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Restore GUI environment variables passed from the parent process
env_prefix = "--env-"
_remaining_argv = []
for arg in sys.argv:
	if not arg.startswith(env_prefix):
		_remaining_argv.append(arg)
		continue
	parts = arg[len(env_prefix):].split("=", 1)
	if len(parts) == 2:
		key, val = parts
		if key in SESSION_ENV_ALLOWLIST:
			os.environ[key] = val
		else:
			print(f"ubuntu-hello-gtk: ignoring non-allowlisted --env-{key}", file=sys.stderr)
sys.argv[:] = _remaining_argv
if os.geteuid() == 0:
	os.environ["PATH"] = ROOT_SAFE_PATH

# Make sure we have the libs we need
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

# Import them
from gi.repository import Gtk as gtk
from gi.repository import Gio
from gi.repository import GLib
import gtk4compat


_SCHEMA_GNOME_IFACE = "org.gnome.desktop.interface"


_NOLOGIN_SHELLS = ("/usr/sbin/nologin", "/bin/false", "/usr/bin/false", "/sbin/nologin")


def _login_user_names():
	"""Real login accounts: root plus regular UIDs that have a usable shell."""
	import pwd
	names = set()
	for u in pwd.getpwall():
		if u.pw_name == "nobody":
			continue
		if not (u.pw_uid == 0 or 1000 <= u.pw_uid < 60000):
			continue
		if u.pw_shell in _NOLOGIN_SHELLS:
			continue
		names.add(u.pw_name)
	return names


def _enrolled_user_names():
	"""Users that have a face model on disk."""
	try:
		model_dir = paths_factory.user_models_dir_path()
		if not os.path.exists(model_dir):
			return []
		return [f[:-4] for f in os.listdir(model_dir) if f.endswith(".dat")]
	except Exception:
		return []


def _filterable_rows(page):
	"""Rows of a notebook page, unwrapping a single vertical Box wrapper."""
	candidates = list(gtk4compat.iter_children(page)) or [page]

	expanded = []
	for child in candidates:
		if isinstance(child, gtk.Box) and child.get_orientation() == gtk.Orientation.VERTICAL:
			expanded.extend(gtk4compat.iter_children(child))
		else:
			expanded.append(child)

	return list(expanded) if expanded else list(candidates)


def _node_own_texts(node):
	"""Text a single node contributes, ignoring its children.

	gtk.Entry holds user-typed text, which is never a translatable string, so
	it contributes nothing.
	"""
	if isinstance(node, gtk.Label):
		text = _label_display_text(node)
		return [text] if text else []
	if isinstance(node, gtk.DropDown):
		return _dropdown_display_texts(node)
	return []


def _walk_children(node, visit):
	"""Visit every child except popovers and separate toplevels."""
	try:
		for child in gtk4compat.iter_children(node):
			if not isinstance(child, (gtk.Popover, gtk.Native)):
				visit(child)
	except Exception:
		pass


def _label_display_text(node):
	"""Text a Label actually shows, resolving Pango markup when present."""
	text = node.get_text() or ""
	if text or not hasattr(node, "get_label"):
		return text

	raw = node.get_label() or ""
	if "<" not in raw:
		return raw
	try:
		from gi.repository import Pango
		# (ok, AttrList, text, accel_char) -- index 1 is the AttrList, and
		# returning it lets a non-str reach " ".join() in _widget_display_text.
		return Pango.parse_markup(raw, -1, "\0")[2] or ""
	except Exception:
		return raw


def _dropdown_display_texts(node):
	"""Every entry of a DropDown, plus its selection.

	All entries (Automatic + locales), not only the active one, so search still
	finds Language after the selection changes.
	"""
	texts = []
	model = node.get_model()
	if model is not None:
		for i in range(model.get_n_items()):
			try:
				texts.append(str(model.get_string(i)))
			except Exception:
				pass
	selected = node.get_selected_item()
	if selected is not None:
		texts.append(selected.get_string())
	return texts



class MainWindow(gtk.Window):
	def __init__(self, run_main_loop=True):
		"""Initialize the Settings window."""
		# Load the custom CSS theme stylesheet
		paths_factory.load_custom_css()

		# Make the class a GTK window
		gtk.Window.__init__(self)

		self.run_main_loop = run_main_loop
		self.capture = None
		self._sorted_users = []
		self.active_user = ""
		self._language_combo_ready = False
		self._search_row_baselines = []
		self._rebuilding = False
		self._build_ui(initial=True)

		if run_main_loop:
			gtk4compat.run_main()

	def _adopt_toplevel(self, built_window, initial):
		"""Take the freshly built toplevel, or transplant its content into ours.

		Language rebuild: keep the ONE toplevel and move the new content into
		it. Destroying and recreating the toplevel while popups/stacking state
		still referenced it crashed mutter 50 (meta_window_set_stack_position
		assertions, then SIGSEGV).
		"""
		if initial or getattr(self, "window", None) is None:
			self.window = built_window
			return

		content = built_window.get_child()
		header = built_window.get_titlebar()
		built_window.set_child(None)
		built_window.set_titlebar(None)
		self.window.set_titlebar(header)
		self.window.set_child(content)
		self.window.set_title(built_window.get_title() or "")
		built_window.destroy()   # never realized, never shown

	def _restore_window_state(self, restore):
		"""Put back the notebook page, search text and geometry after a rebuild."""
		page = restore.get("page")
		if page is not None and self.notebook is not None and 0 <= page < self.notebook.get_n_pages():
			self.notebook.set_current_page(page)

		if restore.get("search") and self.settings_search is not None:
			self.settings_search.set_text(restore["search"])

		if restore.get("width") and restore.get("height"):
			try:
				self.window.set_default_size(restore["width"], restore["height"])
				return
			except Exception:
				pass
		# GTK 4 windows grow to their content's natural size; pin a sane default.
		self.window.set_default_size(900, 620)

	def _build_ui(self, initial=True, restore=None):
		"""Load Glade UI (also used for instant language rebuild)."""
		restore = restore or {}

		# Right-to-left languages mirror the whole window (must precede widget creation).
		rtl = gtk4compat.apply_text_direction(effective_ui_language())
		self.builder = gtk4compat.builder(self, paths_factory.main_window_wireframe_path(), "ubuntu-hello-gtk")

		self._adopt_toplevel(self.builder.get_object("mainwindow"), initial)
		self.userlist = gtk4compat.dropdown(self.builder.get_object("userlist"), self.on_user_change)
		self.modellistbox = self.builder.get_object("modellistbox")
		# Reminder shown while exactly one model is enrolled: a single model
		# recorded in one kind of light fails intermittently in another (see
		# onboarding.prepare_second_scan), so nudge towards a second one.
		self.single_model_infobar = self.builder.get_object("single_model_infobar")
		self.opencvimage = self.builder.get_object("opencvimage")
		paths_factory.set_picture_file(self.builder.get_object("image1"), paths_factory.about_logo_path())

		self.keyring_status_label = self.builder.get_object("keyring_status_label")
		self.keyring_enable_button = self.builder.get_object("keyring_enable_button")
		self.keyring_disable_button = self.builder.get_object("keyring_disable_button")

		self.version_label = self.builder.get_object("version_label")
		if self.version_label:
			self.version_label.set_text(self.get_display_version())

		self.notebook = self.builder.get_object("notebook")
		# GtkNotebook's tab position is literal (LEFT stays left in RTL): keep the
		# sidebar on the reading-start side.
		if self.notebook is not None:
			self.notebook.set_tab_pos(gtk.PositionType.RIGHT if rtl else gtk.PositionType.LEFT)
		self.settings_search = self.builder.get_object("settings_search")
		if self.settings_search is not None and initial:
			shortcuts = gtk.ShortcutController()
			shortcuts.set_scope(gtk.ShortcutScope.GLOBAL)
			shortcuts.add_shortcut(gtk.Shortcut.new(gtk.ShortcutTrigger.parse_string("<Control>f"),
				gtk.CallbackAction.new(self.focus_settings_search)))
			self.window.add_controller(shortcuts)
		self.language_combo = gtk4compat.dropdown(self.builder.get_object("language_combo"), self.on_language_changed)
		self.cameraselect = gtk4compat.dropdown(self.builder.get_object("cameraselect"), self.on_camera_change)

		if initial:
			self.window.set_icon_name("ubuntu-hello-gtk")
			self.window.connect("close-request", self.exit)

		# Model table (Python _() follows reloaded catalog after language switch)
		self.models = gtk4compat.ColumnList([i18n._("ID"), i18n._("Created"), i18n._("Label")])
		self.listmodel = self.models
		scroller = gtk.ScrolledWindow()
		scroller.set_policy(gtk.PolicyType.NEVER, gtk.PolicyType.AUTOMATIC)
		scroller.set_has_frame(True)
		scroller.set_vexpand(True)
		scroller.set_min_content_height(160)
		scroller.set_child(self.models.widget)
		self.modellistbox.append(scroller)

		self._populate_users(restore.get("active_user"))
		self._language_combo_ready = False
		self._setup_language_combo(restore.get("language"))
		self._search_row_baselines = []
		self._collect_search_rows()

		self.load_model_list()
		self.update_keyring_status()
		self.load_notification_settings()
		self.load_security_settings()

		self._restore_window_state(restore)

		self.window.present()
		# Re-apply fuzzy filter after show (haystacks use displayed labels)
		if self.settings_search is not None and (self.settings_search.get_text() or "").strip():
			self.on_settings_search_changed(self.settings_search)

	def _default_user(self, preferred_user, sorted_users):
		"""Preferred user if it exists, else the real user, else first enrolled."""
		if preferred_user and preferred_user in sorted_users:
			return preferred_user

		real_user = get_real_user()
		if real_user in sorted_users:
			return real_user

		with_models = [u for u in _enrolled_user_names() if u in sorted_users]
		if with_models:
			return min(with_models)
		return sorted_users[0] if sorted_users else ""

	def _populate_users(self, preferred_user=None):
		"""Fill user combo; prefer preferred_user, else real user / first with models."""
		users = _login_user_names()
		users.update(_enrolled_user_names())

		sorted_users = sorted(users)
		self._sorted_users = sorted_users
		self.active_user = ""
		self.userlist.items = 0
		self.userlist.remove_all()

		for user in sorted_users:
			self.userlist.append_text(user)
			self.userlist.items += 1

		default_user = self._default_user(preferred_user, sorted_users)
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
			snap["width"], snap["height"] = self.window.get_width(), self.window.get_height()
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

		i18n.reload_from_preferences()

		# Same toplevel, new content (see _build_ui): no window destroy/recreate.
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
		# We are inside the DropDown's notify::selected handler while its list
		# popover still holds the pointer grab. Destroying the window from here
		# leaves that grab dangling and the whole app unresponsive ("Tried to
		# map a grabbing popup with a non-top most parent"). Close the popover,
		# let it unmap, then rebuild from the main loop.
		self._rebuilding = True
		self.settle_page_focus()
		GLib.timeout_add(200, self._deferred_language_rebuild, code)

	def _deferred_language_rebuild(self, code):
		self._rebuilding = False
		try:
			self.settle_page_focus()
			self._apply_language_rebuild(code)
		except Exception as exc:
			# Never leave the window stuck in "rebuilding" (dead handlers).
			print(f"Language rebuild failed: {exc}", file=sys.stderr)
			self._rebuilding = False
		return False

	def _widget_display_text(self, widget):
		"""Collect currently displayed (translated) text from a widget subtree."""
		parts = []

		def walk(node):
			if node is None:
				return
			# A collapsed InfoBar (e.g. the single-model reminder) is not on
			# screen: its text must not attract the fuzzy search.
			if isinstance(node, gtk.Revealer) and not node.get_reveal_child():
				return
			try:
				if isinstance(node, gtk.Button):
					label = node.get_label()
					if label:
						parts.append(label)
					walk(node.get_child())
					return
				parts.extend(_node_own_texts(node))
			except Exception:
				pass
			_walk_children(node, walk)

		walk(widget)
		return " ".join(parts)

	def _atomic_pages(self):
		"""Pages where a match reveals every child (About/Language/Video).

		Video must be atomic -- the preview EventBox has no label text, so
		row-level filtering would hide the camera UI while the device still opens.
		"""
		pages = set()
		for obj_id in ("box5", "language_page", "box2"):
			obj = self.builder.get_object(obj_id)
			if obj is not None:
				pages.add(obj)
		return pages

	def _collect_search_rows(self):
		"""Remember filterable rows under each notebook tab (Models/Video/Keyring/Language/About)."""
		self._search_row_baselines = []
		notebook = self.notebook
		if notebook is None:
			return

		atomic_pages = self._atomic_pages()
		for page_index in range(notebook.get_n_pages()):
			page = notebook.get_nth_page(page_index)
			if page is None:
				continue

			self._search_row_baselines.append({
				"page_index": page_index,
				"page": page,
				"tab_label": notebook.get_tab_label(page),
				"rows": _filterable_rows(page),
				"atomic": page in atomic_pages,
			})

	def on_settings_search_stop(self, entry):
		"""Escape in the search field clears the query (restores every page)."""
		if entry.get_text():
			entry.set_text("")
		return True

	def focus_settings_search(self, *_args):
		"""Ctrl+F: jump to the search field."""
		if self.settings_search is not None:
			self.settings_search.grab_focus()
		return True

	def _filter_page_atomic(self, query, rows, tab_text):
		"""All-or-nothing page: any matching row reveals every row."""
		page_match = fuzzy_match(query, tab_text)
		page_best = fuzzy_score(query, tab_text)

		for row in rows:
			text = self._widget_display_text(row)
			if fuzzy_match(query, text):
				page_match = True
				page_best = max(page_best, fuzzy_score(query, text))

		for row in rows:
			row.set_visible(page_match)
		return page_match, page_best

	def _filter_page_rows(self, query, rows, tab_text):
		"""Per-row filtering: each row is shown only if it matches."""
		page_match = fuzzy_match(query, tab_text)
		page_best = fuzzy_score(query, tab_text)
		any_row = False

		for row in rows:
			text = self._widget_display_text(row)
			match = fuzzy_match(query, text) or fuzzy_match(query, tab_text)
			row.set_visible(match)
			if match:
				any_row = True
				page_best = max(page_best,
								fuzzy_score(query, text), fuzzy_score(query, tab_text))

		return any_row or page_match, page_best

	def on_settings_search_changed(self, entry):
		"""Fuzzy-filter Settings rows by currently displayed translated label text."""
		query = (entry.get_text() or "").strip()
		matches = []

		for page_info in self._search_row_baselines:
			tab_text = self._widget_display_text(page_info["tab_label"])
			rows = page_info["rows"]

			if not query:
				for row in rows:
					row.set_visible(True)
				page_info["page"].set_visible(True)
				continue

			if page_info.get("atomic", False):
				page_match, page_best = self._filter_page_atomic(query, rows, tab_text)
			else:
				page_match, page_best = self._filter_page_rows(query, rows, tab_text)

			page_info["page"].set_visible(True)
			if page_match:
				matches.append((page_best, fuzzy_match(query, tab_text), page_info["page_index"]))

		if not query or not matches or self.notebook is None:
			return

		# A hit on the tab label itself outranks an equal-score hit buried in a
		# page's body text (subsequence matching is generous on long
		# descriptions: "kyrng" also matches a paragraph on another page). On a
		# full tie max() keeps the earliest page, as the previous running-best
		# comparison did.
		best_tab = max(matches, key=lambda m: (m[0], m[1]))[2]
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
			# A DropDown's list is a Gtk.Popover child: setting it visible MAPS
			# a grabbing popup. On Wayland that fails when our window is not
			# the topmost surface (e.g. right after the language rebuild) and
			# leaves the whole app unresponsive; on any backend it is the
			# "dropdown opens by itself when switching tabs" bug.
			if isinstance(widget, (gtk.Popover, gtk.Native)):
				return
			try:
				widget.set_visible(True)
			except Exception:
				return
			try:
				for child in gtk4compat.iter_children(widget):
					show_tree(child)
			except Exception:
				pass

		show_tree(page)

	def load_model_list(self):
		"""(Re)load the model list"""

		# Get username and default to none if there are no models at all yet
		user = 'none'
		if self.active_user: user = self.active_user
		status = 1
		output = ""
		try:
			# Execute the list command to get the models
			res = subprocess.run(["ubuntu-hello", "list", "--plain", "-U", user], capture_output=True, text=True)
			status = res.returncode
			output = res.stdout + res.stderr
		except (OSError, subprocess.SubprocessError) as e:
			print(f"Error executing ubuntu-hello list: {e}", file=sys.stderr)

		self.models.clear()

		# If there was no error
		if status == 0:
			# Split the output per line
			lines = output.split("\n")

			# Add the models to the table
			for i in range(len(lines)):
				items = lines[i].split(",")
				if len(items) < 3: continue
				self.models.append(items)

		self.update_single_model_reminder()

	def update_single_model_reminder(self):
		"""Reveal the second-model reminder only while exactly one model exists."""
		if not getattr(self, "single_model_infobar", None):
			return
		count = len(self.models) if getattr(self, "models", None) is not None else 0
		self.single_model_infobar.set_reveal_child(count == 1)

	def settle_page_focus(self):
		"""After a notebook page switch: no widget focused, no dropdown list popped open."""
		for combo in (getattr(self, "userlist", None), getattr(self, "cameraselect", None), getattr(self, "language_combo", None)):
			widget = getattr(combo, "widget", None)
			if widget is not None:
				gtk4compat.close_popover(widget)
		try:
			self.window.set_focus(None)
		except Exception:
			pass
		return False

	def on_single_model_button_clicked(self, button):
		self.on_model_add(None)

	def on_about_link(self, label, uri):
		"""Open About-page links in the *user's* browser (we run as root under pkexec).

		xdg-open needs the user's session (display, Wayland socket, session
		bus) to reach their default browser; without it the browser is either
		not found or launched without the URL. Forward exactly those variables
		(pkexec already handed them to us) and never block the UI on it.
		"""
		import re
		user = get_real_user()
		if not user or user == "root" or not re.match(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$", user):
			return True
		if not re.match(r"^https?://", uri or ""):
			return True
		session_env = [f"{var}={os.environ[var]}" for var in (
			"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
			"XDG_CURRENT_DESKTOP", "XDG_DATA_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XAUTHORITY") if os.environ.get(var)]
		threading.Thread(target=open_uri_as_user, args=(user, uri, session_env), daemon=True).start()
		return True

	def exit(self, widget=None, context=None):
		"""Cleanly exit"""
		if getattr(self, "_rebuilding", False):
			return True
		if getattr(self, "capture", None) is not None:
			try:
				self.capture.release()
			except Exception:
				pass
		if getattr(self, "run_main_loop", True):
			try:
				gtk4compat.quit_main()
			except RuntimeError:
				pass
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
		original_locale = {}
		try:
			original_locale = dict(i18n.original_locale_env())
		except Exception:
			original_locale = {}
		# Forward display + locale so Automatic language and theme keep working
		# after polkit elevation on all supported DEs (GNOME/KDE/XFCE/…).
		# Must stay the same set the module-level restore accepts.
		for var in SESSION_ENV_ALLOWLIST:
			val = os.environ.get(var)
			if var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
				# The desktop's locale, not this process's (a saved language
				# preference already rewrote LANG/LANGUAGE at i18n import).
				val = original_locale.get(var)
			if val:
				extra_args.append(f"--env-{var}={val}")
		args = ["pkexec", sys.executable] + sys.argv + extra_args
		os.execvp("pkexec", args)
	except Exception:
		args = ["sudo", sys.executable] + sys.argv
		os.execvp("sudo", args)


# Make sure we run as sudo
elevate()


def open_uri_as_user(user, uri, session_env):
	"""Open *uri* in *user*'s default browser from a root process.

	1. The desktop portal (org.freedesktop.portal.OpenURI) on the user's
	   session bus: it runs inside the session, so it resolves the browser
	   exactly like a click in any app (snap Firefox, Flatpak, mimeapps).
	2. Fallback: xdg-open as the user with the session env forwarded.
	"""
	as_user = ["sudo", "-u", user, "-H", "env", *session_env]
	try:
		res = subprocess.run(as_user + ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
			"--object-path", "/org/freedesktop/portal/desktop", "--method", "org.freedesktop.portal.OpenURI.OpenURI",
			"", uri, "{}"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
		if res.returncode == 0:
			return "portal"
	except (OSError, subprocess.SubprocessError):
		pass
	try:
		subprocess.Popen(as_user + ["xdg-open", uri], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL, start_new_session=True)
		return "xdg-open"
	except OSError as exc:
		print(f"Could not open {uri}: {exc}", file=sys.stderr)
		return None


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


def get_real_user():
	"""The desktop user behind this root process, or "root" if none can be found."""
	import re

	# Each source is only consulted if the cheaper ones above it came up empty
	# or returned root -- loginctl in particular spawns a subprocess.
	for source in (
		lambda: os.environ.get("SUDO_USER"),
		_user_from_pkexec,
		_user_from_login,
		lambda: os.environ.get("USER"),
		_user_from_loginctl,
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


def get_user_theme_preference(user=None):
	"""Light/dark for *user* (default: the session's real user)."""
	import theme_detect
	if user is None:
		user = get_real_user()
	if not user or user == "root":
		return "light"
	return theme_detect.get_theme_preference(user=user, default="light")


def get_user_animations_preference(user=None):
	"""Animations enabled for *user* (default: the session's real user)."""
	if user is None:
		user = get_real_user()
	if not user or user == "root":
		return True

	import subprocess
	try:
		cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "gsettings", "get", _SCHEMA_GNOME_IFACE, "enable-animations"]
		val = subprocess.check_output(cmd, text=True, timeout=5).strip()
		return val.lower() == "true"
	except Exception:
		pass

	try:
		cmd = ["sudo", "-u", user, "env", f"HOME=/home/{user}", "dconf", "read", "/org/gnome/desktop/interface/enable-animations"]
		val = subprocess.check_output(cmd, text=True, timeout=5).strip()
		return val.lower() == "true"
	except Exception:
		pass

	return True


def apply_gtk_theme_name(theme_name, prefer_dark, icon_theme=""):
	"""Follow the desktop theme (Yaru accent + light/dark, icon theme) — GTK 4 has no auto theme as root."""
	import theme_detect
	resolved = theme_detect.resolve_gtk4_theme(theme_name, prefer_dark)
	gtk_settings = gtk.Settings.get_default()
	if resolved and gtk_settings:
		gtk_settings.set_property("gtk-theme-name", resolved)
	if icon_theme and gtk_settings:
		gtk_settings.set_property("gtk-icon-theme-name", icon_theme)
	return resolved


def effective_ui_language():
	"""Language the UI shows: the preference when explicit, else what i18n resolved for Automatic."""
	code = preferences.read_language()
	if code and code != preferences.AUTO:
		return code
	getter = getattr(i18n, "effective_language", None)
	try:
		return getter() if callable(getter) else "en"
	except Exception:
		return "en"


# Live theme follower for the elevated app (see theme_detect.ThemeWatcher).
_theme_watcher = None
# Monotonic counter over theme refreshes. The watcher debounces a burst of keys
# into one callback (250 ms), but each probe runs several `sudo -u …` helpers,
# so two switches close together (light → dark → light) can finish out of order.
# Only the newest refresh may touch Gtk.Settings.
_theme_refresh_generation = 0
_theme_refresh_lock = threading.Lock()


def _detect_user_theme(user):
	"""Probe the user's theme preferences (subprocesses: gsettings/dconf as the user). No GTK calls."""
	import theme_detect
	# Every probe resolves the SAME account: re-resolving the session user here
	# could mix one user's light/dark with another's GTK/icon theme.
	detected = {
		"prefer_dark": get_user_theme_preference(user=user) == "dark",
		"enable_animations": get_user_animations_preference(user=user),
		"theme_name": "",
		"icon_theme": "",
	}
	if user and user != "root":
		detected["theme_name"] = theme_detect.get_gtk_theme_name(user=user)
		detected["icon_theme"] = theme_detect.get_icon_theme_name(user=user)
	return detected


def _apply_theme_settings(user, detected):
	"""Apply already-detected theme values to Gtk.Settings (main loop only)."""
	gtk_settings = gtk.Settings.get_default()
	if gtk_settings:
		gtk_settings.set_property("gtk-application-prefer-dark-theme", detected["prefer_dark"])
		gtk_settings.set_property("gtk-enable-animations", detected["enable_animations"])
	if user and user != "root":
		apply_gtk_theme_name(detected["theme_name"], detected["prefer_dark"], detected["icon_theme"])
	return False


def apply_user_theme(user):
	"""Read the user's light/dark, GTK theme, icon theme and animation prefs and apply them (root, synchronous)."""
	detected = _detect_user_theme(user)
	_apply_theme_settings(user, detected)
	return detected["prefer_dark"]


def refresh_user_theme_async(user):
	"""Live theme change: probe on a worker thread, apply on the GTK main loop.

	The probes run several `sudo -u … gsettings/dconf` subprocesses; doing that
	inside the ThemeWatcher callback stalled the UI for their duration.
	"""
	global _theme_refresh_generation
	with _theme_refresh_lock:
		_theme_refresh_generation += 1
		generation = _theme_refresh_generation

	def worker():
		try:
			detected = _detect_user_theme(user)
		except Exception as exc:
			print(f"theme refresh failed: {exc}", file=sys.stderr)
			return
		GLib.idle_add(_apply_theme_settings_if_current, generation, user, detected)

	thread = threading.Thread(target=worker, daemon=True)
	thread.start()
	return thread


def _apply_theme_settings_if_current(generation, user, detected):
	"""Apply a finished probe only while it is still the newest refresh.

	The check and the apply share the lock: releasing it in between would let a
	refresh started right after the check be overwritten by this older probe.
	Holding it across the apply is cheap -- _apply_theme_settings only sets
	Gtk.Settings properties and stats a theme directory, and nothing it calls
	takes this lock.
	"""
	with _theme_refresh_lock:
		if generation != _theme_refresh_generation:
			return False   # a newer switch already started: its result wins
		return _apply_theme_settings(user, detected)


def _gsetting_str(settings, key):
	try:
		return settings.get_string(key)
	except Exception:
		return ""


def _gsetting_bool(settings, key, default=True):
	try:
		return settings.get_boolean(key)
	except Exception:
		return default


def _apply_theme_from_settings(settings, key=None):
	"""Push the desktop's current theme/animation preferences into GTK."""
	try:
		gtk_theme = _gsetting_str(settings, "gtk-theme")
		prefer_dark = (_gsetting_str(settings, "color-scheme") == "prefer-dark"
		               or bool(gtk_theme and "dark" in gtk_theme.lower()))
		enable_animations = _gsetting_bool(settings, "enable-animations")

		gtk_settings = gtk.Settings.get_default()
		if gtk_settings:
			gtk_settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)
			gtk_settings.set_property("gtk-enable-animations", enable_animations)
		apply_gtk_theme_name(gtk_theme, prefer_dark)
	except Exception as e:
		print(f"Error updating theme: {e}", file=sys.stderr)


def _setup_theme_as_root():
	"""Apply the desktop user's theme and follow their change feed.

	Root cannot subscribe to the user's dconf: follow the desktop's change feed
	(gsettings monitor / xfconf-query -m as the user, plus the KDE/LXQt
	settings.ini files) so a light/dark or accent switch is applied live
	instead of needing a restart.
	"""
	global _theme_watcher
	import theme_detect
	user = get_real_user()
	apply_user_theme(user)
	if user and user != "root" and _theme_watcher is None:
		_theme_watcher = theme_detect.ThemeWatcher(user, lambda: refresh_user_theme_async(user)).start()


def _setup_theme_as_user():
	"""Track the session's own GNOME interface settings."""
	schemas = Gio.SettingsSchemaSource.get_default().list_schemas(True)
	all_schemas = schemas[0] + schemas[1]
	if _SCHEMA_GNOME_IFACE not in all_schemas:
		return

	settings = Gio.Settings.new(_SCHEMA_GNOME_IFACE)
	settings.connect("changed", _apply_theme_from_settings)
	_apply_theme_from_settings(settings)


def setup_theme():
	try:
		if os.geteuid() == 0:
			_setup_theme_as_root()
		else:
			_setup_theme_as_user()
	except Exception as e:
		print(f"Error setting up theme tracking: {e}", file=sys.stderr)


import tab_models
MainWindow.on_user_change = tab_models.on_user_change
MainWindow.on_model_add = tab_models.on_model_add
MainWindow.on_model_delete = tab_models.on_model_delete
import tab_video
MainWindow.on_page_switch = tab_video.on_page_switch
MainWindow.capture_frame = tab_video.capture_frame
MainWindow.on_camera_change = tab_video.on_camera_change
import tab_notifications
MainWindow.load_notification_settings = tab_notifications.load_notification_settings
MainWindow.on_notifications_enabled_state_set = tab_notifications.on_notifications_enabled_state_set
MainWindow.on_notifications_sound_state_set = tab_notifications.on_notifications_sound_state_set
MainWindow.on_notifications_details_state_set = tab_notifications.on_notifications_details_state_set
import tab_security
MainWindow.load_security_settings = tab_security.load_security_settings
MainWindow.on_security_preset_toggled = tab_security.on_security_preset_toggled
MainWindow.on_security_liveness_state_set = tab_security.on_security_liveness_state_set
import tab_keyring
MainWindow.update_keyring_status = tab_keyring.update_keyring_status
MainWindow.on_keyring_enable = tab_keyring.on_keyring_enable
MainWindow.on_keyring_disable = tab_keyring.on_keyring_disable


def _launch():
	# Setup theme tracking to follow system dark/light theme
	setup_theme()

	# If no models have been created yet or when it is forced, start the onboarding
	model_dir = paths_factory.user_models_dir_path()
	has_models = False
	try:
		if os.path.isdir(model_dir) and os.listdir(model_dir):
			has_models = True
	except OSError:
		has_models = False

	if "--force-onboarding" in sys.argv or not has_models:
		import onboarding
		ob = onboarding.OnboardingWindow()
		if not getattr(ob, "completed", False):
			sys.exit(0)

	# Open the GTK window
	return MainWindow()


if os.environ.get("UH_DONT_AUTO_LAUNCH") != "1" and "pytest" not in sys.modules:
	window = _launch()

