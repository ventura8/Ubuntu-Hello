"""Tests for `ubuntu-hello add` (ubuntu-hello/src/cli/add.py).

The subcommand runs at import time. Camera, dlib, OpenCV and the notification
card are mocked; the guided capture itself (enroll_capture.capture_guided_samples)
has its own tests, so here it is replaced by a scripted fake that drives the
callbacks add.py hands it. Models are written under tmp_path only.
"""
import builtins
import importlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import enroll_capture
import notify
from recorders import video_capture

SRC_DIR = Path(__file__).resolve().parent.parent / "ubuntu-hello" / "src"
DESCRIPTOR = [0.25] * 128


class FakeTTY:
	def __init__(self, tty):
		self.tty = tty

	def isatty(self):
		return self.tty


@pytest.fixture
def add_env(tmp_path, monkeypatch):
	spec = importlib.util.spec_from_file_location("paths_factory", SRC_DIR / "paths_factory.py")
	pf = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(pf)
	monkeypatch.setitem(sys.modules, "paths_factory", pf)

	etc = tmp_path / "etc"
	etc.mkdir()
	dlib_data = tmp_path / "dlib-data"
	dlib_data.mkdir()
	(dlib_data / "shape_predictor_5_face_landmarks.dat").write_bytes(b"")
	monkeypatch.setattr(pf.paths, "config_dir", etc)
	monkeypatch.setattr(pf.paths, "user_models_dir", etc / "models")
	monkeypatch.setattr(pf.paths, "dlib_data_dir", dlib_data)

	# Fresh hardware / library doubles per test so call assertions are isolated.
	dlib = MagicMock()
	face = MagicMock(name="face")
	detector = MagicMock(return_value=[face])
	dlib.get_frontal_face_detector.return_value = detector
	dlib.face_recognition_model_v1.return_value.compute_face_descriptor.return_value = DESCRIPTOR
	monkeypatch.setitem(sys.modules, "dlib", dlib)
	cv2 = MagicMock()
	monkeypatch.setitem(sys.modules, "cv2", cv2)

	capture = MagicMock()
	capture.read_frame.return_value = ("frame", None)
	monkeypatch.setattr(video_capture, "VideoCapture", MagicMock(return_value=capture))
	notifier = MagicMock()
	notifier_cls = MagicMock(return_value=notifier)
	monkeypatch.setattr(notify, "EnrollNotifier", notifier_cls)

	sleeps = []
	monkeypatch.setattr("time.sleep", sleeps.append)
	monkeypatch.setattr(enroll_capture, "frame_darkness", lambda gs, np, cv: 10.0)
	monkeypatch.setattr(sys, "stdin", FakeTTY(False))

	guided = {}

	def fake_capture(read_frame, detect_faces, encode_face, emit, first_sample):
		guided["first_sample"] = first_sample
		frame, gsframe = read_frame()
		second = encode_face(frame, detect_faces(gsframe)[0])
		for line in ("@progress 1/3", "@guide left", "Turn your head slightly to the left",
		             "@progress 2/3", "@progress garbage"):
			emit(line)
		return [list(first_sample), second]

	monkeypatch.setattr(enroll_capture, "capture_guided_samples", fake_capture)

	def set_args(arguments=(), y=False, plain=False, user="alice"):
		monkeypatch.setattr(builtins, "ubuntu_hello_user", user, raising=False)
		monkeypatch.setattr(
			builtins, "ubuntu_hello_args",
			SimpleNamespace(arguments=list(arguments), y=y, plain=plain, user=user),
			raising=False)

	def run():
		sys.modules.pop("cli.add", None)
		return importlib.import_module("cli.add")

	set_args()
	yield SimpleNamespace(
		etc=etc, models=etc / "models", model=etc / "models" / "alice.dat", config=etc / "config.ini",
		dlib_data=dlib_data, dlib=dlib, detector=detector, face=face, cv2=cv2, capture=capture,
		notifier=notifier, notifier_cls=notifier_cls, sleeps=sleeps, guided=guided,
		set_args=set_args, run=run)
	sys.modules.pop("cli.add", None)


