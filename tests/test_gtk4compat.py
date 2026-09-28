"""gtk4compat helpers with a fake GTK (no display): the DropDown id/text adapter and dialog shims."""
import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

gtk4compat = importlib.import_module("gtk4compat")

INVALID = 4294967295


class FakeStringList:
	def __init__(self):
		self.items = []

	def append(self, text):
		self.items.append(text)

	def splice(self, pos, n_removals, additions):
		self.items[pos:pos + n_removals] = list(additions)

	def get_n_items(self):
		return len(self.items)

	def get_string(self, index):
		return self.items[index]


class FakeDropDown:
	def __init__(self):
		self.selected = INVALID
		self.handlers = {}
		self.model = None
		self.search = None

	def set_model(self, model):
		self.model = model

	def set_expression(self, expression):
		self.expression = expression

	def set_enable_search(self, flag):
		self.search = flag

	def set_search_match_mode(self, mode):
		self.match_mode = mode

	def set_factory(self, factory):
		self.factory = factory

	def set_list_factory(self, factory):
		self.list_factory = factory

	def set_expression(self, expression):
		self.expression = expression

	def set_size_request(self, w, h):
		self.size_request = (w, h)

	def connect(self, signal, handler):
		self.handlers[signal] = handler

	def set_selected(self, index):
		self.selected = index
		self.handlers["notify::selected"](self, None)

	def get_selected(self):
		return self.selected

	def get_visible(self):
		return True


@pytest.fixture
def fake_gtk(monkeypatch):
	fake = MagicMock()
	fake.StringList = FakeStringList
	fake.INVALID_LIST_POSITION = INVALID
	monkeypatch.setattr(gtk4compat, "gtk", fake)
	return fake


def _make(fake_gtk, on_changed=None):
	widget = FakeDropDown()
	return widget, gtk4compat.IdDropDown(widget, on_changed)


def test_append_and_ids(fake_gtk):
	widget, combo = _make(fake_gtk)
	assert widget.search is True
	assert widget.model is combo.store
	assert widget.match_mode == fake_gtk.StringFilterMatchMode.SUBSTRING   # search anywhere, not prefix-only
	combo.append("auto", "Automatic")
	combo.append("ro", "Romanian (Română)")
	combo.append_text("/dev/video0")
	assert combo.items == 3
	assert combo.get_model().get_n_items() == 3
	assert combo.get_active() == -1
	assert combo.get_active_id() is None
	assert combo.get_active_text() is None
	assert combo.set_active_id("ro") is True
	assert combo.get_active() == 1
	assert combo.get_active_id() == "ro"
	assert combo.get_active_text() == "Romanian (Română)"
	assert combo.set_active_id("missing") is False
	assert combo.get_active_id() == "ro"


def test_set_active_negative_clears_and_remove_all(fake_gtk):
	widget, combo = _make(fake_gtk)
	combo.append_text("a")
	combo.set_active(0)
	assert combo.get_active_text() == "a"
	combo.set_active(-1)
	assert widget.selected == INVALID
	assert combo.get_active() == -1
	combo.remove_all()
	assert combo.items == 0
	assert combo.get_model().get_n_items() == 0
	assert combo.get_active_id() is None


def test_changed_callback_and_delegation(fake_gtk):
	seen = []
	widget, combo = _make(fake_gtk, on_changed=seen.append)
	combo.append_text("x")
	combo.set_active(0)
	assert seen == [combo]
	# unknown attributes go to the wrapped widget
	assert combo.get_visible() is True
	assert combo.widget is widget


def test_dropdown_wraps_once_and_updates_callback(fake_gtk):
	real_dropdown = getattr(gtk4compat, "_real_dropdown", gtk4compat.dropdown)  # conftest shims dropdown()
	widget = FakeDropDown()
	first = real_dropdown(widget)
	second = real_dropdown(widget, on_changed=lambda c: None)
	assert first is second
	assert second._on_changed is not None
	# a widget that refuses attributes (no __dict__: a slotted class, like a C-backed
	# GObject without instance dict) still gets an adapter via the fallback branch
	class Rigid:
		__slots__ = ("selected", "handlers", "model", "search", "expression", "match_mode",
		             "factory", "list_factory", "size_request")
	for _name, _fn in vars(FakeDropDown).items():
		if callable(_fn) and not _name.startswith("__") or _name == "__init__":
			setattr(Rigid, _name, _fn)
	rigid = Rigid()
	with pytest.raises(AttributeError):
		rigid.anything = 1
	adapter = real_dropdown(rigid)
	assert isinstance(adapter, gtk4compat.IdDropDown)
	assert not hasattr(rigid, "_uh_dropdown")
	assert real_dropdown(rigid) is not adapter   # nothing to cache on: a fresh adapter each time


