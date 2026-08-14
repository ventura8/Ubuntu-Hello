import os
import subprocess
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk as gtk
from i18n import _
import auth_helper
from wallet_backend import wallet_backend_label, wallet_unlock_phrase


KEYRING_KEYS_DIR = "/etc/ubuntu-hello/keyring-keys"

class KeyringPasswordDialog(gtk.Dialog):
	def __init__(self, parent, user):
		gtk.Dialog.__init__(self, title=_("Enable Keyring/KWallet Unlocking"), parent=parent, flags=gtk.DialogFlags.MODAL)
		self.add_buttons(gtk.STOCK_CANCEL, gtk.ResponseType.CANCEL, gtk.STOCK_OK, gtk.ResponseType.OK)
		self.set_default_response(gtk.ResponseType.OK)
		self.set_resizable(False)

		box = self.get_content_area()
		box.set_spacing(10)
		box.set_margin_left(15)
		box.set_margin_right(15)
		box.set_margin_top(15)
		box.set_margin_bottom(15)

		label = gtk.Label()
		label.set_markup(_("Enter password for user <b>{}</b> to unlock {} ({}):").format(
			user, wallet_unlock_phrase(), wallet_backend_label()))
		label.set_alignment(0.0, 0.5)
		box.pack_start(label, False, False, 0)

		self.entry1 = gtk.Entry()
		self.entry1.set_visibility(False)
		self.entry1.set_placeholder_text(_("Password"))
		# Connect the 'activate' signal of the entry to respond with OK when user hits Enter
		self.entry1.connect("activate", lambda entry: self.response(gtk.ResponseType.OK))
		box.pack_start(self.entry1, False, False, 0)

		self.show_all()

def update_keyring_status(self):
	if not self.active_user or self.userlist.items == 0:
		self.keyring_status_label.set_markup(_("<i>No user selected</i>"))
		self.keyring_enable_button.set_sensitive(False)
		self.keyring_disable_button.set_sensitive(False)
		return

	keyring_keys_dir = KEYRING_KEYS_DIR
	tpm_keys_dir = "/etc/ubuntu-hello/tpm-keys"
	pending_dir = "/etc/ubuntu-hello/keyring-caching-pending"
	
	key_file = os.path.join(keyring_keys_dir, self.active_user)
	tpm_pub = os.path.join(tpm_keys_dir, f"{self.active_user}.pub")
	pending_file = os.path.join(pending_dir, self.active_user)
	wallet = wallet_backend_label()

	if os.path.exists(tpm_pub):
		self.keyring_status_label.set_markup(_("<span foreground='green'><b>Enabled (TPM Hardware)</b></span> — {}").format(wallet))
		self.keyring_enable_button.set_sensitive(False)
		self.keyring_disable_button.set_sensitive(True)
	elif os.path.exists(key_file):
		self.keyring_status_label.set_markup(_("<span foreground='green'><b>Enabled (Software Caching)</b></span> — {}").format(wallet))
		self.keyring_enable_button.set_sensitive(False)
		self.keyring_disable_button.set_sensitive(True)
	elif os.path.exists(pending_file):
		self.keyring_status_label.set_markup(_("<span foreground='orange'><b>Enabled (Pending Password Entry)</b></span> — {}").format(wallet))
		self.keyring_enable_button.set_sensitive(False)
		self.keyring_disable_button.set_sensitive(True)
	else:
		self.keyring_status_label.set_markup(_("<span foreground='red'><b>Disabled</b></span> — {}").format(wallet))
		self.keyring_enable_button.set_sensitive(True)
		self.keyring_disable_button.set_sensitive(False)

def on_keyring_enable(self, button):
	if not self.active_user:
		return

	dialog = KeyringPasswordDialog(self.window, self.active_user)
	response = dialog.run()

	passwd1 = dialog.entry1.get_text()
	dialog.destroy()

	if response != gtk.ResponseType.OK:
		return

	if not passwd1:
		error_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		error_dialog.set_title(_("Error"))
		error_dialog.props.text = _("Password cannot be empty")
		error_dialog.run()
		error_dialog.destroy()
		return

	if not auth_helper.verify_user_password(self.active_user, passwd1):
		error_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		error_dialog.set_title(_("Error"))
		error_dialog.props.text = _("Incorrect password for user {}").format(self.active_user)
		error_dialog.run()
		error_dialog.destroy()
		return

	try:
		res = subprocess.run(
			["ubuntu-hello", "keyring", "enable", "-U", self.active_user],
			input=passwd1 + "\n",
			capture_output=True,
			text=True,
			timeout=120,
		)
		if res.returncode != 0:
			detail = (res.stderr or res.stdout or "").strip() or _("unknown error")
			raise Exception(detail)

		success_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.INFO, buttons=gtk.ButtonsType.CLOSE)
		success_dialog.set_title(_("Success"))
		success_dialog.props.text = _("Keyring/KWallet unlocking enabled successfully for user {}.").format(self.active_user)
		success_dialog.run()
		success_dialog.destroy()
	except Exception as e:
		error_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		error_dialog.set_title(_("Error"))
		error_dialog.props.text = _("Failed to enable keyring unlocking: {}").format(str(e))
		error_dialog.run()
		error_dialog.destroy()

	self.update_keyring_status()

def on_keyring_disable(self, button):
	if not self.active_user:
		return

	confirm_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.QUESTION, buttons=gtk.ButtonsType.YES_NO)
	confirm_dialog.set_title(_("Disable Keyring/KWallet Unlocking"))
	confirm_dialog.props.text = _("Are you sure you want to disable keyring/KWallet unlocking for user {}?").format(self.active_user)
	response = confirm_dialog.run()
	confirm_dialog.destroy()

	if response != gtk.ResponseType.YES:
		return

	keyring_keys_dir = KEYRING_KEYS_DIR
	tpm_keys_dir = "/etc/ubuntu-hello/tpm-keys"
	pending_dir = "/etc/ubuntu-hello/keyring-caching-pending"
	
	key_file = os.path.join(keyring_keys_dir, self.active_user)
	pub_file = os.path.join(tpm_keys_dir, f"{self.active_user}.pub")
	priv_file = os.path.join(tpm_keys_dir, f"{self.active_user}.priv")
	pending_file = os.path.join(pending_dir, self.active_user)

	try:
		for path in (key_file, pub_file, priv_file, pending_file):
			if os.path.exists(path):
				os.unlink(path)
		
		# Success dialog
		success_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.INFO, buttons=gtk.ButtonsType.CLOSE)
		success_dialog.set_title(_("Success"))
		success_dialog.props.text = _("Keyring/KWallet unlocking disabled for user {}.").format(self.active_user)
		success_dialog.run()
		success_dialog.destroy()
	except Exception as e:
		error_dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		error_dialog.set_title(_("Error"))
		error_dialog.props.text = _("Failed to disable keyring unlocking: {}").format(str(e))
		error_dialog.run()
		error_dialog.destroy()

	self.update_keyring_status()
