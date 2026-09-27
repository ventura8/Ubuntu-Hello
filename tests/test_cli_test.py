"""Tests for `ubuntu-hello test` (ubuntu-hello/src/cli/test.py): the live camera test window.

The command is a loop at import time that runs until a key is pressed. OpenCV,
dlib, the camera and the overlay text renderer are mocked; cv2.waitKey is
scripted to end the loop after a few frames. Model files live under tmp_path.
"""
import builtins
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import numpy as np
import pytest

import frame_text
from recorders import video_capture

SRC_DIR = Path(__file__).resolve().parent.parent / "ubuntu-hello" / "src"
WINDOW = "Ubuntu Hello Test"
GREEN, RED = (0, 230, 0), (0, 0, 230)

SCAN_HIST = np.array([[10.0], [60.0], [30.0], [0.0], [0.0], [0.0], [0.0], [0.0]])
DARK_HIST = np.array([[90.0], [10.0], [0.0], [0.0], [0.0], [0.0], [0.0], [0.0]])

# Stored descriptors: model 0 has one, model 1 has two (the second is the one we will match).
DESC_A = [0.0] * 128
DESC_B = [1.0] * 128
DESC_C = [0.5] * 128
NEAR_C = np.array(DESC_C) + 0.02      # distance ~0.23, under certainty 3.5 / 10
FAR = np.array([5.0] * 128)


def face(left=10, top=20, right=50, bottom=60):
	loc = MagicMock()
	loc.left.return_value = left
	loc.top.return_value = top
	loc.right.return_value = right
	loc.bottom.return_value = bottom
	return loc


@pytest.fixture
def test_env(tmp_path, monkeypatch):
	spec = importlib.util.spec_from_file_location("paths_factory", SRC_DIR / "paths_factory.py")
	pf = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(pf)
	monkeypatch.setitem(sys.modules, "paths_factory", pf)

	etc = tmp_path / "etc"
	(etc / "models").mkdir(parents=True)
	monkeypatch.setattr(pf.paths, "config_dir", etc)
	monkeypatch.setattr(pf.paths, "user_models_dir", etc / "models")
	monkeypatch.setattr(pf.paths, "dlib_data_dir", tmp_path / "dlib-data")
	monkeypatch.setattr(builtins, "ubuntu_hello_user", "alice", raising=False)

	cv2 = MagicMock()
	cv2.EVENT_LBUTTONDOWN = 1
	cv2.EVENT_MOUSEMOVE = 0
	gray = np.zeros((120, 160), dtype=np.uint8)
	cv2.createCLAHE.return_value.apply.return_value = gray
	cv2.calcHist.return_value = SCAN_HIST
	monkeypatch.setitem(sys.modules, "cv2", cv2)

	dlib = MagicMock()
	detector = MagicMock(return_value=[])
	dlib.get_frontal_face_detector.return_value = detector
	encoder = dlib.face_recognition_model_v1.return_value
	monkeypatch.setitem(sys.modules, "dlib", dlib)

	capture = MagicMock()
	capture.read_frame.return_value = ("orig", "gray")
	monkeypatch.setattr(video_capture, "VideoCapture", MagicMock(return_value=capture))

	texts = []
	monkeypatch.setattr(frame_text, "draw_text",
	                    lambda img, text, org, scale, color: texts.append((text, org, color)))

	clock = {"now": 5000.0}

	def fake_time():
		clock["now"] += 0.4
		return clock["now"]

	sleeps = []
	monkeypatch.setattr("time.time", fake_time)
	monkeypatch.setattr("time.sleep", sleeps.append)

	def keys(*codes, on_first=None):
		"""Script cv2.waitKey; *on_first* runs inside the first frame's key poll."""
		script = list(codes)

		def wait_key(delay):
			if on_first is not None and len(script) == len(codes):
				on_first()
			return script.pop(0)

		cv2.waitKey.side_effect = wait_key

	def mouse_callback():
		name, callback = cv2.setMouseCallback.call_args.args
		assert name == WINDOW
		return callback

	def write_models(models):
		(etc / "models" / "alice.dat").write_text(json.dumps(models))

	def run():
		sys.modules.pop("cli.test", None)
		return importlib.import_module("cli.test")

	yield SimpleNamespace(
		config=etc / "config.ini", cv2=cv2, dlib=dlib, detector=detector, encoder=encoder,
		capture=capture, texts=texts, sleeps=sleeps, keys=keys, mouse_callback=mouse_callback,
		write_models=write_models, run=run)
	sys.modules.pop("cli.test", None)


