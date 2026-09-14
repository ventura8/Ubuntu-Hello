"""Real-loop E2E: one lucky frame must not authenticate at the Secure level.

compare.py accepts on the *best* frame of a whole scan, and a scan takes dozens of
independent looks at the camera. The frame that decides authentication is therefore
the minimum of many draws, so a single blurred or oddly lit outlier from a stranger
was enough to get in. The Secure level closes that by requiring several separate
frames to agree (`[video] confirmations`) rather than by pushing `certainty` lower,
which on real IR hardware starts rejecting the legitimate user in changed light.

The scan loop itself runs for real here, through runpy: only the camera, dlib and
the histogram are faked, at their own boundaries. The per-frame distances are
scripted; everything that decides on them is production code.
"""
from __future__ import annotations

import _thread
import importlib
import json
import runpy
import signal
import time

import numpy as np
import pytest

import compare

COMPARE_PY = compare.__file__

CONFIG_TEMPLATE = """
[core]
detection_notice = false
timeout_notice = false
no_confirmation = true
suppress_unknown = false
abort_if_ssh = false
abort_if_lid_closed = false
abort_on_session_idle = false
disabled = false
ignore_services =
use_cnn = false
workaround = off

[video]
certainty = {certainty}
confirmations = {confirmations}
timeout = 1
acquisition_timeout = 2
device_path = none
warn_no_device = false
max_height = 320
frame_width = -1
frame_height = -1
dark_threshold = 60
recording_plugin = opencv
device_format = v4l2
force_mjpeg = false
exposure = -1
device_fps = -1
rotate = 0

[snapshots]
save_failed = false
save_successful = false

[rubberstamps]
enabled = false

[debug]
end_report = false
gtk_stdout = false

[notifications]
enabled = false
"""


class _Encoder:
	"""dlib's face encoder, returning descriptors at scripted distances.

	The enrolled model is the zero vector, so the norm of what this returns *is*
	the match distance. compare divides the configured certainty by ten, and the
	descriptor is scaled the same way, so the numbers here read in config units.
	"""

	def __init__(self, distances):
		self.distances = list(distances)
		self.calls = 0

	def compute_face_descriptor(self, frame, landmark, jitters=1):
		self.calls += 1
		distance = self.distances.pop(0) if self.distances else 9.0
		descriptor = np.zeros(128)
		descriptor[0] = distance / 10.0
		return descriptor


class _Camera:
	"""Enough of recorders.video_capture.VideoCapture for the loop to turn over."""

	def __init__(self, config):
		self.fw = 320
		self.internal = self

	def get(self, prop):           # cv2.CAP_PROP_FRAME_WIDTH / _HEIGHT
		return 320

	def read_frame(self):
		time.sleep(0.001)          # a plausible frame interval, not a spin
		return np.zeros((320, 320, 3), dtype=np.uint8), np.zeros((320, 320), dtype=np.uint8)

	def release(self):
		pass


class _SilentNotifier:
	disabled_reason = ""

	def __init__(self, *args, **kwargs):
		pass

	def __getattr__(self, name):
		return lambda *args, **kwargs: None


