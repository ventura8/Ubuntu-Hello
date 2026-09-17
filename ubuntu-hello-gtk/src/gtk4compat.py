"""Small GTK 4 helpers replacing GTK 3 APIs the app relied on.

- Gtk.main()/main_quit() are gone: a GLib.MainLoop per top-level flow.
- Gtk.Dialog.run() is gone: run_dialog() spins a nested loop until the
  dialog answers, keeping the wizard's synchronous question/answer flow.
- Container.get_children() is gone: iter_children() walks the sibling list.
- Builder.connect_signals() is gone: builder(scope) resolves handler names on
  the given object (PyGObject BuilderScope).
"""

import gi
import re

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk as gtk
from gi.repository import GLib
from gi.repository import Gio
from gi.repository import GObject
from gi.repository import Pango

_main_loop = None


def run_main():
	"""Block in the application main loop (one level; nested runs are fine)."""
	global _main_loop
	_main_loop = GLib.MainLoop()
	try:
		_main_loop.run()
	finally:
		_main_loop = None


def quit_main():
	if _main_loop is not None and _main_loop.is_running():
		_main_loop.quit()


def run_dialog(dialog):
	"""Show *dialog* and block until it responds; returns the response id."""
	result = {"response": gtk.ResponseType.NONE}
	loop = GLib.MainLoop()

	def on_response(_dialog, response):
		result["response"] = response
		if loop.is_running():
			loop.quit()

	handler = dialog.connect("response", on_response)
	dialog.connect("close-request", lambda *_a: on_response(dialog, gtk.ResponseType.DELETE_EVENT) or False)
	dialog.present()
	loop.run()
	try:
		dialog.disconnect(handler)
	except Exception:
		pass
	return result["response"]


def alert(parent, heading, body="", buttons=("Close",), default=0, cancel=None):
	"""Modal Gtk.AlertDialog; blocks (nested loop) and returns the chosen button index.

	Replaces Gtk.MessageDialog (deprecated in GTK 4.10). *cancel* is the index
	returned when the dialog is dismissed (Escape / close); defaults to *default*.
	"""
	result = {"index": default if cancel is None else cancel}
	loop = GLib.MainLoop()
	dialog = gtk.AlertDialog(message=heading, detail=body or "", modal=True)
	dialog.set_buttons(list(buttons))
	dialog.set_default_button(default)
	dialog.set_cancel_button(default if cancel is None else cancel)

	def done(source, res):
		try:
			result["index"] = source.choose_finish(res)
		except Exception:
			pass
		if loop.is_running():
			loop.quit()

	dialog.choose(parent, None, done)
	loop.run()
	return result["index"]


class PromptWindow(gtk.Window):
	"""Small modal window with a content box and action buttons.

	Replaces Gtk.Dialog (deprecated in GTK 4.10) for prompts that need custom
	widgets (password / model-name entries). Emits ``response`` with a
	Gtk.ResponseType-style int so run_dialog() drives it exactly like before.
	"""

	__gsignals__ = {"response": (GObject.SignalFlags.RUN_LAST, None, (int,))}

	def __init__(self, parent, title):
		gtk.Window.__init__(self, title=title, transient_for=parent, modal=True)
		self.set_resizable(False)
		self.set_default_size(420, -1)
		outer = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=18)
		outer.set_margin_top(20)
		outer.set_margin_bottom(20)
		outer.set_margin_start(20)
		outer.set_margin_end(20)
		self.content = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=10)
		outer.append(self.content)
		self.actions = gtk.Box(spacing=10)
		self.actions.set_halign(gtk.Align.END)
		outer.append(self.actions)
		self.set_child(outer)
		self.connect("close-request", lambda *_a: self.respond(gtk.ResponseType.DELETE_EVENT) or False)

	def add_action(self, label, response, suggested=False):
		button = gtk.Button(label=label)
		if suggested:
			button.add_css_class("suggested-action")
			self.set_default_widget(button)
		button.connect("clicked", lambda *_a: self.respond(response))
		self.actions.append(button)
		return button

	def respond(self, response):
		self.emit("response", int(response))


class _Row(GObject.Object):
	"""ListStore item carrying one row's cell values."""

	def __init__(self, values):
		GObject.Object.__init__(self)
		self.values = list(values)