def test_iter_children():
	class Node:
		def __init__(self, nxt=None):
			self.nxt = nxt

		def get_next_sibling(self):
			return self.nxt

	last = Node()
	first = Node(last)
	parent = MagicMock()
	parent.get_first_child.return_value = first
	assert list(gtk4compat.iter_children(parent)) == [first, last]
	assert list(gtk4compat.iter_children(object())) == []


def test_builder_falls_back_to_c_gettext_without_python_i18n(fake_gtk, monkeypatch):
	monkeypatch.setitem(sys.modules, "i18n", types.ModuleType("i18n"))  # no `_`
	b = gtk4compat.builder("scope", "/nonexistent/x.ui", "dom")
	fake_gtk.Builder.assert_called_once_with("scope")
	b.set_translation_domain.assert_called_once_with("dom")
	b.add_from_file.assert_called_once_with("/nonexistent/x.ui")


def test_run_main_and_quit(monkeypatch):
	loop = MagicMock()
	loop.is_running.return_value = True
	monkeypatch.setattr(gtk4compat.GLib, "MainLoop", lambda: loop)
	gtk4compat.run_main()
	loop.run.assert_called_once()
	assert gtk4compat._main_loop is None
	quit_main = getattr(gtk4compat, "_real_quit_main", gtk4compat.quit_main)  # conftest shims quit_main()
	quit_main()  # no loop: no-op
	monkeypatch.setattr(gtk4compat, "_main_loop", loop, raising=False)
	quit_main()
	loop.quit.assert_called_once()


def test_close_popover_pops_down_visible_popovers(fake_gtk):
	class Pop:
		def __init__(self, visible):
			self.visible = visible
			self.down = False
			self.nxt = None

		def get_visible(self):
			return self.visible

		def popdown(self):
			self.down = True

		def get_next_sibling(self):
			return self.nxt

	fake_gtk.Popover = Pop
	hidden, shown = Pop(False), Pop(True)
	hidden.nxt = shown
	widget = MagicMock()
	widget.get_first_child.return_value = hidden
	assert gtk4compat.close_popover(widget) is True
	assert shown.down
	assert not hidden.down
	widget.get_first_child.return_value = hidden
	shown.visible = False
	assert gtk4compat.close_popover(widget) is False


def test_translate_ui_xml_translates_only_translatable_text():
	xml = ('<property name="label" translatable="1">Hello &amp; bye</property>'
	       '<property name="title" translatable="1" context="Window title">Config</property>'
	       '<property name="label">Keep</property>'
	       '<property name="label" translatable="1">   </property>')
	out = gtk4compat.translate_ui_xml(xml, lambda s: f"[{s}]", lambda c, s: f"[{c}|{s}]")
	assert '>[Hello &amp; bye]<' in out          # entities decoded for gettext, re-escaped for XML
	assert '>[Window title|Config]<' in out       # msgctxt via pgettext
	assert '>Keep<' in out
	assert '>   <' in out
	# no pgettext available: context strings fall back to plain gettext
	assert '>[Config]<' in gtk4compat.translate_ui_xml(xml, lambda s: f"[{s}]")


def test_builder_pretranslates_with_python_gettext(fake_gtk, tmp_path, monkeypatch):
	ui = tmp_path / "x.ui"
	ui.write_text('<interface><object class="GtkLabel"><property name="label" translatable="1">Models</property></object></interface>', encoding="utf-8")
	fake_i18n = MagicMock()
	fake_i18n._ = lambda s: "نماذج" if s == "Models" else s
	fake_i18n.translation.pgettext = lambda c, s: s
	monkeypatch.setitem(sys.modules, "i18n", fake_i18n)
	b = gtk4compat.builder("scope", str(ui), "dom")
	b.add_from_string.assert_called_once()
	assert "نماذج" in b.add_from_string.call_args.args[0]
	b.add_from_file.assert_not_called()