def test_missing_dlib_module_exits(add_env, monkeypatch, capsys):
	monkeypatch.setitem(sys.modules, "dlib", None)
	with pytest.raises(SystemExit) as exc:
		add_env.run()
	assert exc.value.code == 1
	assert "Can't import the dlib module" in capsys.readouterr().out
	assert not add_env.model.exists()


def test_missing_data_files_exits(add_env, capsys):
	(add_env.dlib_data / "shape_predictor_5_face_landmarks.dat").unlink()
	with pytest.raises(SystemExit) as exc:
		add_env.run()
	assert exc.value.code == 1
	out = capsys.readouterr().out
	assert "Data files have not been downloaded" in out
	assert "cd " + str(add_env.dlib_data) in out
	add_env.capture.read_frame.assert_not_called()


def test_first_model_is_created_and_saved(add_env, capsys):
	add_env.run()

	assert add_env.models.is_dir()
	saved = json.loads(add_env.model.read_text())
	assert len(saved) == 1
	model = saved[0]
	assert model["id"] == 0
	assert model["label"] == "Model #0"
	assert model["data"] == [DESCRIPTOR, DESCRIPTOR]
	assert isinstance(model["time"], int)
	assert stat.S_IMODE(os.stat(add_env.model).st_mode) == 0o600

	out = capsys.readouterr().out
	assert "No face model folder found, creating one" in out
	assert "Adding face model for the user alice" in out
	assert 'Using default label "Model #0"' in out
	assert "@guide center" in out
	assert "@progress 2/3" in out
	assert "Captured 2 face samples" in out
	assert out.rstrip().endswith("Added a new model to alice")

	# The user gets time to read the first prompt, and the camera is released.
	assert add_env.sleeps == [2]
	add_env.capture.release.assert_called_once_with()
	add_env.detector.assert_called()
	assert add_env.guided["first_sample"] == DESCRIPTOR


def test_prompts_are_mirrored_on_the_notification(add_env):
	add_env.run()

	args, kwargs = add_env.notifier_cls.call_args
	assert args == ("alice",)
	assert kwargs == {"enabled": True}
	guides = [c.args for c in add_env.notifier.guide.call_args_list]
	assert guides == [
		("Please look straight into the camera", 0, 0),
		("Please look straight into the camera", 1, 3),
		("Turn your head slightly to the left", 1, 3),
		("Turn your head slightly to the left", 2, 3),
		# A malformed progress line keeps the last good count.
		("Turn your head slightly to the left", 2, 3),
	]
	add_env.notifier.saved.assert_called_once_with("Model #0", 2)
	add_env.notifier.failed.assert_not_called()


def test_notifications_disabled_in_config(add_env):
	add_env.config.write_text("[notifications]\nenabled = false\n")
	add_env.run()
	assert add_env.notifier_cls.call_args.kwargs == {"enabled": False}


def test_appends_to_existing_models_with_cli_label(add_env, capsys):
	existing = [{"id": i, "label": f"m{i}", "time": 1, "data": [DESCRIPTOR]} for i in (0, 1, 4, 7)]
	add_env.models.mkdir()
	add_env.model.write_text(json.dumps(existing))
	add_env.set_args(arguments=["desk, lamp on"], plain=True)

	add_env.run()

	saved = json.loads(add_env.model.read_text())
	assert saved[:4] == existing
	assert saved[4]["id"] == 8
	assert saved[4]["label"] == "desk lamp on"
	out = capsys.readouterr().out
	assert "NOTICE: Each additional model slows down" in out
	assert 'Removing illegal character ","' in out
	assert "Adding face model for the user" not in out
	assert "No face model folder found" not in out


def test_interactive_label_is_truncated(add_env, monkeypatch):
	monkeypatch.setattr(sys, "stdin", FakeTTY(True))
	prompts = []
	monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "x" * 40)
	add_env.run()
	assert prompts == ["Enter a label for this new model [Model #0]: "]
	assert json.loads(add_env.model.read_text())[0]["label"] == "x" * 24


def test_interactive_empty_or_eof_keeps_default_label(add_env, monkeypatch):
	monkeypatch.setattr(sys, "stdin", FakeTTY(True))

	def eof(prompt):
		raise EOFError

	monkeypatch.setattr("builtins.input", eof)
	add_env.run()
	assert json.loads(add_env.model.read_text())[0]["label"] == "Model #0"