class ColumnList:
	"""Gtk.ColumnView + Gio.ListStore + SingleSelection with a tiny list-like API.

	Replaces Gtk.TreeView/ListStore (deprecated in GTK 4.10). Rows are plain
	value lists (``rows[i][col]``); only the first *ncols* values are shown.
	"""

	def __init__(self, titles, ncols=None):
		self.rows = []
		self.titles = list(titles)
		self.ncols = ncols or len(self.titles)
		self.store = Gio.ListStore.new(_Row)
		self.selection = gtk.SingleSelection.new(self.store)
		self.selection.set_autoselect(False)
		self.selection.set_can_unselect(True)
		self.widget = gtk.ColumnView.new(self.selection)
		self.widget.set_vexpand(True)
		self.widget.set_hexpand(True)
		self.widget.add_css_class("data-table")
		for index, title in enumerate(self.titles):
			factory = gtk.SignalListItemFactory()
			factory.connect("setup", self._setup_cell)
			factory.connect("bind", self._bind_cell, index)
			column = gtk.ColumnViewColumn.new(title, factory)
			column.set_expand(True)
			column.set_resizable(True)
			self.widget.append_column(column)

	@staticmethod
	def _setup_cell(_factory, item):
		label = gtk.Label(xalign=0.0)
		# Long device names / paths must not fight the parent for width.
		label.set_ellipsize(Pango.EllipsizeMode.END)
		item.set_child(label)

	@staticmethod
	def _bind_cell(_factory, item, index):
		item.get_child().set_text(str(item.get_item().values[index]))

	def __len__(self):
		return len(self.rows)

	def __getitem__(self, index):
		return self.rows[index]

	def append(self, values):
		values = list(values)
		self.rows.append(values)
		self.store.append(_Row(values))

	def clear(self):
		self.rows = []
		self.store.remove_all()

	def select(self, index):
		self.selection.set_selected(index if index is not None and index >= 0 else gtk.INVALID_LIST_POSITION)

	def selected_index(self):
		selected = self.selection.get_selected()
		return -1 if selected == gtk.INVALID_LIST_POSITION else int(selected)

	def selected_row(self):
		index = self.selected_index()
		return self.rows[index] if 0 <= index < len(self.rows) else None

	def connect_selection_changed(self, callback):
		self.selection.connect("notify::selected", lambda *_a: callback(self))

	def scroll_to(self, index):
		try:
			self.widget.scroll_to(index, None, gtk.ListScrollFlags.NONE, None)
		except Exception:
			pass


def iter_children(widget):
	"""Yield the direct children of *widget* (GTK 4 has no get_children())."""
	child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
	while child is not None:
		nxt = child.get_next_sibling()
		yield child
		child = nxt


_TRANSLATABLE = re.compile(
	r'(<property\b[^>]*\btranslatable="(?:1|yes|true)"[^>]*>)(.*?)(</property>)', re.S)
_CONTEXT = re.compile(r'\bcontext="([^"]*)"')


def translate_ui_xml(xml, gettext_func, pgettext_func=None):
	"""Translate every translatable <property> of a GtkBuilder .ui in Python.

	GtkBuilder's own translation goes through C gettext, which ignores the
	Settings language override whenever the process locale is C/C.UTF-8 (no
	locale data installed, root under pkexec, CI). Python gettext honours
	LANGUAGE regardless, so the .ui is translated here before loading.
	"""
	import html

	def repl(match):
		opening, text, closing = match.groups()
		if not text.strip():
			return match.group(0)
		source = html.unescape(text)
		context = _CONTEXT.search(opening)
		if context and pgettext_func is not None:
			translated = pgettext_func(context.group(1), source)
		else:
			translated = gettext_func(source)
		return opening + html.escape(translated, quote=False) + closing

	return _TRANSLATABLE.sub(repl, xml)


def builder(scope, path, domain):
	"""Gtk.Builder that resolves <signal handler="..."> names on *scope*.

	The .ui text is translated with the app's Python gettext (see
	translate_ui_xml) so Settings' instant language switch also reaches the
	Builder-defined labels.
	"""
	b = gtk.Builder(scope)
	try:
		import i18n
		gettext_func = getattr(i18n, "_", None)
		pgettext_func = getattr(getattr(i18n, "translation", None), "pgettext", None)
	except Exception:
		gettext_func, pgettext_func = None, None
	xml = None
	if callable(gettext_func):
		try:
			with open(path, encoding="utf-8") as fh:
				xml = fh.read()
		except OSError:
			xml = None
	if xml is not None:
		b.add_from_string(translate_ui_xml(xml, gettext_func, pgettext_func))
	else:
		b.set_translation_domain(domain)
		b.add_from_file(path)
	return b


def apply_text_direction(language_code):
	"""Mirror the UI for right-to-left languages (GTK only reads the locale at startup)."""
	import languages
	rtl = languages.is_rtl(language_code)
	gtk.Widget.set_default_direction(gtk.TextDirection.RTL if rtl else gtk.TextDirection.LTR)
	return rtl


