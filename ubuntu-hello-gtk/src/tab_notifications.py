"""Settings -> Notifications tab: desktop cards for face-auth attempts.

Three switches map 1:1 onto ``[notifications]`` in /etc/ubuntu-hello/config.ini
(see ubuntu-hello/src/notify.py for what each does):

  enabled  - show the card at all
  sound    - play the notification sounds (service-login / dialog-warning)
  details  - append diagnostics (model, certainty/threshold, frames, pid, ...)

Writes go through config_edit.set_option so comments in the config survive.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk as gtk

import config_edit
import paths_factory
from i18n import _

SECTION = "notifications"

# switch widget id -> (config key, default)
SWITCHES = {
	"notifications_enabled_switch": ("enabled", True),
	"notifications_sound_switch": ("sound", True),
	"notifications_details_switch": ("details", False),
}


def load_notification_settings(self):
	"""Reflect the config file in the switches (without triggering writes)."""
	path = paths_factory.config_file_path()
	self._loading_notification_settings = True
	try:
		for widget_id, (key, default) in SWITCHES.items():
			switch = self.builder.get_object(widget_id)
			if switch is None:
				continue
			switch.set_active(config_edit.get_bool(path, SECTION, key, default))
		enabled = config_edit.get_bool(path, SECTION, "enabled", True)
		_set_dependent_sensitivity(self, enabled)
	finally:
		self._loading_notification_settings = False


def _set_dependent_sensitivity(self, enabled):
	"""Sound/details only matter while cards are shown at all."""
	for widget_id in ("notifications_sound_switch", "notifications_details_switch"):
		switch = self.builder.get_object(widget_id)
		if switch is not None:
			switch.set_sensitive(bool(enabled))


def _save(self, key, active):
	try:
		config_edit.set_option(paths_factory.config_file_path(), SECTION, key, "true" if active else "false")
	except OSError as err:
		dialog = gtk.MessageDialog(parent=self.window, flags=gtk.DialogFlags.MODAL, type=gtk.MessageType.ERROR, buttons=gtk.ButtonsType.CLOSE)
		dialog.set_title(_("Ubuntu Hello Error"))
		dialog.props.text = _("Could not save the notification setting")
		dialog.format_secondary_text(str(err))
		dialog.run()
		dialog.destroy()


def on_notifications_enabled_state_set(self, switch, state):
	if getattr(self, "_loading_notification_settings", False):
		return False
	_save(self, "enabled", state)
	_set_dependent_sensitivity(self, state)
	return False


def on_notifications_sound_state_set(self, switch, state):
	if getattr(self, "_loading_notification_settings", False):
		return False
	_save(self, "sound", state)
	return False


def on_notifications_details_state_set(self, switch, state):
	if getattr(self, "_loading_notification_settings", False):
		return False
	_save(self, "details", state)
	return False