def _run_scan(tmp_path, monkeypatch, distances, confirmations, certainty=1.8, omit_key=False):
	"""Run compare.py's real scan over *distances* and return its exit code."""
	config = tmp_path / "config.ini"
	text = CONFIG_TEMPLATE.format(certainty=certainty, confirmations=confirmations)
	if omit_key:
		text = text.replace("confirmations = %s\n" % confirmations, "")
	config.write_text(text)
	models = tmp_path / "alice.dat"
	models.write_text(json.dumps([{"time": 0, "label": "test", "id": 0, "data": [[0.0] * 128]}]))

	encoder = _Encoder(distances)
	# Resolved here rather than at import time: other tests in the suite replace
	# these entries in sys.modules, and a reference captured at collection can be
	# a different object from the one compare.py imports when it runs.
	paths_factory = importlib.import_module("paths_factory")
	config_ensure = importlib.import_module("config_ensure")
	notify = importlib.import_module("notify")
	video_capture_module = importlib.import_module("recorders.video_capture")
	cv2 = importlib.import_module("cv2")
	dlib = importlib.import_module("dlib")

	monkeypatch.setattr(paths_factory, "config_file_path", lambda: str(config))
	# The GTK copy of paths_factory shadows the daemon one on the test path and has
	# no model/dlib helpers, so these are added rather than replaced.
	monkeypatch.setattr(paths_factory, "user_model_path", lambda user: str(models), raising=False)
	# init_detector refuses to start when the landmark data file is missing, so
	# these point at a file that does exist; the dlib constructors reading them
	# are faked below, so the contents are never looked at.
	for helper in ("dlib_face_recognition_resnet_model_v1_path", "shape_predictor_5_face_landmarks_path",
	               "mmod_human_face_detector_path", "dlib_data_dir_path"):
		monkeypatch.setattr(paths_factory, helper, lambda: COMPARE_PY, raising=False)
	monkeypatch.setattr(config_ensure, "ensure_system_config", lambda *a, **k: None)
	monkeypatch.setattr(notify, "AuthNotifier", _SilentNotifier)
	monkeypatch.setattr(video_capture_module, "VideoCapture", _Camera)
	monkeypatch.setattr(dlib, "get_frontal_face_detector", lambda: (lambda frame, upsample=0: [object()]))
	monkeypatch.setattr(dlib, "shape_predictor", lambda path: (lambda frame, rect: object()))
	monkeypatch.setattr(dlib, "face_recognition_model_v1", lambda path: encoder)
	monkeypatch.setattr(cv2, "createCLAHE", lambda **kwargs: type("C", (), {"apply": staticmethod(lambda gs: gs)})())
	# A frame that is nowhere near dark enough to be skipped.
	monkeypatch.setattr(cv2, "calcHist", lambda *a, **k: np.array([[1.0]] + [[10.0]] * 7))
	# No auth overlay: the missing binary also skips compare's atexit hook, which
	# would otherwise tear down the camera from inside the pytest process.
	monkeypatch.setattr(compare.subprocess, "Popen", _no_overlay)
	# Load the detectors inline instead of on compare's helper thread: the thread
	# signals completion through a lock it only releases on success, so anything
	# raised in the fakes would hang the run instead of failing the test.
	monkeypatch.setattr(_thread, "start_new_thread",
	                    lambda fn, args=(), kwargs=None: fn(*args, **(kwargs or {})))
	monkeypatch.setattr("sys.argv", ["compare.py", "alice"])

	previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
	try:
		with pytest.raises(SystemExit) as exit_info:
			runpy.run_path(COMPARE_PY, run_name="__main__")
		return exit_info.value.code, encoder
	finally:
		for sig, handler in previous.items():
			signal.signal(sig, handler)


def _no_overlay(*args, **kwargs):
	raise FileNotFoundError("no auth overlay in tests")


AUTHENTICATED = 0
TIMED_OUT = 11
DETECTOR_FAILED = 1


class TestADeadDetectorThreadCannotHangTheLogin:
	def test_a_failing_model_load_exits_instead_of_blocking_forever(self, tmp_path, monkeypatch):
		"""compare.py loads dlib on a helper thread and waits on a lock it releases.

		The release used to happen only on success, so a constructor that raised --
		a truncated model file, out of memory -- left the lock held, and the main
		thread blocked on it forever with the camera already open. The PAM caller
		then waited for a login that could never finish. This runs the real helper
		thread, so a regression hangs the worker and fails on the join timeout
		rather than hanging the whole suite.
		"""
		import threading

		config = tmp_path / "config.ini"
		config.write_text(CONFIG_TEMPLATE.format(certainty=1.8, confirmations=1), encoding="utf-8")
		models = tmp_path / "alice.dat"
		models.write_text(json.dumps([{"time": 0, "label": "t", "id": 0, "data": [[0.0] * 128]}]),
		                  encoding="utf-8")

		paths_factory = importlib.import_module("paths_factory")
		config_ensure = importlib.import_module("config_ensure")
		notify = importlib.import_module("notify")
		video_capture_module = importlib.import_module("recorders.video_capture")
		cv2 = importlib.import_module("cv2")
		dlib = importlib.import_module("dlib")

		monkeypatch.setattr(paths_factory, "config_file_path", lambda: str(config))
		monkeypatch.setattr(paths_factory, "user_model_path", lambda user: str(models), raising=False)
		for helper in ("dlib_face_recognition_resnet_model_v1_path", "shape_predictor_5_face_landmarks_path",
		               "mmod_human_face_detector_path", "dlib_data_dir_path"):
			monkeypatch.setattr(paths_factory, helper, lambda: COMPARE_PY, raising=False)
		monkeypatch.setattr(config_ensure, "ensure_system_config", lambda *a, **k: None)
		monkeypatch.setattr(notify, "AuthNotifier", _SilentNotifier)
		monkeypatch.setattr(video_capture_module, "VideoCapture", _Camera)
		monkeypatch.setattr(dlib, "get_frontal_face_detector", lambda: (lambda frame, upsample=0: []))
		# The failure under test: the landmark model cannot be loaded.
		def explode(path):
			raise RuntimeError("model file is truncated")
		monkeypatch.setattr(dlib, "shape_predictor", explode)
		monkeypatch.setattr(cv2, "createCLAHE",
		                    lambda **kwargs: type("C", (), {"apply": staticmethod(lambda gs: gs)})())
		monkeypatch.setattr(cv2, "calcHist", lambda *a, **k: np.array([[1.0]] + [[10.0]] * 7))
		monkeypatch.setattr(compare.subprocess, "Popen", _no_overlay)
		monkeypatch.setattr("sys.argv", ["compare.py", "alice"])
		# compare.py installs SIGTERM/SIGINT handlers, which Python only allows from
		# the main thread; the scan runs on a worker here so the join can time out.
		monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
		# Deliberately NOT stubbing _thread.start_new_thread: the point is the real
		# helper thread and the real lock hand-off.

		outcome = {}

		def run():
			try:
				runpy.run_path(COMPARE_PY, run_name="__main__")
			except SystemExit as exit_info:
				outcome["code"] = exit_info.code
			except BaseException as err:                       # pragma: no cover - diagnostic
				outcome["error"] = repr(err)

		worker = threading.Thread(target=run, daemon=True)
		worker.start()
		worker.join(timeout=30)
		assert not worker.is_alive(), "compare.py never returned: the detector lock was not released"
		assert outcome.get("code") == DETECTOR_FAILED, outcome