class IdDropDown:
	"""ComboBoxText-style API over a stock Gtk.DropDown (ComboBoxText is deprecated in GTK 4.10).

	Keeps (id, text) pairs in a Gtk.StringList, exposes remove_all/append/
	append_text/set_active/get_active/set_active_id/get_active_id/
	get_active_text and forwards ``notify::selected`` to *on_changed(self)*.
	Everything else is delegated to the wrapped widget.
	"""

	def __init__(self, widget, on_changed=None, min_width=0):
		self.widget = widget
		self._ids = []
		self.items = 0
		self._on_changed = on_changed
		self.store = gtk.StringList()
		widget.set_model(self.store)
		# Display: plain label per row (the search expression below is NOT the
		# display text). Long lists (98 languages, /dev/v4l/by-path/... cameras)
		# get type-ahead search that matches anywhere, case-insensitively, and
		# ignores accents ("romana" finds "Romanian (Română)", "video0" finds a
		# by-path camera) — GTK's default is a prefix match on the label.
		# The expression is only the search haystack; both the button and the
		# popup list must render rows with our own factory (set after the
		# expression, and explicitly for the list, or GTK shows the haystack).
		widget.set_expression(gtk.ClosureExpression.new(str, self._search_haystack, None))
		self.factory = gtk.SignalListItemFactory()          # button: may ellipsize
		self.factory.connect("setup", self._setup_row)
		self.factory.connect("bind", self._bind_row)
		self.list_factory = gtk.SignalListItemFactory()     # popup rows: always the full name
		self.list_factory.connect("setup", self._setup_list_row)
		self.list_factory.connect("bind", self._bind_row)
		widget.set_factory(self.factory)
		widget.set_list_factory(self.list_factory)
		widget.set_enable_search(True)
		if hasattr(widget, "set_search_match_mode"):
			widget.set_search_match_mode(gtk.StringFilterMatchMode.SUBSTRING)
		if min_width:
			widget.set_size_request(min_width, -1)
		widget.connect("notify::selected", self._notify_selected)

	@staticmethod
	def _setup_row(_factory, item):
		# Full names in the list (the popup grows to fit); the button itself may ellipsize.
		label = gtk.Label(xalign=0.0)
		label.set_ellipsize(Pango.EllipsizeMode.END)
		label.set_max_width_chars(72)
		item.set_child(label)

	@staticmethod
	def _setup_list_row(_factory, item):
		# One line per row: wrapped rows got clipped by the popup's fixed height
		# when the search left a single result. The button is given a
		# width-request in the .ui so names fit; very long ones ellipsize.
		# Never ellipsize in the list: its natural width makes the popup grow
		# past the button so the full name is always readable.
		label = gtk.Label(xalign=0.0)
		label.set_single_line_mode(True)
		item.set_child(label)

	@staticmethod
	def _bind_row(_factory, item):
		item.get_child().set_text(item.get_item().get_string())

	@staticmethod
	def search_haystack(text):
		"""Text plus its accent-stripped form (both lower-case) for type-ahead matching."""
		import unicodedata
		folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
		return f"{text} {folded}".lower()

	def _search_haystack(self, item, *_args):
		try:
			text = item.get_string()
		except Exception:
			return ""
		index = self.store.get_n_items() and next((i for i in range(self.store.get_n_items()) if self.store.get_string(i) == text), -1)
		id_ = self._ids[index] if isinstance(index, int) and 0 <= index < len(self._ids) else ""
		return f"{self.search_haystack(text)} {id_}".strip()

	def _notify_selected(self, *_args):
		if self._on_changed is not None:
			self._on_changed(self)

	def __getattr__(self, name):
		return getattr(self.widget, name)

	def remove_all(self):
		self.store.splice(0, self.store.get_n_items(), [])
		self._ids = []
		self.items = 0

	def append(self, id_, text):
		self.store.append(text)
		self._ids.append(id_)
		self.items += 1

	def append_text(self, text):
		self.append(text, text)

	def get_model(self):
		return self.store

	def set_active(self, index):
		self.widget.set_selected(index if index is not None and index >= 0 else gtk.INVALID_LIST_POSITION)

	def get_active(self):
		selected = self.widget.get_selected()
		return -1 if selected == gtk.INVALID_LIST_POSITION else int(selected)

	def set_active_id(self, id_):
		if id_ in self._ids:
			self.set_active(self._ids.index(id_))
			return True
		return False

	def get_active_id(self):
		index = self.get_active()
		return self._ids[index] if 0 <= index < len(self._ids) else None

	def get_active_text(self):
		index = self.get_active()
		return self.store.get_string(index) if 0 <= index < self.store.get_n_items() else None


def close_popover(widget):
	"""Pop down any Gtk.Popover child of *widget* (a DropDown's list), returns True if one was open."""
	closed = False
	for child in iter_children(widget):
		if isinstance(child, gtk.Popover) and child.get_visible():
			child.popdown()
			closed = True
	return closed


def dropdown(widget, on_changed=None, min_width=0):
	"""Wrap (once) a Builder Gtk.DropDown in an IdDropDown adapter."""
	adapter = getattr(widget, "_uh_dropdown", None)
	if adapter is None:
		adapter = IdDropDown(widget, on_changed, min_width)
		try:
			widget._uh_dropdown = adapter
		except Exception:
			pass
	elif on_changed is not None:
		adapter._on_changed = on_changed
	return adapter


def pixbuf_to_texture(pixbuf):
	gi.require_version("Gdk", "4.0")
	from gi.repository import Gdk
	return Gdk.Texture.new_for_pixbuf(pixbuf)
