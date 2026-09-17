"""Settings -> Security tab: how strict the face match is, and liveness.

Two independent controls, both written to /etc/ubuntu-hello/config.ini through the
comment-preserving editor (`ubuntu-hello set` takes a bare key and cannot address a
section):

  [video] certainty        - Euclidean distance on dlib's 128-d face descriptor, x10.
                             LOWER is stricter; dlib's own "same person" reference is
                             6.0, so every preset here is well inside that.
  [video] confirmations    - separate frames that must agree before the match counts.
                             A scan takes dozens of looks, so at 1 the deciding frame
                             is the best of many tries; asking for several removes the
                             lucky outlier without making the threshold itself harsher.
  [rubberstamps] enabled   - ask for a nod after the face matches. A photo held up to
                             the camera cannot nod (it does not stop a video replay,
                             which is the documented limit of challenge-response).

The setup wizard writes the same values (see onboarding.SECURITY_PRESETS); this tab
lets the user change them afterwards, and unlike the wizard it keeps the two settings
independent so "Balanced plus a nod" is possible.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk as gtk
import gtk4compat

import config_edit
import paths_factory
from i18n import _

VIDEO_SECTION = "video"
STAMP_SECTION = "rubberstamps"

# radio widget id -> certainty written for it. Same numbers as the wizard.
PRESETS = {
	"security_fast": 3.5,
	"security_balanced": 3.0,
	"security_secure": 2.6,
}
# radio widget id -> agreeing frames demanded for it. Every level asks for more
# than one, and a stricter level asks for more. This is where the levels tighten,
# rather than by lowering `certainty` further: on real IR hardware the distance to
# the same face drifts by about 0.4 between lighting conditions with one enrolled
# model, so a harsher threshold buys its strictness by refusing the real user,
# while another agreeing frame costs a fraction of a second. The looser the
# threshold, the more a level needs this -- Fast lets far more frames through,
# so a lone outlier frame matters most there.
CONFIRMATIONS = {
	"security_fast": 2,
	"security_balanced": 3,
	"security_secure": 4,
}
DEFAULT_PRESET = "security_secure"

# The rule's third field is the failure mode, in the vocabulary inherited from Howdy,
# where the names are the reverse of what they suggest:
#   failsafe   -- the check must pass; a timeout ABORTS authentication (fail closed)
#   faildeadly -- a timeout lets authentication through anyway (fail open)
LIVENESS_RULE = "nod\t5s\tfailsafe\tmin_distance=12"


def _read_certainty(path):
	try:
		import configparser
		parser = configparser.ConfigParser()
		parser.read(path, encoding="utf-8")
		return parser.getfloat(VIDEO_SECTION, "certainty", fallback=4.2)
	except Exception:
		return 4.2


def preset_for_certainty(certainty):
	"""The radio that best represents *certainty* (the config may hold any value)."""
	return min(PRESETS, key=lambda name: abs(PRESETS[name] - certainty))


def load_security_settings(self):
	"""Reflect the config file in the radios and the switch (without writing back)."""
	path = paths_factory.config_file_path()
	self._loading_security_settings = True
	try:
		active = preset_for_certainty(_read_certainty(path))
		for name in PRESETS:
			button = self.builder.get_object(name)
			if button is not None:
				button.set_active(name == active)
		switch = self.builder.get_object("security_liveness_switch")
		if switch is not None:
			switch.set_active(config_edit.get_bool(path, STAMP_SECTION, "enabled", False))
	finally:
		self._loading_security_settings = False


def _save(self, section, key, value):
	try:
		config_edit.set_option(paths_factory.config_file_path(), section, key, value)
		return True
	except OSError as err:
		gtk4compat.alert(self.window, _("Could not save the security setting"), str(err))
		return False


def on_security_preset_toggled(self, button):
	"""One of the strictness radios was chosen: write its certainty."""
	if getattr(self, "_loading_security_settings", False):
		return
	# GTK emits ``toggled`` for the button being switched off as well.
	if not button.get_active():
		return
	for name, certainty in PRESETS.items():
		widget = self.builder.get_object(name)
		if widget is button or (widget is not None and widget.get_active()):
			_save(self, VIDEO_SECTION, "certainty", certainty)
			_save(self, VIDEO_SECTION, "confirmations", CONFIRMATIONS.get(name, 1))
			return


def on_security_liveness_state_set(self, switch, state):
	if getattr(self, "_loading_security_settings", False):
		return False
	if state:
		# Write the rule as well: the shipped default is fail-open on timeout, which
		# would authenticate a user who never nods.
		_save(self, STAMP_SECTION, "stamp_rules", LIVENESS_RULE)
	_save(self, STAMP_SECTION, "enabled", "true" if state else "false")
	return False
