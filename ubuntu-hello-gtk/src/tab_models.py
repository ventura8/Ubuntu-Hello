import subprocess
import enroll

from i18n import _
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk as gtk
from gi.repository import GObject as gobject
import gtk4compat


def on_user_change(self, select):
	self.active_user = select.get_active_text()
	self.load_model_list()
	self.update_keyring_status()


def on_model_add(self, button):
	if self.userlist.items == 0:
		return
	# Ask for the model name in a small modal prompt
	dialog = gtk4compat.PromptWindow(self.window, _("Confirm Model Creation"))
	dialog.add_action(_("Cancel"), gtk.ResponseType.CANCEL)
	dialog.add_action(_("Add"), gtk.ResponseType.OK, suggested=True)
	dialog.content.append(gtk.Label(label=_("Please enter a name for the new model, 24 characters max"), xalign=0.0, wrap=True))

	entry = gtk.Entry()
	entry.set_hexpand(True)
	entry.set_placeholder_text(_("Model name:"))
	entry.set_max_length(24)
	entry.connect("activate", lambda *_a: dialog.respond(gtk.ResponseType.OK))
	dialog.content.append(entry)

	response = gtk4compat.run_dialog(dialog)
	entered_name = entry.get_text()
	dialog.destroy()

	if response == gtk.ResponseType.OK:
		dialog = gtk4compat.PromptWindow(self.window, _("Creating Model"))
		dialog.content.append(gtk.Label(label=_("Keep your face inside the camera view and follow the prompts; this takes about a minute."), wrap=True, max_width_chars=48))
		guide_label = gtk.Label(label=_("Look straight at the camera"), wrap=True)
		guide_label.add_css_class("heading")
		dialog.content.append(guide_label)
		progress_label = gtk.Label(label="")
		progress_label.add_css_class("dim-label")
		dialog.content.append(progress_label)
		dialog.present()

		# Wait a bit to allow the user to read the dialog
		gobject.timeout_add(600, lambda: execute_add(self, dialog, entered_name, guide_label, progress_label))


def execute_add(box, dialog, entered_name, guide_label=None, progress_label=None):
	"""Run `ubuntu-hello add` on a worker thread; the dialog shows the live guidance."""

	def on_guide(key):
		text = enroll.guide_text(key)
		if guide_label is not None and text:
			guide_label.set_text(text)
		return False

	def on_progress(count, total):
		if progress_label is not None:
			progress_label.set_text(enroll.progress_text(count, total))
		return False

	def on_done(status, output):
		dialog.destroy()
		if status != 0:
			gtk4compat.alert(box.window, _("Error while adding model, error code {}: \n\n").format(str(status)).strip(), output)
		box.load_model_list()
		return False

	enroll.run_add(
		["ubuntu-hello", "-y", "-U", box.active_user, "add", entered_name],
		on_guide, on_progress, on_done)
	return False


def on_model_delete(self, button):
	row = self.models.selected_row()
	if row is None:
		return
	model_id, name = row[0], row[2]

	choice = gtk4compat.alert(
		self.window,
		_("Are you sure you want to delete model {id} ({name})?").format(id=model_id, name=name),
		buttons=(_("Cancel"), _("Delete")), default=1, cancel=0)
	if choice != 1:
		return

	res = subprocess.run(["ubuntu-hello", "remove", str(model_id), "-y", "-U", self.active_user], capture_output=True, text=True)
	status, output = res.returncode, res.stdout + res.stderr

	if status != 0:
		gtk4compat.alert(self.window, _("Error while deleting model, error code {}: \n\n").format(status).strip(), output)

	self.load_model_list()