def test_y_flag_skips_label_question_on_a_tty(add_env, monkeypatch):
	monkeypatch.setattr(sys, "stdin", FakeTTY(True))
	monkeypatch.setattr("builtins.input", MagicMock(side_effect=AssertionError("asked")))
	add_env.set_args(y=True)
	add_env.run()
	assert json.loads(add_env.model.read_text())[0]["label"] == "Model #0"


def test_cnn_detector_passes_rect_to_pose_predictor(add_env):
	add_env.config.write_text("[core]\nuse_cnn = true\n")
	cnn = MagicMock(return_value=[add_env.face])
	add_env.dlib.cnn_face_detection_model_v1.return_value = cnn

	add_env.run()

	assert add_env.dlib.cnn_face_detection_model_v1.call_args.args[0] == str(
		add_env.dlib_data / "mmod_human_face_detector.dat")
	add_env.dlib.get_frontal_face_detector.assert_not_called()
	cnn.assert_called()
	predictor = add_env.dlib.shape_predictor.return_value
	assert all(c.args[1] is add_env.face.rect for c in predictor.call_args_list)
	assert predictor.call_count == 2


def test_hog_detector_passes_location_itself(add_env):
	add_env.run()
	predictor = add_env.dlib.shape_predictor.return_value
	assert all(c.args[1] is add_env.face for c in predictor.call_args_list)


def _assert_failed(add_env, capsys, message):
	with pytest.raises(SystemExit) as exc:
		add_env.run()
	assert exc.value.code == 1
	out = capsys.readouterr().out
	assert message in out
	add_env.notifier.failed.assert_called_once()
	assert message in add_env.notifier.failed.call_args.args[0]
	add_env.capture.release.assert_called()
	assert not add_env.model.exists()
	return out


def test_only_black_frames(add_env, monkeypatch, capsys):
	darkness = iter([None, 100] * 30)
	monkeypatch.setattr(enroll_capture, "frame_darkness", lambda gs, np, cv: next(darkness))
	_assert_failed(add_env, capsys, "Camera saw only black frames - is IR emitter working?")
	assert add_env.capture.read_frame.call_count == 60
	add_env.detector.assert_not_called()


def test_all_frames_too_dark(add_env, monkeypatch, capsys):
	add_env.config.write_text("[video]\ndark_threshold = 50\n")
	monkeypatch.setattr(enroll_capture, "frame_darkness", lambda gs, np, cv: 80.0)
	out = _assert_failed(add_env, capsys, "All frames were too dark")
	assert "Average darkness: 80.0, Threshold: 50.0" in out
	add_env.detector.assert_not_called()


def test_no_face_in_usable_frames(add_env, capsys):
	add_env.detector.return_value = []
	_assert_failed(add_env, capsys, "No face detected, aborting")
	assert add_env.detector.call_count == 60


def test_face_found_after_dark_frames_starts_capture(add_env, monkeypatch):
	darkness = iter([None, 95.0, 20.0, 20.0, 20.0, 20.0])
	monkeypatch.setattr(enroll_capture, "frame_darkness", lambda gs, np, cv: next(darkness))
	add_env.run()
	# Two skipped frames, one scanned frame, then one read by the guided capture.
	assert add_env.capture.read_frame.call_count == 4
	assert json.loads(add_env.model.read_text())[0]["data"] == [DESCRIPTOR, DESCRIPTOR]


def test_multiple_faces_abort(add_env, capsys):
	add_env.detector.return_value = [MagicMock(), MagicMock()]
	_assert_failed(add_env, capsys, "Multiple faces detected, aborting")


def test_capture_error_still_releases_camera(add_env, monkeypatch):
	def broken(**kwargs):
		raise RuntimeError("camera unplugged")

	monkeypatch.setattr(enroll_capture, "capture_guided_samples", broken)
	with pytest.raises(RuntimeError, match="camera unplugged"):
		add_env.run()
	add_env.capture.release.assert_called_once_with()
	assert not add_env.model.exists()
	add_env.notifier.saved.assert_not_called()