_PICTURE_UI = (
	'<interface><object class="GtkPicture">'
	'<property name="content-fit">contain</property>'
	'<property name="can-shrink">1</property></object></interface>'
)


def test_builder_without_python_i18n_loads_file_with_c_gettext_domain(fake_gtk, tmp_path, monkeypatch):
	monkeypatch.setitem(sys.modules, "i18n", types.ModuleType("i18n"))  # no `_`
	ui = tmp_path / "x.ui"
	ui.write_text(_PICTURE_UI, encoding="utf-8")
	b = gtk4compat.builder("scope", str(ui), "dom")
	b.set_translation_domain.assert_called_once_with("dom")
	b.add_from_string.assert_called_once_with(_PICTURE_UI)
	b.add_from_file.assert_not_called()


def test_adapt_ui_xml_keeps_content_fit_on_gtk_4_8_and_newer(fake_gtk):
	assert gtk4compat.adapt_ui_xml(_PICTURE_UI) == _PICTURE_UI


def test_adapt_ui_xml_drops_content_fit_on_gtk_4_6(fake_gtk):
	"""Ubuntu 22.04's GTK 4.6 rejects the whole .ui on an unknown property."""
	del fake_gtk.ContentFit
	out = gtk4compat.adapt_ui_xml(_PICTURE_UI)
	assert "content-fit" not in out
	assert '<property name="can-shrink">1</property>' in out


def test_builder_adapts_ui_for_running_gtk(fake_gtk, tmp_path, monkeypatch):
	del fake_gtk.ContentFit
	fake_i18n = MagicMock()
	fake_i18n._ = lambda s: s
	monkeypatch.setitem(sys.modules, "i18n", fake_i18n)
	ui = tmp_path / "x.ui"
	ui.write_text(_PICTURE_UI, encoding="utf-8")
	b = gtk4compat.builder("scope", str(ui), "dom")
	assert "content-fit" not in b.add_from_string.call_args.args[0]

def test_apply_text_direction(fake_gtk, monkeypatch):
	fake_languages = MagicMock()
	fake_languages.is_rtl = lambda code: code == "ar"
	monkeypatch.setitem(sys.modules, "languages", fake_languages)
	assert gtk4compat.apply_text_direction("ar") is True
	fake_gtk.Widget.set_default_direction.assert_called_with(fake_gtk.TextDirection.RTL)
	assert gtk4compat.apply_text_direction("de") is False
	fake_gtk.Widget.set_default_direction.assert_called_with(fake_gtk.TextDirection.LTR)


def test_search_haystack_folds_accents_and_case():
	hay = gtk4compat.IdDropDown.search_haystack("Romanian (Română)")
	assert "romanian (romana)" in hay
	assert "română" in hay


def test_search_expression_includes_id_and_folded_text(fake_gtk):
	widget, combo = _make(fake_gtk)
	combo.append("ro", "Romanian (Română)")
	item = MagicMock(); item.get_string.return_value = "Romanian (Română)"
	hay = combo._search_haystack(item)
	assert "romana" in hay
	assert hay.endswith(" ro")
	bad = MagicMock(); bad.get_string.side_effect = RuntimeError("gone")
	assert combo._search_haystack(bad) == ""


def test_dropdown_min_width(fake_gtk):
	widget = FakeDropDown()
	gtk4compat.IdDropDown(widget, min_width=380)
	assert widget.size_request == (380, -1)


def test_run_dialog_blocks_until_response(fake_gtk, monkeypatch):
	loop = MagicMock(); loop.is_running.return_value = True
	monkeypatch.setattr(gtk4compat.GLib, "MainLoop", lambda: loop)
	dialog = MagicMock()
	handlers = {}
	def connect(sig, cb):
		handlers[sig] = cb
		return 7
	dialog.connect.side_effect = connect
	fake_gtk.ResponseType.NONE = -1; fake_gtk.ResponseType.DELETE_EVENT = -4
	loop.run.side_effect = lambda: handlers["response"](dialog, 5)
	run_dialog = getattr(gtk4compat, "_real_run_dialog", gtk4compat.run_dialog)
	assert run_dialog(dialog) == 5
	dialog.present.assert_called_once(); loop.quit.assert_called_once(); dialog.disconnect.assert_called_once_with(7)
	# closing the window answers DELETE_EVENT
	loop.reset_mock(); loop.is_running.return_value = True
	loop.run.side_effect = lambda: handlers["close-request"](dialog)
	assert run_dialog(dialog) == -4


