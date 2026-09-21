"""The real recognition pipeline on recorded frames from the real camera.

compare.py's actual scan loop, the real dlib detector, landmark predictor and
encoder, and the real nod rubber stamp all run here. Only the camera is replaced,
by a replay of a recorded clip. See conftest.py for why this tier exists.
"""
from __future__ import annotations

import _thread
import importlib
import json
import runpy
import signal

import numpy as np
import pytest

import compare
import enroll_capture

COMPARE_PY = compare.__file__

AUTHENTICATED = 0
TIMED_OUT = 11
LIVENESS_FAILED = 15

CONFIG = """
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
timeout = {timeout}
acquisition_timeout = {acquisition}
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
enabled = {liveness}
stamp_rules = nod\t{nod_timeout}s\tfailsafe\tmin_distance=12

[debug]
end_report = false
gtk_stdout = false

[notifications]
enabled = false
"""


class _SilentNotifier:
	disabled_reason = ""

	def __init__(self, *args, **kwargs):
		pass

	def __getattr__(self, name):
		return lambda *args, **kwargs: None


def _no_overlay(*args, **kwargs):
	raise FileNotFoundError("no auth overlay in tests")


def scan(tmp_path, monkeypatch, footage, model_file, dlib_data_dir, clip, *,
         certainty=2.6, confirmations=4, timeout=8, liveness=False, nod_timeout=5, pipeline, loop=True):
	"""Run compare.py's real main loop over *clip* and return its exit code."""

	config = tmp_path / "config.ini"
	config.write_text(CONFIG.format(certainty=certainty, confirmations=confirmations, timeout=timeout,
	                                acquisition=timeout + 2, liveness="true" if liveness else "false",
	                                nod_timeout=nod_timeout), encoding="utf-8")

	paths_factory = importlib.import_module("paths_factory")
	config_ensure = importlib.import_module("config_ensure")
	notify = importlib.import_module("notify")
	video_capture_module = importlib.import_module("recorders.video_capture")

	monkeypatch.setattr(paths_factory, "config_file_path", lambda: str(config))
	monkeypatch.setattr(paths_factory, "user_model_path", lambda user: str(model_file), raising=False)
	monkeypatch.setattr(paths_factory, "dlib_data_dir_path", lambda: dlib_data_dir, raising=False)
	monkeypatch.setattr(paths_factory, "shape_predictor_5_face_landmarks_path",
	                    lambda: dlib_data_dir + "/shape_predictor_5_face_landmarks.dat", raising=False)
	monkeypatch.setattr(paths_factory, "dlib_face_recognition_resnet_model_v1_path",
	                    lambda: dlib_data_dir + "/dlib_face_recognition_resnet_model_v1.dat", raising=False)
	monkeypatch.setattr(paths_factory, "mmod_human_face_detector_path",
	                    lambda: dlib_data_dir + "/mmod_human_face_detector.dat", raising=False)
	monkeypatch.setattr(config_ensure, "ensure_system_config", lambda *a, **k: None)
	monkeypatch.setattr(notify, "AuthNotifier", _SilentNotifier)
	the_clip = footage[clip]
	monkeypatch.setattr(video_capture_module, "VideoCapture", lambda cfg: pipeline["capture"](the_clip, loop))
	if pipeline is not None and pipeline["mode"] == "signals":
		# No pixels to look at: dlib's numeric work is played back per frame, and
		# compare.py's own loop, darkness check and gates still run for real.
		dlib = importlib.import_module("dlib")
		monkeypatch.setattr(dlib, "get_frontal_face_detector", lambda: pipeline["detector"])
		monkeypatch.setattr(dlib, "shape_predictor", lambda path: pipeline["predictor"])
		monkeypatch.setattr(dlib, "face_recognition_model_v1", lambda path: pipeline["encoder"])
		monkeypatch.setattr("os.path.isfile", lambda p, _real=__import__("os").path.isfile:
		                    True if p.endswith("shape_predictor_5_face_landmarks.dat") else _real(p))
	monkeypatch.setattr(compare.subprocess, "Popen", _no_overlay)
	# Load the detectors inline: the helper thread signals through a lock, and a
	# raise in it would hang the test instead of failing it.
	monkeypatch.setattr(_thread, "start_new_thread", lambda fn, args=(), kwargs=None: fn(*args, **(kwargs or {})))
	monkeypatch.setattr("sys.argv", ["compare.py", "alice"])

	previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
	try:
		with pytest.raises(SystemExit) as exit_info:
			runpy.run_path(COMPARE_PY, run_name="__main__")
		return exit_info.value.code
	finally:
		for sig, handler in previous.items():
			signal.signal(sig, handler)