def drawn(env):
	return [t[0] for t in env.texts]


def test_non_opencv_recorder_is_refused(test_env, capsys):
	test_env.config.write_text("[video]\nrecording_plugin = ffmpeg\n")
	with pytest.raises(SystemExit) as exc:
		test_env.run()
	assert exc.value.code == 12
	assert "doesn't support the test command yet" in capsys.readouterr().out
	test_env.cv2.namedWindow.assert_not_called()


def test_keypress_closes_window(test_env, capsys):
	test_env.keys(-1, -1, ord("q"))
	test_env.run()

	cv2 = test_env.cv2
	cv2.namedWindow.assert_called_once_with(WINDOW)
	assert cv2.imshow.call_count == 3
	assert all(c.args[0] == WINDOW for c in cv2.imshow.call_args_list)
	cv2.destroyAllWindows.assert_called_once_with()
	out = capsys.readouterr().out
	assert "Opening a window with a test feed" in out
	assert "Closing window" in out

	texts = drawn(test_env)
	assert "RESOLUTION: 160x120" in texts  # width x height
	assert "FRAMES: 1" in texts and "FRAMES: 3" in texts
	assert texts.count("SCAN FRAME") == 3
	assert "SLOW MODE" not in texts
	# Eight histogram bars per frame.
	assert cv2.rectangle.call_count == 24
	# The fake clock ticks 0.4 s per call, so the per-second FPS counter rolls over.
	assert any(t.startswith("FPS: ") and t != "FPS: 0" for t in texts)
	assert test_env.sleeps == []


def test_ctrl_c_is_handled(test_env, capsys):
	test_env.capture.read_frame.side_effect = KeyboardInterrupt
	test_env.run()
	test_env.cv2.destroyAllWindows.assert_called_once_with()
	assert "Closing window" in capsys.readouterr().out


def test_click_toggles_slow_mode(test_env):
	cv2 = test_env.cv2

	def click():
		callback = test_env.mouse_callback()
		callback(cv2.EVENT_MOUSEMOVE, 5, 5, 0, None)   # ignored
		callback(cv2.EVENT_LBUTTONDOWN, 5, 5, 0, None)

	test_env.keys(-1, -1, 27, on_first=click)
	test_env.run()

	texts = drawn(test_env)
	# Frame 1 was drawn before the click; frames 2 and 3 show the banner.
	assert texts.count("SLOW MODE") == 2
	# Frames 1 and 2 finished in slow mode and were delayed (frame 3 quit first).
	assert len(test_env.sleeps) == 2
	assert all(0.0 <= s <= 0.5 for s in test_env.sleeps)


def test_dark_frames_are_not_scanned(test_env):
	test_env.cv2.calcHist.return_value = DARK_HIST
	test_env.keys(-1, 1)
	test_env.run()
	assert drawn(test_env).count("DARK FRAME") == 2
	assert "SCAN FRAME" not in drawn(test_env)
	test_env.detector.assert_not_called()


def test_dark_threshold_from_config(test_env):
	test_env.config.write_text("[video]\ndark_threshold = 95\n")
	test_env.cv2.calcHist.return_value = DARK_HIST
	test_env.keys(1)
	test_env.run()
	assert drawn(test_env).count("SCAN FRAME") == 1
	test_env.detector.assert_called_once()


