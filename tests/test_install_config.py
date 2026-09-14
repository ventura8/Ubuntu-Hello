"""The install-time migration that gives an upgraded config the new strictness key.

`[video] confirmations` decides how many separate frames must agree before a face
match is trusted. A config written before the key existed has no value for it, so
without this migration every upgraded install would silently keep authenticating
on a single frame -- the best of the dozens of looks a scan takes.
"""
from __future__ import annotations

import configparser

import install_config
import tab_security


def _write(tmp_path, body):
	path = tmp_path / "config.ini"
	path.write_text(body, encoding="utf-8")
	return path


class TestLadderMatchesTheApp:
	def test_each_shipped_level_maps_to_its_own_frame_count(self):
		"""install_config cannot import the GTK module, so the table is duplicated.

		If the two ever drift, an upgrade quietly hands the user a different level
		from the one the Settings tab shows as selected.
		"""
		for name, certainty in tab_security.PRESETS.items():
			assert install_config.confirmations_for(certainty) == tab_security.CONFIRMATIONS[name], name

	def test_a_threshold_looser_than_any_level_still_gets_a_count(self):
		"""4.2 was the shipped default for a long time; those configs exist."""
		assert install_config.confirmations_for(4.2) == min(tab_security.CONFIRMATIONS.values())

	def test_a_stricter_threshold_never_asks_for_fewer_frames(self):
		strictest = min(tab_security.PRESETS.values())
		counts = [install_config.confirmations_for(c / 10.0)
		          for c in range(int(strictest * 10), 60)]
		assert counts == sorted(counts, reverse=True)

	def test_a_hand_tightened_threshold_is_left_on_a_single_frame(self):
		"""Below the strictest shipped level, piling on frames would break login.

		Measured on this project's IR hardware, only 2% of frames fall under 2.0
		with one enrolled model -- demanding four of them would never finish.
		"""
		assert install_config.confirmations_for(2.0) == 1
		assert install_config.confirmations_for(1.8) == 1


class TestMigration:
	def test_the_key_is_added_at_the_level_the_user_already_chose(self, tmp_path, capsys):
		path = _write(tmp_path, "[video]\ncertainty = 3.0\ntimeout = 8\n")
		install_config.add_missing_confirmations(str(path))
		parser = configparser.ConfigParser()
		parser.read(path, encoding="utf-8")
		assert parser.getint("video", "confirmations") == tab_security.CONFIRMATIONS["security_balanced"]
		assert parser.getfloat("video", "certainty") == 3.0     # their threshold is untouched

	def test_an_existing_value_is_never_overwritten(self, tmp_path):
		path = _write(tmp_path, "[video]\ncertainty = 3.0\nconfirmations = 9\n")
		install_config.add_missing_confirmations(str(path))
		parser = configparser.ConfigParser()
		parser.read(path, encoding="utf-8")
		assert parser.getint("video", "confirmations") == 9

	def test_comments_and_unrelated_settings_survive(self, tmp_path):
		path = _write(tmp_path, "[core]\n# keep me\ndisabled = false\n\n[video]\ncertainty = 2.6\n")
		install_config.add_missing_confirmations(str(path))
		text = path.read_text(encoding="utf-8")
		assert "# keep me" in text
		assert "disabled = false" in text
		assert "confirmations = " in text

	def test_a_config_with_no_video_section_is_not_a_crash(self, tmp_path):
		path = _write(tmp_path, "[core]\ndisabled = false\n")
		install_config.add_missing_confirmations(str(path))
		parser = configparser.ConfigParser()
		parser.read(path, encoding="utf-8")
		assert parser.getint("video", "confirmations") == install_config.confirmations_for(3.5)

	def test_an_unreadable_file_warns_instead_of_failing_the_install(self, tmp_path, capsys):
		install_config.add_missing_confirmations(str(tmp_path / "nope.ini"))
		assert "Warning" in capsys.readouterr().out