class TestEnrollment:
	def test_guided_enrollment_collects_several_distinct_samples(self, enrolled_model):
		samples = enrolled_model[0]["data"]
		assert 2 <= len(samples) <= enroll_capture.MAX_SAMPLES, len(samples)
		assert all(len(s) == 128 for s in samples)
		vectors = np.array(samples)
		for i in range(len(vectors)):
			for j in range(i + 1, len(vectors)):
				assert np.linalg.norm(vectors[i] - vectors[j]) >= enroll_capture.DUPLICATE_DISTANCE, \
					"the duplicate filter let two near-identical samples through"


class TestMatching:
	def test_the_same_face_authenticates_at_secure(self, tmp_path, monkeypatch, footage, model_file, dlib_data_dir, pipeline):
		"""Enrolled from one clip, recognised in another, at the shipped default level."""
		code = scan(tmp_path, monkeypatch, footage, model_file, dlib_data_dir, "nod", pipeline=pipeline)
		assert code == AUTHENTICATED

	def test_no_face_never_authenticates(self, tmp_path, monkeypatch, footage, model_file, dlib_data_dir, pipeline):
		code = scan(tmp_path, monkeypatch, footage, model_file, dlib_data_dir, "away", timeout=4, pipeline=pipeline)
		assert code == TIMED_OUT

	def test_the_agreeing_frames_gate_is_live_on_real_frames(self, tmp_path, monkeypatch, footage, model_file, dlib_data_dir, pipeline):
		"""A count above the clip's usable frames must refuse the clip that passes at 4.

		The clip is played once rather than looped, so the count is unreachable by
		construction and not merely by how slowly frames happen to be processed.
		"""
		clip = footage["nod"]
		code = scan(tmp_path, monkeypatch, footage, model_file, dlib_data_dir, "nod",
		            confirmations=clip.n + 1, timeout=4, pipeline=pipeline, loop=False)
		assert code == TIMED_OUT


class TestLiveness:
	def test_a_nod_passes_the_challenge(self, tmp_path, monkeypatch, footage, model_file, dlib_data_dir, pipeline):
		"""The nod detector on real landmarks: the rewrite that fixed the two-second window."""
		code = scan(tmp_path, monkeypatch, footage, model_file, dlib_data_dir, "nod", liveness=True, pipeline=pipeline)
		assert code == AUTHENTICATED

	def test_holding_still_fails_the_challenge(self, tmp_path, monkeypatch, footage, model_file, dlib_data_dir, pipeline):
		"""A recognised face that never nods must be refused: failsafe, not faildeadly."""
		code = scan(tmp_path, monkeypatch, footage, model_file, dlib_data_dir, "still", liveness=True, nod_timeout=3, pipeline=pipeline)
		assert code == LIVENESS_FAILED


class TestCalibration:
	def test_secure_level_has_margin_on_this_footage(self, footage, enrolled_model, descriptors_of):
		"""Turns the session's threshold measurements into a guard.

		Secure at 2.6 must match a healthy share of usable frames of the same face,
		not scrape by on a single lucky one; that is what makes four agreeing frames
		arrive in well under a second.
		"""
		model = np.array(enrolled_model[0]["data"])
		distances = [float(np.min(np.linalg.norm(model - d, axis=1))) * 10
		             for d in descriptors_of(footage["nod"])]
		assert len(distances) >= 20, "too few usable frames to judge (%d)" % len(distances)
		under = sum(1 for x in distances if x < 2.6) / len(distances)
		assert under >= 0.5, "only %.0f%% of frames under the Secure threshold" % (under * 100)
		assert min(distances) < 2.0, "best frame %.2f: this footage would not reach a tight threshold" % min(distances)