class _FakePromptWindow:
	def __init__(self, parent, title):
		self.parent, self.title = parent, title
		self.content = MagicMock()
		self.actions = []
		self.destroyed = False

	def add_action(self, label, response, suggested=False):
		self.actions.append((label, response, suggested))

	def destroy(self):
		self.destroyed = True


def test_alert_without_alertdialog_uses_prompt_window(fake_gtk, monkeypatch):
	"""GTK 4.6 (Ubuntu 22.04) has no Gtk.AlertDialog: same contract on a PromptWindow."""
	del fake_gtk.AlertDialog
	windows = []

	def make(parent, title):
		windows.append(_FakePromptWindow(parent, title))
		return windows[-1]

	monkeypatch.setattr(gtk4compat, "PromptWindow", make)
	answers = iter([1, -4, 7])
	monkeypatch.setattr(gtk4compat, "run_dialog", lambda window: next(answers))
	assert gtk4compat.alert("parent", "Sure?", "detail", buttons=("Cancel", "Delete"), default=1, cancel=0) == 1
	win = windows[0]
	assert win.parent == "parent"
	assert win.destroyed
	assert win.actions == [("Cancel", 0, False), ("Delete", 1, True)]
	assert win.content.append.call_count == 2  # heading + body
	fake_gtk.Label.assert_any_call(label="Sure?", xalign=0.0, wrap=True)
	fake_gtk.Label.assert_any_call(label="detail", xalign=0.0, wrap=True)
	# Escape / close (DELETE_EVENT) -> cancel index; out-of-range answer too
	assert gtk4compat.alert("parent", "Sure?", buttons=("Cancel", "Delete"), default=1, cancel=0) == 0
	assert gtk4compat.alert("parent", "Info") == 0
	assert windows[2].content.append.call_count == 1  # no body label


def test_dropdown_without_expression_support_disables_search(fake_gtk):
	"""PyGObject 3.42 (Ubuntu 22.04) rejects a Gtk.Expression: no type-ahead, still a dropdown."""
	widget = FakeDropDown()
	with patch.object(gtk4compat.gi, "version_info", (3, 42, 1)):
		gtk4compat.IdDropDown(widget)
	assert widget.search is False
	assert not hasattr(widget, "expression")
	fake_gtk.ClosureExpression.new.assert_not_called()
	assert widget.factory is not None


def test_gtk_expressions_supported_from_pygobject_3_44():
	with patch.object(gtk4compat.gi, "version_info", (3, 44, 0)):
		assert gtk4compat.gtk_expressions_supported() is True
	with patch.object(gtk4compat.gi, "version_info", (3, 42, 1)):
		assert gtk4compat.gtk_expressions_supported() is False

def test_alert_returns_chosen_button(fake_gtk, monkeypatch):
	loop = MagicMock(); loop.is_running.return_value = True
	monkeypatch.setattr(gtk4compat.GLib, "MainLoop", lambda: loop)
	dialog = fake_gtk.AlertDialog.return_value
	dialog.choose.side_effect = lambda parent, cancellable, cb: cb(dialog, "result")
	dialog.choose_finish.return_value = 1
	loop.run.side_effect = lambda: None
	assert gtk4compat.alert("parent", "Sure?", "detail", buttons=("Cancel", "Delete"), default=1, cancel=0) == 1
	dialog.set_buttons.assert_called_once_with(["Cancel", "Delete"])
	dialog.set_default_button.assert_called_once_with(1); dialog.set_cancel_button.assert_called_once_with(0)
	# dismissed / error in choose_finish -> cancel index
	dialog.choose_finish.side_effect = RuntimeError("dismissed")
	assert gtk4compat.alert("parent", "Sure?", buttons=("Cancel", "Delete"), default=1, cancel=0) == 0
	assert gtk4compat.alert("parent", "Info") == 0
