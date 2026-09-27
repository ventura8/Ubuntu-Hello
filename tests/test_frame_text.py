"""Translated labels drawn onto a video frame must be readable, not question marks.

`cv2.putText` only has OpenCV's Hershey fonts, which are Latin-only strokes, so
every non-ASCII character came out as a question mark. The camera test and the
snapshot both label frames with translatable strings ("SCAN FRAME", "DARK FRAME",
"SLOW MODE", "FRAMES: %d"), so for anyone reading a non-Latin script the overlay
was a row of question marks.
"""
from __future__ import annotations

import sys
import types

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


class TestFontLookup:
	@pytest.fixture(autouse=True)
	def _fresh_cache(self, monkeypatch):
		monkeypatch.setattr(frame_text, "_font_cache", {})

	def test_latin_accents_have_no_script_hint(self):
		"""Latin-1 letters are outside every script range: no fontconfig :lang."""
		assert frame_text._script_tag("Videobild für Modell") is None

	def test_fontconfig_answer_is_used_and_cached_per_script(self, monkeypatch, tmp_path):
		font = tmp_path / "NotoSansArabic-Regular.ttf"
		font.write_bytes(b"\0")
		calls = []

		def fake_run(cmd, **kwargs):
			calls.append(cmd)
			return frame_text.subprocess.CompletedProcess(cmd, 0, stdout=str(font) + "\n")

		monkeypatch.setattr(frame_text.subprocess, "run", fake_run)
		assert frame_text._font_path(ARABIC) == str(font)
		assert frame_text._font_path("SCAN " + ARABIC) == str(font)
		assert calls == [[frame_text.FC_MATCH, "-f", "%{file}", ":lang=ar"]]

	def test_unhinted_text_asks_for_a_general_purpose_face(self, monkeypatch, tmp_path):
		font = tmp_path / "DejaVuSans.ttf"
		font.write_bytes(b"\0")
		calls = []

		def fake_run(cmd, **kwargs):
			calls.append(cmd[-1])
			return frame_text.subprocess.CompletedProcess(cmd, 0, stdout=str(font))

		monkeypatch.setattr(frame_text.subprocess, "run", fake_run)
		assert frame_text._font_path("für") == str(font)
		assert calls == ["DejaVu Sans"]

	def test_a_font_path_that_does_not_exist_is_not_trusted(self, monkeypatch, tmp_path):
		monkeypatch.setattr(frame_text.subprocess, "run",
		                    lambda cmd, **kw: frame_text.subprocess.CompletedProcess(
		                        cmd, 0, stdout=str(tmp_path / "gone.ttf")))
		assert frame_text._font_path(GREEK) is None
		assert frame_text._font_cache == {"el": None}


class _FakeFont:
	def __init__(self, path, size):
		self.path, self.size = path, size

	def getmetrics(self):
		return (12, 3)


class _FakeCanvas:
	def __init__(self, array):
		self.array = np.array(array)

	def __array__(self, dtype=None, copy=None):
		return self.array


class _FakeDraw:
	calls = []

	def __init__(self, canvas):
		self.canvas = canvas

	def text(self, xy, text, font=None, fill=None):
		_FakeDraw.calls.append((xy, text, font, fill))
		self.canvas.array[:] = 7            # "ink" the whole canvas


def _fake_pil(monkeypatch):
	image_mod = types.SimpleNamespace(fromarray=_FakeCanvas)
	draw_mod = types.SimpleNamespace(Draw=_FakeDraw)
	font_mod = types.SimpleNamespace(truetype=_FakeFont)
	pil = types.ModuleType("PIL")
	pil.Image, pil.ImageDraw, pil.ImageFont = image_mod, draw_mod, font_mod
	monkeypatch.setitem(sys.modules, "PIL", pil)
	_FakeDraw.calls = []
	# OpenCV is a MagicMock in the unit suite; a real channel swap stands in.
	fake_cv2 = types.SimpleNamespace(
		cvtColor=lambda img, code: np.asarray(img)[..., ::-1].copy(),
		COLOR_BGR2RGB=4, COLOR_RGB2BGR=5,
		putText=lambda *a, **k: None, FONT_HERSHEY_SIMPLEX=0, LINE_AA=16)
	monkeypatch.setattr(frame_text, "cv2", fake_cv2)


class TestRealFontPath:
	def test_non_latin_text_is_drawn_through_pillow(self, monkeypatch):
		_fake_pil(monkeypatch)
		monkeypatch.setattr(frame_text, "_font_path", lambda text: "/fonts/arabic.ttf")
		image = _blank()
		result = frame_text.draw_text(image, ARABIC, (10, 50), 0.5, (255, 128, 0))
		assert result is image
		(xy, text, font, fill), = _FakeDraw.calls
		assert text == ARABIC
		assert xy == (10, 50 - 12)          # baseline -> top: ascent subtracted
		assert fill == (0, 128, 255)        # BGR in, RGB out
		assert (font.path, font.size) == ("/fonts/arabic.ttf", 22)
		assert np.all(image == 7)           # painted pixels copied back in place

	def test_tiny_scales_keep_a_legible_minimum_size(self, monkeypatch):
		_fake_pil(monkeypatch)
		monkeypatch.setattr(frame_text, "_font_path", lambda text: "/fonts/greek.ttf")
		frame_text.draw_text(_blank(), GREEK, (0, 20), 0.05, (0, 0, 0))
		assert _FakeDraw.calls[0][2].size == 8

	def test_missing_pillow_falls_back_to_hershey(self, monkeypatch):
		drawn = []
		monkeypatch.setitem(sys.modules, "PIL", None)
		monkeypatch.setattr(frame_text, "_font_path", lambda text: "/fonts/arabic.ttf")
		monkeypatch.setattr(frame_text, "_hershey", lambda *args: drawn.append(args) or args[0])
		image = _blank()
		assert frame_text.draw_text(image, ARABIC, (10, 50), 0.4, (0, 255, 0), 2) is image
		assert drawn == [(image, ARABIC, (10, 50), 0.4, (0, 255, 0), 2)]
