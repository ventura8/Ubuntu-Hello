"""Recorded-footage tier: the real recognition loop on frames from the real camera.

Every other test shape either never touches recognition or scripts its outcome:
the unit suite mocks dlib and OpenCV outright, and the real-GTK suite still mocks
dlib. That left the part of the product that decides who gets in with no automated
coverage, which is where this session's manual-only bugs lived: the threshold that
matched zero frames, the nod detected only in its first two seconds, the drift
between lighting conditions.

Two kinds of fixture, both recorded once from the real camera with record.py:

* ``<clip>.npz``          pixels. A face, so never committed. Replayed through the
                          real dlib detector, landmark predictor and encoder.
* ``<clip>.signals.npz``  what deidentify.py keeps of those pixels: per frame, was it
                          too dark, was one face found and where, its five landmark
                          points, its 128-number descriptor. No image can be rebuilt
                          from it. Replayed through compare.py's real loop and the
                          real nod stamp, with dlib's numeric work played back.

Pixels are preferred when present. Everything here skips cleanly with neither.

Run with:
    UH_REAL_DLIB=1 pytest tests/footage/
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

if os.environ.get("UH_REAL_DLIB") != "1":
	pytest.skip("requires UH_REAL_DLIB=1 (real OpenCV; real dlib for pixel clips)", allow_module_level=True)

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CLIPS = ("still", "nod", "away")
REAL_FOOTAGE = ROOT / "tests" / "fixtures" / "footage"             # recorded, never committed
SYNTHETIC_FOOTAGE = ROOT / "tests" / "fixtures" / "footage-synthetic"   # generated, committed


def _has_all_clips(directory):
	return all((directory / f"{c}.npz").is_file() or (directory / f"{c}.signals.npz").is_file() for c in CLIPS)


def _footage_dir():
	override = os.environ.get("UH_FOOTAGE_DIR")
	if override:
		return Path(override)
	if _has_all_clips(REAL_FOOTAGE):
		return REAL_FOOTAGE
	return SYNTHETIC_FOOTAGE


FOOTAGE = _footage_dir()
IS_SYNTHETIC = FOOTAGE.resolve() == SYNTHETIC_FOOTAGE.resolve()
PIXEL_MODE = any((FOOTAGE / f"{c}.npz").is_file() for c in CLIPS)

# Signal replay never calls into dlib: its numeric work is played back per frame.
# compare.py still imports the module by name, so on a machine without it (the CI
# images ship OpenCV but not dlib) a stand-in with the three constructors keeps
# the import satisfied. Pixel clips need the real library and skip without it.
import importlib.util
import sys
import types

if importlib.util.find_spec("dlib") is None and not PIXEL_MODE:
	_stub = types.ModuleType("dlib")
	_stub.get_frontal_face_detector = lambda: None
	_stub.shape_predictor = lambda path: None
	_stub.face_recognition_model_v1 = lambda path: None
	_stub.cnn_face_detection_model_v1 = lambda path: None
	sys.modules["dlib"] = _stub
DARK_THRESHOLD = 60.0

DLIB_DATA_CANDIDATES = ("/usr/share/dlib-data", "/usr/share/ubuntu-hello/dlib-data", "/usr/lib/ubuntu-hello/dlib-data")


def _dlib_data_dir():
	for candidate in DLIB_DATA_CANDIDATES:
		if os.path.isfile(os.path.join(candidate, "shape_predictor_5_face_landmarks.dat")):
			return candidate
	return None


def _clip_path(name):
	pixels = FOOTAGE / f"{name}.npz"
	signals = FOOTAGE / f"{name}.signals.npz"
	if pixels.is_file():
		return pixels, "pixels"
	if signals.is_file():
		return signals, "signals"
	return None, None


def pytest_collection_modifyitems(config, items):
	missing = [c for c in CLIPS if _clip_path(c)[0] is None]
	modes = {_clip_path(c)[1] for c in CLIPS if _clip_path(c)[0] is not None}
	reason = None
	if missing:
		reason = "footage missing under %s: %s (record with tests/footage/record.py)" % (FOOTAGE, ", ".join(missing))
	elif "pixels" in modes and _dlib_data_dir() is None:
		reason = "pixel clips need the dlib model files; none found in " + ", ".join(DLIB_DATA_CANDIDATES)
	elif "pixels" in modes and getattr(__import__("dlib"), "get_frontal_face_detector", None) is None:
		reason = "pixel clips need the real dlib library"
	if reason:
		marker = pytest.mark.skip(reason=reason)
		for item in items:
			if "tests/footage" in str(item.fspath):
				item.add_marker(marker)
		return
	if IS_SYNTHETIC:
		# Synthetic signals exercise the loop, the gate and the nod stamp. What they
		# cannot say is anything about a real camera, which is all calibration is.
		marker = pytest.mark.skip(reason="calibration is about real footage; running on the synthetic fixture")
		for item in items:
			if "calibration" in item.name.lower() or "Calibration" in item.nodeid:
				item.add_marker(marker)


class Clip:
	"""One recorded clip, from pixels or from signals."""

	def __init__(self, name):
		path, self.mode = _clip_path(name)
		z = np.load(path)
		self.name = name
		self.t = z["t"]
		if self.mode == "pixels":
			self.frames = z["frames"]
			self.height, self.width = int(self.frames.shape[1]), int(self.frames.shape[2])
		else:
			self.height, self.width = int(z["height"]), int(z["width"])
			self.dark, self.faces, self.rect = z["dark"], z["faces"], z["rect"]
			self.landmarks, self.descriptor = z["landmarks"], z["descriptor"]
			# Stand-in frames whose only property is their darkness: black for a
			# strobe frame (compare.py skips it), mid-grey for a usable one.
			level = np.where(self.dark, 0, 128).astype(np.uint8)
			self.frames = np.broadcast_to(level[:, None, None, None], (len(level), self.height, self.width, 3)).copy()
		self.n = len(self.t)


class ReplayCapture:
	"""Stands in for recorders.video_capture.VideoCapture, replaying a clip.

	Frames come back in recorded order and wrap around when the clip runs out, so
	a scan that never matches runs until compare.py's own timeout exactly as on a
	live camera that keeps delivering frames.

	The replay objects need to know which frame the loop is looking at. compare.py
	and the nod stamp both read a frame, then detect, then predict, then encode,
	strictly in that order on one thread, so "the last frame read" is exact. That
	is shared through *registry*, a dict the replay objects were built with. It is
	passed explicitly rather than kept at module level: pytest and a plain import
	can load this file as two different module objects, and a module-level slot
	then splits into two, leaving the fakes replaying a stale frame forever.
	"""

	def __init__(self, clip, registry=None, loop=True):
		import cv2
		self._cv2 = cv2
		self.clip = clip
		self.loop = loop
		self.reads = 0
		self.last_index = -1
		self.fw = clip.width
		self.internal = self
		if registry is not None:
			registry["capture"] = self

	def get(self, prop):
		if prop == self._cv2.CAP_PROP_FRAME_HEIGHT:
			return float(self.clip.height)
		if prop == self._cv2.CAP_PROP_FRAME_WIDTH:
			return float(self.clip.width)
		return 0.0

	def read_frame(self):
		self.reads += 1
		if not self.loop and self.reads > self.clip.n:
			# Clip exhausted: deliver black frames, which compare.py skips, so the
			# scan runs on to its own timeout with no further chance to match.
			self.last_index = -1
			black = np.zeros((self.clip.height, self.clip.width, 3), dtype=np.uint8)
			return black, black[:, :, 0].copy()
		self.last_index = (self.reads - 1) % self.clip.n
		frame = self.clip.frames[self.last_index]
		return frame, self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)

	def release(self):
		pass


# --- dlib stand-ins for signal replay ---------------------------------------

class _Point:
	__slots__ = ("x", "y")

	def __init__(self, x, y):
		self.x, self.y = int(x), int(y)


class _Landmarks:
	def __init__(self, points):
		self._points = [_Point(x, y) for x, y in points]

	def part(self, i):
		return self._points[i]


class _Rect:
	def __init__(self, l, t, r, b):
		self._v = (int(l), int(t), int(r), int(b))

	def left(self):
		return self._v[0]

	def top(self):
		return self._v[1]

	def right(self):
		return self._v[2]

	def bottom(self):
		return self._v[3]


class _Replay:
	def __init__(self, registry):
		self._registry = registry

	def _index(self):
		capture = self._registry.get("capture")
		assert capture is not None and capture.last_index >= 0, "detector called before any frame was read"
		return capture.clip, capture.last_index


class ReplayDetector(_Replay):
	def __call__(self, gsframe, upsample=0):
		clip, i = self._index()
		return [_Rect(*clip.rect[i])] * int(clip.faces[i])


class ReplayPredictor(_Replay):
	def __call__(self, frame, rect):
		clip, i = self._index()
		return _Landmarks(clip.landmarks[i])


class ReplayEncoder(_Replay):
	def compute_face_descriptor(self, frame, landmarks, jitters=1):
		clip, i = self._index()
		return clip.descriptor[i].astype(float).tolist()


# --- fixtures ------------------------------------------------------------------

@pytest.fixture(scope="session")
def dlib_data_dir():
	return _dlib_data_dir() or "/nonexistent/dlib-data"


@pytest.fixture(scope="session")
def footage():
	return {name: Clip(name) for name in CLIPS}


@pytest.fixture(scope="session")
def pipeline(footage, dlib_data_dir):
	"""detect / predict / encode / clahe: real dlib on pixels, replay on signals."""
	import cv2
	clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
	registry = {}
	if all(c.mode == "pixels" for c in footage.values()):
		import dlib
		return {
			"mode": "pixels",
			"detector": dlib.get_frontal_face_detector(),
			"predictor": dlib.shape_predictor(os.path.join(dlib_data_dir, "shape_predictor_5_face_landmarks.dat")),
			"encoder": dlib.face_recognition_model_v1(os.path.join(dlib_data_dir, "dlib_face_recognition_resnet_model_v1.dat")),
			"clahe": clahe,
			"registry": registry,
			"capture": lambda clip, loop=True: ReplayCapture(clip, registry, loop),
		}
	return {"mode": "signals", "detector": ReplayDetector(registry), "predictor": ReplayPredictor(registry),
	        "encoder": ReplayEncoder(registry), "clahe": clahe, "registry": registry,
	        "capture": lambda clip, loop=True: ReplayCapture(clip, registry, loop)}


def per_frame_descriptors(clip, pipeline):
	"""Descriptor of every usable single-face frame, via the same steps compare.py takes."""
	import cv2
	capture = pipeline["capture"](clip)
	out = []
	for _ in range(clip.n):
		frame, gs = capture.read_frame()
		gs = pipeline["clahe"].apply(gs)
		hist = cv2.calcHist([gs], [0], None, [8], [0, 256])
		total = float(hist.sum())
		if total == 0 or hist[0] / total * 100 > DARK_THRESHOLD:
			continue
		faces = pipeline["detector"](gs, 1)
		if len(faces) != 1:
			continue
		shape = pipeline["predictor"](frame, faces[0])
		out.append(np.array(pipeline["encoder"].compute_face_descriptor(frame, shape, 1)))
	return out


@pytest.fixture(scope="session")
def descriptors_of(pipeline):
	"""clip -> descriptor of every usable single-face frame, through the active pipeline."""
	return lambda clip: per_frame_descriptors(clip, pipeline)


@pytest.fixture(scope="session")
def enrolled_model(footage, pipeline):
	"""A face model built from the "still" clip by the real guided enrollment.

	enroll_capture.capture_guided_samples is driven exactly as `ubuntu-hello add`
	drives it, with the clip looping under a virtual clock that advances one frame
	interval per read. The clip holds one pose, so the six-pose walk collects fewer
	than the maximum and the duplicate filter does real work.
	"""
	import cv2
	import enroll_capture
	clip = footage["still"]
	interval = float(clip.t[-1] - clip.t[0]) / max(1, clip.n - 1)
	capture = pipeline["capture"](clip)
	state = {"clock": 0.0}

	def read_frame():
		frame, gs = capture.read_frame()
		state["clock"] += interval
		gs = pipeline["clahe"].apply(gs)
		hist = cv2.calcHist([gs], [0], None, [8], [0, 256])
		total = float(hist.sum())
		if total == 0 or hist[0] / total * 100 > DARK_THRESHOLD:
			return frame, None
		return frame, gs

	def detect(gs):
		return pipeline["detector"](gs, 1)

	def encode(frame, face):
		return list(pipeline["encoder"].compute_face_descriptor(frame, pipeline["predictor"](frame, face), 1))

	first = None
	while first is None:
		frame, gs = read_frame()
		if gs is None:
			continue
		faces = detect(gs)
		if len(faces) == 1:
			first = encode(frame, faces[0])

	samples = enroll_capture.capture_guided_samples(
		read_frame, detect, encode, emit=lambda line: None, first_sample=first,
		clock=lambda: state["clock"], sleep=None)
	return [{"time": 0, "label": "Footage still", "id": 0, "data": samples}]


@pytest.fixture
def model_file(tmp_path, enrolled_model):
	path = tmp_path / "alice.dat"
	path.write_text(json.dumps(enrolled_model), encoding="utf-8")
	return path
