"""Unit tests for the Settings -> Security tab's strictness levels.

The tab and the setup wizard write the same two keys, so they have to agree on
what each level means; a drift between them would silently change a user's
security when they next touched either screen.
"""
from __future__ import annotations

import configparser
from unittest.mock import MagicMock

import onboarding
import tab_security


class TestLevelsAreConsistent:
	def test_the_tab_and_the_wizard_describe_the_same_levels(self):
		assert sorted(tab_security.PRESETS.values()) == sorted(onboarding.SECURITY_PRESETS.values())
		assert sorted(tab_security.CONFIRMATIONS.values()) == sorted(onboarding.SECURITY_CONFIRMATIONS.values())

	def test_every_level_has_both_halves(self):
		assert set(tab_security.PRESETS) == set(tab_security.CONFIRMATIONS)

	def test_secure_is_the_strictest_on_both_counts(self):
		assert tab_security.DEFAULT_PRESET == "security_secure"
		assert tab_security.PRESETS["security_secure"] < tab_security.PRESETS["security_balanced"]
		assert tab_security.CONFIRMATIONS["security_secure"] > tab_security.CONFIRMATIONS["security_balanced"]

	def test_every_level_asks_several_frames_to_agree(self):
		"""One frame under the bar is the best of dozens of draws, not evidence."""
		assert min(tab_security.CONFIRMATIONS.values()) > 1

	def test_no_level_is_tight_enough_to_reject_its_own_user(self):
		"""Secure at 1.8 matched no frame at all once the light changed.

		Measured logins on this project's IR hardware only became reliable from
		about 2.4 upwards with a single enrolled model.
		"""
		assert min(tab_security.PRESETS.values()) >= 2.4

	def test_no_level_is_looser_than_dlib_calls_the_same_person(self):
		"""dlib's own reference for "same person" is 6.0; every level stays inside it."""
		assert max(tab_security.PRESETS.values()) < 6.0


class TestPresetForCertainty:
	def test_exact_values_map_to_their_own_level(self):
		for name, certainty in tab_security.PRESETS.items():
			assert tab_security.preset_for_certainty(certainty) == name

	def test_a_hand_edited_value_falls_to_the_nearest_level(self):
		assert tab_security.preset_for_certainty(1.9) == "security_secure"
		assert tab_security.preset_for_certainty(4.2) == "security_fast"
		assert tab_security.preset_for_certainty(2.9) == "security_balanced"


class TestWritingALevel:
	def _window(self, path):
		window = MagicMock()
		window.builder.get_object.side_effect = lambda name: None
		window._loading_security_settings = False
		return window

	def test_choosing_a_level_writes_the_threshold_and_the_frame_count(self, tmp_path, monkeypatch):
		path = tmp_path / "config.ini"
		path.write_text("[video]\ncertainty = 3.5\nconfirmations = 1\n", encoding="utf-8")
		monkeypatch.setattr(tab_security.paths_factory, "config_file_path", lambda: str(path))

		secure = MagicMock()
		secure.get_active.return_value = True
		win = MagicMock()
		win._loading_security_settings = False
		win.builder.get_object.side_effect = lambda name: secure if name == "security_secure" else None

		tab_security.on_security_preset_toggled(win, secure)

		parser = configparser.ConfigParser()
		parser.read(path, encoding="utf-8")
		assert parser.getfloat("video", "certainty") == tab_security.PRESETS["security_secure"]
		assert parser.getint("video", "confirmations") == tab_security.CONFIRMATIONS["security_secure"]

	def test_the_button_being_switched_off_writes_nothing(self, tmp_path, monkeypatch):
		path = tmp_path / "config.ini"
		path.write_text("[video]\ncertainty = 3.5\nconfirmations = 1\n", encoding="utf-8")
		monkeypatch.setattr(tab_security.paths_factory, "config_file_path", lambda: str(path))

		leaving = MagicMock()
		leaving.get_active.return_value = False
		win = MagicMock()
		win._loading_security_settings = False

		tab_security.on_security_preset_toggled(win, leaving)
		assert "certainty = 3.5" in path.read_text(encoding="utf-8")


class TestLivenessRule:
	def test_the_rule_fails_closed(self):
		"""In Howdy's inherited vocabulary `faildeadly` authenticates on a timeout.

		Writing that rule would let a user who never nods straight through, which is
		exactly the bug manual testing found.
		"""
		assert "failsafe" in tab_security.LIVENESS_RULE
		assert "faildeadly" not in tab_security.LIVENESS_RULE
