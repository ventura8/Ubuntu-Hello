"""Draw translated text onto a video frame.

`cv2.putText` can only use OpenCV's built-in Hershey fonts, which are Latin-only
vector strokes: any character outside ASCII is drawn as a question mark. The live
camera test and the snapshot both label frames with translatable strings, so for
anyone using a non-Latin script the overlay read as rows of question marks.

Text is therefore drawn with a real font through Pillow, which is only reached when
the string actually needs it. Pure ASCII keeps going through `cv2.putText`: it is
faster, and it keeps the existing overlay pixel-identical. If Pillow is missing, or
fontconfig cannot offer a face that covers the text, the Hershey path is used anyway
-- a degraded label is better than a camera test that refuses to run.
"""

import os
import re
import subprocess

import cv2
import numpy as np

# Rough pixels-per-unit for the Hershey font, used to match Pillow's size to the
# `scale` callers already pass to cv2.putText so the two paths look alike.
HERSHEY_PIXELS_PER_SCALE = 44.0
FC_MATCH = "/usr/bin/fc-match"

_font_cache = {}
_ASCII_ONLY = re.compile(r"^[\x00-\x7f]*$")


def needs_real_font(text):
	"""True when the Hershey font cannot draw this string."""
	return not _ASCII_ONLY.match(text or "")


def _script_tag(text):
	"""A fontconfig language hint for the first non-ASCII character in *text*."""
	for char in text:
		code = ord(char)
		if code < 0x80:
			continue
		for start, end, lang in (
			(0x0370, 0x03FF, "el"), (0x0400, 0x04FF, "ru"), (0x0530, 0x058F, "hy"),
			(0x0590, 0x05FF, "he"), (0x0600, 0x06FF, "ar"), (0x0700, 0x074F, "syr"),
			(0x0780, 0x07BF, "dv"), (0x0900, 0x097F, "hi"), (0x0980, 0x09FF, "bn"),
			(0x0A00, 0x0A7F, "pa"), (0x0A80, 0x0AFF, "gu"), (0x0B00, 0x0B7F, "or"),
			(0x0B80, 0x0BFF, "ta"), (0x0C00, 0x0C7F, "te"), (0x0C80, 0x0CFF, "kn"),
			(0x0D00, 0x0D7F, "ml"), (0x0D80, 0x0DFF, "si"), (0x0E00, 0x0E7F, "th"),
			(0x0E80, 0x0EFF, "lo"), (0x0F00, 0x0FFF, "bo"), (0x1000, 0x109F, "my"),
			(0x10A0, 0x10FF, "ka"), (0x1200, 0x137F, "am"), (0x1780, 0x17FF, "km"),
			(0x3040, 0x30FF, "ja"), (0x4E00, 0x9FFF, "zh"), (0xAC00, 0xD7AF, "ko"),
		):
			if start <= code <= end:
				return lang
		return None
	return None


def _font_path(text):
	"""A font file that covers *text*, asked of fontconfig, or None."""
	lang = _script_tag(text)
	if lang in _font_cache:
		return _font_cache[lang]
	path = None
	try:
		pattern = ":lang=%s" % lang if lang else "DejaVu Sans"
		result = subprocess.run([FC_MATCH, "-f", "%{file}", pattern],
		                        capture_output=True, text=True, timeout=5)
		candidate = result.stdout.strip()
		if candidate and os.path.isfile(candidate):
			path = candidate
	except (OSError, subprocess.SubprocessError):
		path = None
	_font_cache[lang] = path
	return path


def _hershey(image, text, origin, scale, color, thickness):
	cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)
	return image


def draw_text(image, text, origin, scale, color, thickness=0):
	"""Draw *text* at *origin* (the baseline-left point cv2.putText expects).

	Returns the image. Colour is BGR, as everywhere else in the capture path.
	"""
	if not text or not needs_real_font(text):
		return _hershey(image, text, origin, scale, color, thickness)

	path = _font_path(text)
	if path is None:
		return _hershey(image, text, origin, scale, color, thickness)

	try:
		from PIL import Image, ImageDraw, ImageFont
	except ImportError:
		return _hershey(image, text, origin, scale, color, thickness)

	try:
		size = max(8, int(round(scale * HERSHEY_PIXELS_PER_SCALE)))
		font = ImageFont.truetype(path, size)
		canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
		draw = ImageDraw.Draw(canvas)
		# cv2 puts the baseline at `origin`; Pillow measures from the top, so the
		# ascent is subtracted to land the glyphs in the same place.
		ascent = font.getmetrics()[0]
		draw.text((origin[0], origin[1] - ascent), text,
		          font=font, fill=(color[2], color[1], color[0]))
		image[:] = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
		return image
	except Exception:
		return _hershey(image, text, origin, scale, color, thickness)
