"""Translated labels drawn onto a video frame must be readable, not question marks.

`cv2.putText` only has OpenCV's Hershey fonts, which are Latin-only strokes, so
every non-ASCII character came out as a question mark. The camera test and the
snapshot both label frames with translatable strings ("SCAN FRAME", "DARK FRAME",
"SLOW MODE", "FRAMES: %d"), so for anyone reading a non-Latin script the overlay
was a row of question marks.
"""
from __future__ import annotations

import numpy as np
import pytest

import frame_text

ARABIC = "إطار المسح"
GREEK = "ΣΑΡΩΣΗ"
JAPANESE = "スキャン"


def _blank():
	return np.zeros((80, 320, 3), dtype=np.uint8)


def _ink(image):
	"""How many pixels were painted."""
	return int(np.count_nonzero(image))


class TestWhichPathIsTaken:
	def test_ascii_does_not_need_a_real_font(self):
		assert not frame_text.needs_real_font("SCAN FRAME")
		assert not frame_text.needs_real_font("")

	@pytest.mark.parametrize("text", [ARABIC, GREEK, JAPANESE, "Videobild für Modell"])
	def test_anything_beyond_ascii_does(self, text):
		assert frame_text.needs_real_font(text)

	def test_the_script_hint_follows_the_first_non_ascii_character(self):
		assert frame_text._script_tag("SCAN " + ARABIC) == "ar"
		assert frame_text._script_tag(GREEK) == "el"
		assert frame_text._script_tag(JAPANESE) == "ja"
		assert frame_text._script_tag("plain ascii") is None


class TestFallbacks:
	def test_a_missing_font_falls_back_instead_of_failing(self, monkeypatch):
		monkeypatch.setattr(frame_text, "_font_path", lambda text: None)
		image = _blank()
		assert frame_text.draw_text(image, ARABIC, (10, 50), 0.4, (0, 255, 0)) is image

	def test_a_broken_font_file_falls_back_instead_of_failing(self, monkeypatch, tmp_path):
		"""A camera test must never refuse to run because a label could not be drawn."""
		broken = tmp_path / "broken.ttf"
		broken.write_bytes(b"not a font")
		monkeypatch.setattr(frame_text, "_font_path", lambda text: str(broken))
		image = _blank()
		assert frame_text.draw_text(image, ARABIC, (10, 50), 0.4, (0, 255, 0)) is image

	def test_fontconfig_being_absent_is_not_fatal(self, monkeypatch):
		monkeypatch.setattr(frame_text, "FC_MATCH", "/nonexistent/fc-match")
		monkeypatch.setattr(frame_text, "_font_cache", {})
		assert frame_text._font_path(ARABIC) is None

	def test_empty_text_is_a_no_op_not_a_crash(self):
		image = _blank()
		frame_text.draw_text(image, "", (10, 50), 0.4, (0, 255, 0))
		assert _ink(image) == 0
