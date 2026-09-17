"""Real-OpenCV rendering of translated labels drawn onto a video frame.

The pure logic lives in tests/test_frame_text.py; this needs a real cv2, which the
unit suite replaces with a mock, so it runs here. Run with:

    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_frame_text_e2e.py
"""
from __future__ import annotations

import os

import numpy as np
import pytest

if os.environ.get("UH_REAL_GTK") != "1":
	pytest.skip("requires UH_REAL_GTK=1 for a real cv2", allow_module_level=True)

import frame_text

ARABIC = "إطار المسح"
GREEK = "ΣΑΡΩΣΗ"
JAPANESE = "スキャン"


def _blank():
	return np.zeros((80, 320, 3), dtype=np.uint8)


def _ink(image):
	"""How many pixels were painted."""
	return int(np.count_nonzero(image))


class TestRendering:
	def test_ascii_still_draws(self):
		image = _blank()
		frame_text.draw_text(image, "SCAN FRAME", (10, 40), 0.4, (0, 255, 0))
		assert _ink(image) > 0

	@pytest.mark.parametrize("text", [ARABIC, GREEK, JAPANESE])
	def test_non_latin_draws_real_glyphs_not_question_marks(self, text):
		"""The Hershey path would paint identical question marks for all three."""
		pytest.importorskip("PIL")
		if frame_text._font_path(text) is None:
			pytest.skip("no font on this system covers " + text)
		drawn = _blank()
		frame_text.draw_text(drawn, text, (10, 50), 0.4, (0, 255, 0))
		assert _ink(drawn) > 0, "nothing was drawn at all"

		fallback = _blank()
		frame_text._hershey(fallback, text, (10, 50), 0.4, (0, 255, 0), 0)
		assert not np.array_equal(drawn, fallback), (
			"non-Latin text rendered the same as the Latin-only font would, "
			"which means it is still question marks")

	def test_different_scripts_render_differently(self):
		"""Guards the real failure: every script collapsing to the same glyphs."""
		pytest.importorskip("PIL")
		images = []
		for text in (ARABIC, GREEK, JAPANESE):
			if frame_text._font_path(text) is None:
				pytest.skip("no font for " + text)
			image = _blank()
			frame_text.draw_text(image, text, (10, 50), 0.4, (0, 255, 0))
			images.append(image)
		assert not np.array_equal(images[0], images[1])
		assert not np.array_equal(images[1], images[2])