def test_faces_without_models_get_red_circle(test_env):
	test_env.detector.return_value = [face()]
	test_env.keys(1)
	test_env.run()
	# centre (30, 40), radius 20 + 20 % padding
	test_env.cv2.circle.assert_called_once()
	args = test_env.cv2.circle.call_args.args
	assert args[1:] == ((30, 40), 24, RED, 2)
	test_env.encoder.compute_face_descriptor.assert_not_called()
	assert "no match" not in drawn(test_env)


def test_models_without_descriptors_are_treated_as_no_models(test_env):
	"""A model file whose models hold no descriptors crashed np.linalg.norm."""
	test_env.write_models([{"id": 0, "label": "empty", "data": []}])
	test_env.detector.return_value = [face()]
	test_env.keys(1)
	test_env.run()
	assert test_env.cv2.circle.call_args.args[1:] == ((30, 40), 24, RED, 2)
	test_env.encoder.compute_face_descriptor.assert_not_called()


def test_matching_and_non_matching_faces(test_env):
	test_env.write_models([
		{"id": 0, "label": "office", "data": [DESC_A]},
		{"id": 1, "label": "evening", "data": [DESC_B, DESC_C]},
	])
	test_env.detector.return_value = [face(), face(100, 20, 140, 60)]
	test_env.encoder.compute_face_descriptor.side_effect = [NEAR_C, FAR]
	test_env.keys(1)
	test_env.run()

	circles = [c.args[1:] for c in test_env.cv2.circle.call_args_list]
	assert circles == [((30, 40), 24, GREEN, 2), ((120, 40), 24, RED, 2)]
	matched = [t for t in test_env.texts if t[0].startswith("evening (certainty: ")]
	assert len(matched) == 1
	text, org, colour = matched[0]
	assert text == "evening (certainty: %s)" % round(float(np.linalg.norm(NEAR_C - DESC_C)) * 10, 3)
	assert org == (38, 16) and colour == (0, 255, 0)
	assert ("no match", (128, 16), (0, 0, 255)) in test_env.texts
	predictor = test_env.dlib.shape_predictor.return_value
	assert predictor.call_args_list[0] == call("orig", test_env.detector.return_value[0])


def test_match_outside_certainty_is_no_match(test_env):
	test_env.config.write_text("[video]\ncertainty = 1.0\n")
	test_env.write_models([{"id": 0, "label": "office", "data": [DESC_C]}])
	test_env.detector.return_value = [face()]
	test_env.encoder.compute_face_descriptor.return_value = NEAR_C
	test_env.keys(1)
	test_env.run()
	assert "no match" in drawn(test_env)
	assert test_env.cv2.circle.call_args.args[3] == RED


def test_cnn_detector_uses_rect(test_env):
	test_env.config.write_text("[core]\nuse_cnn = true\n")
	loc = MagicMock()
	loc.rect = face()
	cnn = MagicMock(return_value=[loc])
	test_env.dlib.cnn_face_detection_model_v1.return_value = cnn
	test_env.keys(1)
	test_env.run()
	test_env.dlib.get_frontal_face_detector.assert_not_called()
	cnn.assert_called_once()
	assert test_env.cv2.circle.call_args.args[1:] == ((30, 40), 24, RED, 2)


def test_manual_exposure_is_reapplied_every_frame(test_env):
	test_env.config.write_text("[video]\nexposure = 150\n")
	test_env.keys(-1, 1)
	test_env.run()
	cv2 = test_env.cv2
	# The keypress on frame 2 ends the loop before its exposure update.
	assert test_env.capture.internal.set.call_args_list == [
		call(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0),
		call(cv2.CAP_PROP_EXPOSURE, 150.0),
	]


def test_default_exposure_is_left_alone(test_env):
	test_env.keys(-1, 1)
	test_env.run()
	test_env.capture.internal.set.assert_not_called()