class TestOneGoodFrameIsNotEnough:
	def test_single_matching_frame_authenticates_at_the_looser_levels(self, tmp_path, monkeypatch):
		"""Fast and Balanced keep the historical behaviour: one frame under the bar wins."""
		code, _ = _run_scan(tmp_path, monkeypatch, [9.0, 9.0, 1.5], confirmations=1)
		assert code == AUTHENTICATED

	def test_single_matching_frame_is_rejected_when_three_must_agree(self, tmp_path, monkeypatch):
		"""The outlier frame the Secure level exists to catch."""
		code, _ = _run_scan(tmp_path, monkeypatch, [9.0, 1.5, 9.0, 9.0], confirmations=3)
		assert code == TIMED_OUT

	def test_two_matching_frames_are_still_not_enough(self, tmp_path, monkeypatch):
		code, _ = _run_scan(tmp_path, monkeypatch, [1.5, 9.0, 1.4, 9.0], confirmations=3)
		assert code == TIMED_OUT

	def test_three_matching_frames_authenticate(self, tmp_path, monkeypatch):
		"""The real user, who matches a good share of frames, still gets in."""
		code, _ = _run_scan(tmp_path, monkeypatch, [1.5, 1.6, 1.4], confirmations=3)
		assert code == AUTHENTICATED

	def test_the_agreeing_frames_do_not_have_to_be_consecutive(self, tmp_path, monkeypatch):
		"""A blink or a motion-blurred frame in between must not cost the whole run."""
		code, _ = _run_scan(tmp_path, monkeypatch, [1.5, 9.0, 1.6, 9.0, 9.0, 1.4], confirmations=3)
		assert code == AUTHENTICATED

	def test_frames_above_the_threshold_never_count(self, tmp_path, monkeypatch):
		"""Confirmations tighten the decision; they must not relax the threshold."""
		code, _ = _run_scan(tmp_path, monkeypatch, [1.9, 2.0, 1.85], confirmations=1, certainty=1.8)
		assert code == TIMED_OUT

	def test_a_config_without_the_key_keeps_the_single_frame_behaviour(self, tmp_path, monkeypatch):
		"""An upgrade from before this key existed must not suddenly need more frames."""
		code, _ = _run_scan(tmp_path, monkeypatch, [9.0, 1.5], confirmations=1, omit_key=True)
		assert code == AUTHENTICATED

	def test_a_nonsense_count_falls_back_to_one_frame(self, tmp_path, monkeypatch):
		"""Zero or negative must not mean "accept without matching anything"."""
		code, _ = _run_scan(tmp_path, monkeypatch, [9.0, 1.5], confirmations=0)
		assert code == AUTHENTICATED
		code, _ = _run_scan(tmp_path, monkeypatch, [9.0, 9.0], confirmations=0)
		assert code == TIMED_OUT
