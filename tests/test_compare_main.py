"""compare.py's entry point, end to end through runpy.

The recognition decision itself is covered by test_compare_confirmations.py; this
file drives every other exit path of the ``__main__`` block (argument checks, the
missing model, the dark camera, the auth overlay protocol, rotation / scaling,
snapshots, the debug report, rubberstamps and manual exposure) plus the helper
functions' error branches. Only the camera, dlib, OpenCV, the overlay process,
the notifier and the snapshot writer are faked, at their own boundaries.
"""
from __future__ import annotations

import _thread
import atexit
import configparser
import importlib
import json
import runpy
import signal
import subprocess
import sys
import threading
import time
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

import compare

COMPARE_PY = compare.__file__
_REAL_SLEEP = time.sleep

BASE_CONFIG = {
	"core": {
		"abort_on_session_idle": "false",
		"use_cnn": "false",
	},
	"video": {
		"certainty": "1.8",
		"confirmations": "1",
		"timeout": "1",
		"acquisition_timeout": "0.3",
		"device_path": "/dev/video2",
		"max_height": "320",
		"dark_threshold": "60",
		"exposure": "-1",
		"rotate": "0",
	},
	"snapshots": {"save_failed": "false", "save_successful": "false"},
	"rubberstamps": {"enabled": "false"},
	"debug": {"end_report": "false", "gtk_stdout": "false"},
	"notifications": {"enabled": "false"},
}

# Histograms compare.py derives brightness from (8 buckets, bucket 0 = darkest).
BRIGHT = np.array([[1.0]] + [[10.0]] * 7)
DARK = np.array([[90.0]] + [[1.0]] * 7)
BLACK = np.zeros((8, 1))


class _Encoder:
	"""Face descriptors at scripted distances from the zero-vector model."""

	def __init__(self, distances):
		self.distances = list(distances)

	def compute_face_descriptor(self, frame, landmark, jitters=1):
		distance = self.distances.pop(0) if self.distances else 9.0
		descriptor = np.zeros(128)
		descriptor[0] = distance / 10.0
		return descriptor


class _Camera:
	"""recorders.video_capture.VideoCapture stand-in that records what it is told."""

	height = 320
	width = 320

	def __init__(self, config):
		self.fw = self.width
		self.internal = self
		self.released = 0
		self.settings = []
		_Camera.last = self

	def get(self, prop):
		cv2 = sys.modules["cv2"]
		if prop == cv2.CAP_PROP_FRAME_WIDTH:
			return self.width
		return self.height

	def set(self, prop, value):
		self.settings.append((prop, value))

	def read_frame(self):
		_REAL_SLEEP(0.001)
		return (np.zeros((self.height, self.width, 3), dtype=np.uint8),
		        np.zeros((self.height, self.width), dtype=np.uint8))

	def release(self):
		self.released += 1


class _Notifier:
	"""AuthNotifier stand-in recording every card compare.py asks for."""

	events = []
	disabled_reason = ""

	def __init__(self, user, **kwargs):
		_Notifier.events.append(("init", (user,), kwargs))

	def __getattr__(self, name):
		def record(*args, **kwargs):
			_Notifier.events.append((name, args, kwargs))
		return record


class _GtkProc:
	"""The auth overlay process; collects what compare.py writes to its stdin."""

	def __init__(self, argv, **kwargs):
		self.argv = argv
		self.kwargs = kwargs
		self.written = []
		self.terminated = False
		self.stdin = self
		_GtkProc.last = self

	def poll(self):
		return None

	def write(self, data):
		self.written.append(bytes(data).decode("utf-8"))

	def flush(self):
		pass

	def terminate(self):
		self.terminated = True

	def wait(self, timeout=None):
		return 0


def _no_overlay(*args, **kwargs):
	raise FileNotFoundError("no auth overlay in tests")


class Run:
	"""Outcome of one compare.py run."""

	def __init__(self, code, events, camera, encoder):
		self.code = code
		self.events = events
		self.camera = camera
		self.encoder = encoder

	def event(self, name):
		matches = [e for e in self.events if e[0] == name]
		assert matches, "no %r notification in %r" % (name, [e[0] for e in self.events])
		return matches[-1]

	def names(self):
		return [e[0] for e in self.events]


@pytest.fixture
def run_compare(tmp_path, monkeypatch):
	"""Run compare.py's __main__ with faked boundaries; returns a Run."""

	def _run(config=None, distances=(1.0,), hists=None, hist_default=BRIGHT, argv=("alice",), models=None,
	         popen=_no_overlay, display=False, detector=None, cnn_detector=None,
	         camera_size=(320, 320), data_files_present=True, extra=None):
		parser = configparser.ConfigParser()
		parser.read_dict(BASE_CONFIG)
		for section, values in (config or {}).items():
			if not parser.has_section(section):
				parser.add_section(section)
			for key, value in values.items():
				parser.set(section, key, str(value))
		config_path = tmp_path / "config.ini"
		with open(config_path, "w", encoding="utf-8") as handle:
			parser.write(handle)

		model_path = tmp_path / "alice.dat"
		if models is None:
			models = [{"time": 0, "label": "office", "id": 0, "data": [[0.0] * 128]}]
		if models != "missing":
			model_path.write_text(json.dumps(models), encoding="utf-8")

		encoder = _Encoder(distances)
		hist_script = list(hists) if hists is not None else []

		paths_factory = importlib.import_module("paths_factory")
		config_ensure = importlib.import_module("config_ensure")
		notify = importlib.import_module("notify")
		video_capture_module = importlib.import_module("recorders.video_capture")
		cv2 = importlib.import_module("cv2")
		dlib = importlib.import_module("dlib")

		monkeypatch.setattr(paths_factory, "config_file_path", lambda: str(config_path))
		monkeypatch.setattr(paths_factory, "user_model_path", lambda user: str(model_path), raising=False)
		data_file = COMPARE_PY if data_files_present else str(tmp_path / "missing.dat")
		for helper in ("dlib_face_recognition_resnet_model_v1_path", "shape_predictor_5_face_landmarks_path",
		               "mmod_human_face_detector_path"):
			monkeypatch.setattr(paths_factory, helper, lambda: data_file, raising=False)
		monkeypatch.setattr(paths_factory, "dlib_data_dir_path", lambda: tmp_path / "dlib-data", raising=False)
		monkeypatch.setattr(config_ensure, "ensure_system_config", lambda *a, **k: None)
		_Notifier.events = []
		monkeypatch.setattr(notify, "AuthNotifier", _Notifier)
		_Camera.height, _Camera.width = camera_size
		_Camera.last = None
		monkeypatch.setattr(video_capture_module, "VideoCapture", _Camera)
		default_detector = lambda frame, upsample=0: [object()]
		monkeypatch.setattr(dlib, "get_frontal_face_detector", lambda: detector or default_detector)
		monkeypatch.setattr(dlib, "cnn_face_detection_model_v1", lambda path: cnn_detector)
		monkeypatch.setattr(dlib, "shape_predictor", lambda path: (lambda frame, rect: object()))
		monkeypatch.setattr(dlib, "face_recognition_model_v1", lambda path: encoder)
		monkeypatch.setattr(cv2, "createCLAHE",
		                    lambda **kwargs: types.SimpleNamespace(apply=lambda gs: gs))
		monkeypatch.setattr(cv2, "calcHist",
		                    lambda *a, **k: hist_script.pop(0) if hist_script else hist_default)
		monkeypatch.setattr(cv2, "resize", MagicMock(side_effect=lambda img, size, **kw: img))
		monkeypatch.setattr(cv2, "rotate", MagicMock(side_effect=lambda img, code: img))
		monkeypatch.setattr(subprocess, "Popen", popen)
		monkeypatch.setattr(atexit, "register", MagicMock())
		# cleanup() settles the V4L device for 0.35 s; not worth the wall time here.
		monkeypatch.setattr(time, "sleep", lambda seconds: _REAL_SLEEP(min(seconds, 0.001)))
		monkeypatch.setattr(_thread, "start_new_thread",
		                    lambda fn, args=(), kwargs=None: fn(*args, **(kwargs or {})))
		monkeypatch.setattr("sys.argv", ["compare.py", *argv])
		if display:
			monkeypatch.setenv("DISPLAY", ":0")
		else:
			monkeypatch.delenv("DISPLAY", raising=False)
			monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
		for target, name, value in (extra or ()):
			monkeypatch.setattr(target, name, value)

		previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
		try:
			with pytest.raises(SystemExit) as exit_info:
				runpy.run_path(COMPARE_PY, run_name="__main__")
		finally:
			for sig, handler in previous.items():
				signal.signal(sig, handler)
		return Run(exit_info.value.code, list(_Notifier.events), _Camera.last, encoder)

	return _run


class TestArguments:
	def test_no_username_aborts_with_12(self, run_compare):
		result = run_compare(argv=())
		assert result.code == 12
		assert result.events == []          # nothing was even configured

	def test_malformed_username_is_rejected_before_any_path_is_built(self, run_compare, capsys):
		result = run_compare(argv=("../../etc/shadow",))
		assert result.code == 12
		assert "Invalid username format" in capsys.readouterr().out
		assert result.camera is None

	def test_pam_service_reaches_the_notifier(self, run_compare):
		result = run_compare(argv=("alice", "sudo"))
		assert result.code == 0
		name, args, kwargs = result.events[0]
		assert (name, args, kwargs["service"]) == ("init", ("alice",), "sudo")

	def test_broken_language_preference_does_not_stop_authentication(self, run_compare):
		i18n = importlib.import_module("i18n")

		def broken():
			raise RuntimeError("preferences.ini unreadable")

		result = run_compare(extra=[(i18n, "reload_from_preferences", broken)])
		assert result.code == 0


class TestEarlyExits:
	def test_unrestorable_system_config_aborts_with_12(self, run_compare, monkeypatch, capsys):
		config_ensure = importlib.import_module("config_ensure")

		def fail(*args, **kwargs):
			raise OSError("read-only file system")

		# Applied after the harness stubs ensure_system_config, so it wins.
		result = run_compare(extra=[(config_ensure, "ensure_system_config", fail)])
		assert result.code == 12
		assert "Failed to restore /etc/ubuntu-hello/config.ini" in capsys.readouterr().out

	def test_missing_model_file_sends_the_no_model_card(self, run_compare):
		result = run_compare(models="missing")
		assert result.code == 10
		assert result.names() == ["init", "no_model"]
		assert result.camera is None        # never opened the camera for nothing

	def test_empty_model_file_sends_the_no_model_card(self, run_compare):
		result = run_compare(models=[])
		assert result.code == 10
		assert result.names()[-1] == "no_model"

	def test_missing_dlib_data_files_exit_1_and_release_the_camera(self, run_compare, capsys):
		result = run_compare(data_files_present=False)
		out = capsys.readouterr().out
		assert result.code == 1
		assert "sudo ./install.sh" in out
		assert "data files missing" in out
		assert result.camera.released == 1

	def test_disabled_notifications_are_reported_in_debug_mode(self, run_compare, monkeypatch, capsys):
		monkeypatch.setattr(_Notifier, "disabled_reason", "no session bus")
		result = run_compare(config={"debug": {"end_report": "true"}})
		assert result.code == 0
		assert "Notifications off: no session bus" in capsys.readouterr().out


class TestDarkCamera:
	def test_only_dark_frames_exit_13_with_the_too_dark_card(self, run_compare, capsys):
		result = run_compare(hist_default=DARK)
		assert result.code == 13
		assert "All frames were too dark" in capsys.readouterr().out
		_, args, _ = result.event("too_dark")
		average, threshold, valid = args
		assert threshold == 60.0
		assert valid > 0
		assert average == pytest.approx(90.0 / 97.0 * 100)
		assert result.camera.released == 1

	def test_black_frames_are_skipped_and_do_not_count_as_valid(self, run_compare):
		result = run_compare(hists=[BLACK, BLACK, BLACK], distances=[1.0])
		assert result.code == 0
		_, args, kwargs = result.event("success")
		assert args[3] == 4                 # three black frames + the matching one
		assert kwargs["dark_frames"] == 0

	def test_dark_frames_are_counted_in_the_success_card(self, run_compare):
		result = run_compare(hists=[DARK, DARK], distances=[1.0])
		assert result.code == 0
		assert result.event("success")[2]["dark_frames"] == 2


class TestAuthOverlay:
	def test_overlay_receives_progress_and_is_torn_down(self, run_compare):
		result = run_compare(display=True, popen=_GtkProc, hists=[DARK, DARK, DARK], distances=[1.0])
		assert result.code == 0
		proc = _GtkProc.last
		assert proc.argv == [compare.GTK_BIN_PATH, "--start-auth-ui"]
		assert proc.kwargs["stdout"] is subprocess.DEVNULL
		assert proc.written[0] == "M=Starting up... \n"
		assert "M=Identifying you... \n" in proc.written
		assert "S=Scanned 0 frames (skipped 2 dark frames) \n" in proc.written
		assert proc.terminated
		atexit.register.assert_called_once()

	def test_gtk_stdout_debug_pipes_the_overlay_to_the_terminal(self, run_compare):
		run_compare(display=True, popen=_GtkProc, config={"debug": {"gtk_stdout": "true"}})
		assert _GtkProc.last.kwargs["stdout"] is sys.stdout

	def test_a_dead_overlay_pipe_does_not_break_the_scan(self, run_compare):
		class _BrokenPipe(_GtkProc):
			def write(self, data):
				raise BrokenPipeError("overlay closed")

		result = run_compare(display=True, popen=_BrokenPipe)
		assert result.code == 0
		assert _GtkProc.last.written == []

	def test_missing_overlay_binary_is_not_fatal(self, run_compare):
		result = run_compare(display=True, popen=_no_overlay)
		assert result.code == 0
		atexit.register.assert_not_called()


class TestSessionIdleWatcher:
	def test_watcher_thread_starts_when_enabled(self, run_compare):
		started = []

		class _Thread:
			def __init__(self, target=None, daemon=None):
				self.target, self.daemon = target, daemon

			def start(self):
				started.append((self.target.__name__, self.daemon))

		result = run_compare(config={"core": {"abort_on_session_idle": "true"}},
		                     extra=[(threading, "Thread", _Thread)])
		assert result.code == 0
		assert started == [("_watch_session_idle", True)]


class TestFrameGeometry:
	def test_tall_frames_are_scaled_down_to_max_height(self, run_compare):
		cv2 = importlib.import_module("cv2")
		result = run_compare(camera_size=(640, 640))
		assert result.code == 0
		fx = [c.kwargs["fx"] for c in cv2.resize.call_args_list]
		assert fx == [0.5, 0.5]            # colour + greyscale frame

	def test_rotate_1_alternates_both_portrait_orientations(self, run_compare):
		cv2 = importlib.import_module("cv2")
		result = run_compare(config={"video": {"rotate": "1"}}, distances=[9.0, 9.0, 9.0, 1.0])
		assert result.code == 0
		codes = [c.args[1] for c in cv2.rotate.call_args_list]
		# frame 1: counter-clockwise, 2: clockwise, 3: as captured, 4: counter-clockwise
		assert codes == [cv2.ROTATE_90_COUNTERCLOCKWISE] * 2 + [cv2.ROTATE_90_CLOCKWISE] * 2 \
			+ [cv2.ROTATE_90_COUNTERCLOCKWISE] * 2

	def test_rotate_2_uses_width_as_height_and_turns_every_frame(self, run_compare):
		cv2 = importlib.import_module("cv2")
		result = run_compare(config={"video": {"rotate": "2"}}, camera_size=(320, 640),
		                     distances=[9.0, 1.0])
		assert result.code == 0
		# Height is read from the frame width (640) for a portrait-mounted camera.
		assert [c.kwargs["fx"] for c in cv2.resize.call_args_list][0] == 0.5
		codes = [c.args[1] for c in cv2.rotate.call_args_list]
		assert codes == [cv2.ROTATE_90_CLOCKWISE] * 2 + [cv2.ROTATE_90_COUNTERCLOCKWISE] * 2

	def test_cnn_detector_results_are_unwrapped_to_rectangles(self, run_compare):
		seen = []

		class _CnnHit:
			rect = "rect-from-cnn"

		def predictor_factory(path):
			def predictor(frame, rect):
				seen.append(rect)
				return object()
			return predictor

		dlib = importlib.import_module("dlib")
		result = run_compare(config={"core": {"use_cnn": "true"}},
		                     cnn_detector=lambda frame, upsample=0: [_CnnHit()],
		                     extra=[(dlib, "shape_predictor", predictor_factory)])
		assert result.code == 0
		assert seen == ["rect-from-cnn"]


class TestTimeouts:
	def test_no_match_times_out_with_best_certainty_and_snapshot(self, run_compare, monkeypatch):
		snapshot = importlib.import_module("snapshot")
		generate = MagicMock()
		monkeypatch.setattr(snapshot, "generate", generate)
		result = run_compare(config={"snapshots": {"save_failed": "true"}}, distances=[5.0, 4.0])
		assert result.code == 11
		_, args, kwargs = result.event("timeout")
		best, threshold, frames, elapsed, timeout_s = args
		assert best == pytest.approx(4.0)
		assert threshold == pytest.approx(1.8)
		assert frames >= 2
		assert timeout_s == 1
		assert kwargs["dark_frames"] == 0
		frames_arg, lines = generate.call_args.args
		assert len(frames_arg) == 3        # snapshot keeps the first three frames
		assert lines[0] == "FAILED LOGIN"
		assert lines[-1] == "Best certainty value: 4.0"

	def test_timeout_report_explains_unmet_confirmations(self, run_compare, capsys):
		result = run_compare(config={"video": {"confirmations": "3"}, "debug": {"end_report": "true"}},
		                     distances=[1.0])
		assert result.code == 11
		assert "Frames agreeing with the model: 1 (this level needs 3)" in capsys.readouterr().out

	def test_no_face_at_all_reports_no_best_certainty(self, run_compare):
		result = run_compare(detector=lambda frame, upsample=0: [])
		assert result.code == 11
		assert result.event("timeout")[1][0] is None

	def test_negative_acquisition_timeout_is_derived_from_timeout(self, run_compare):
		result = run_compare(config={"video": {"acquisition_timeout": "-1", "timeout": "2"}})
		assert result.code == 0
		# max(2 * 2, 2 + 6) = 8 s of warm-up on top of the 2 s scan window
		assert result.event("start")[2]["max_seconds"] == 10.0
		assert result.event("start")[2]["device"] == "/dev/video2"
		assert result.event("start")[2]["resolution"] == "320x320"


class TestSuccess:
	def test_end_report_prints_the_timing_and_winning_model(self, run_compare, capsys):
		result = run_compare(config={"debug": {"end_report": "true"}}, distances=[1.25])
		out = capsys.readouterr().out
		assert result.code == 0
		assert "Time spent" in out
		assert "Certainty of winning frame: 1.250" in out
		assert 'Winning model: 0 ("office")' in out
		assert "Used: 320x320" in out

	def test_success_card_names_the_matching_model(self, run_compare):
		models = [
			{"time": 0, "label": "office", "id": 0, "data": [[5.0] * 128]},
			{"time": 0, "label": "glasses", "id": 1, "data": [[0.0] * 128]},
		]
		result = run_compare(models=models, distances=[1.0])
		assert result.code == 0
		_, args, _ = result.event("success")
		assert args[0] == pytest.approx(1.0)
		assert args[2] == "glasses"
		assert result.camera.released == 1

	def test_successful_snapshot_is_written(self, run_compare, monkeypatch):
		snapshot = importlib.import_module("snapshot")
		generate = MagicMock()
		monkeypatch.setattr(snapshot, "generate", generate)
		result = run_compare(config={"snapshots": {"save_successful": "true"}})
		assert result.code == 0
		frames_arg, lines = generate.call_args.args
		assert len(frames_arg) == 1
		assert lines[0] == "SUCCESSFUL LOGIN"
		assert lines[4].startswith("Hostname: ")

	def test_manual_exposure_is_reapplied_every_frame(self, run_compare):
		cv2 = importlib.import_module("cv2")
		result = run_compare(config={"video": {"exposure": "120"}}, distances=[9.0, 9.0, 1.0])
		assert result.code == 0
		# Two non-matching frames reach the exposure step; the winning one exits first.
		assert result.camera.settings == [
			(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0), (cv2.CAP_PROP_EXPOSURE, 120.0),
		] * 2


class TestRubberstamps:
	def _stamps(self, monkeypatch, outcome):
		calls = []

		def execute(config, gtk_proc, opencv, notifier=None):
			calls.append((gtk_proc, sorted(opencv), notifier))
			if outcome is not None:
				raise SystemExit(outcome)

		monkeypatch.setitem(sys.modules, "rubberstamps", types.SimpleNamespace(execute=execute))
		return calls

	def test_passing_stamps_send_the_success_card(self, run_compare, monkeypatch):
		calls = self._stamps(monkeypatch, 0)
		result = run_compare(config={"rubberstamps": {"enabled": "true"}})
		assert result.code == 0
		assert result.names()[-1] == "success"
		gtk_proc, keys, notifier = calls[0]
		assert gtk_proc is None             # no overlay without a display
		assert keys == ["clahe", "face_detector", "pose_predictor", "video_capture"]
		assert isinstance(notifier, _Notifier)

	def test_rejecting_stamp_sends_the_rejected_card_not_success(self, run_compare, monkeypatch):
		self._stamps(monkeypatch, 15)
		result = run_compare(config={"rubberstamps": {"enabled": "true"}})
		assert result.code == 15
		assert "success" not in result.names()
		assert result.names()[-1] == "rejected"

	def test_stamps_returning_still_authenticate(self, run_compare, monkeypatch):
		self._stamps(monkeypatch, None)
		result = run_compare(config={"rubberstamps": {"enabled": "true"}}, display=True, popen=_GtkProc)
		assert result.code == 0
		assert result.names()[-1] == "success"
		assert "S= \n" in _GtkProc.last.written   # subtext cleared before the stamps run


@pytest.fixture
def compare_mod(monkeypatch):
	"""compare's helpers with fresh module state."""
	sys.modules.pop("compare", None)
	module = importlib.import_module("compare")
	module._cleaned_up = False
	module.video_capture = None
	if "gtk_proc" in vars(module):
		delattr(module, "gtk_proc")
	monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
	return module


class TestHelperErrorBranches:
	def test_cleanup_survives_a_camera_that_fails_to_release(self, compare_mod):
		camera = MagicMock()
		camera.release.side_effect = OSError("device gone")
		compare_mod.video_capture = camera
		compare_mod.cleanup()
		camera.release.assert_called_once()
		assert compare_mod.video_capture is None
		assert compare_mod._cleaned_up is True

	def test_cleanup_survives_a_failing_settle_sleep(self, compare_mod, monkeypatch):
		def interrupted(seconds):
			raise InterruptedError()

		monkeypatch.setattr(compare_mod.time, "sleep", interrupted)
		compare_mod.video_capture = MagicMock()
		compare_mod.gtk_proc = MagicMock()
		compare_mod.cleanup()
		compare_mod.gtk_proc.terminate.assert_called_once()

	def test_cleanup_survives_an_overlay_that_cannot_be_terminated(self, compare_mod):
		proc = MagicMock()
		proc.terminate.side_effect = ProcessLookupError()
		compare_mod.gtk_proc = proc
		compare_mod.cleanup()
		proc.wait.assert_not_called()
		assert compare_mod._cleaned_up is True

	def test_exit_without_code_only_cleans_up(self, compare_mod):
		camera = MagicMock()
		compare_mod.video_capture = camera
		assert compare_mod.exit() is None
		camera.release.assert_called_once()

	def test_session_idle_hint_rejects_unexpected_session_path_output(self, compare_mod, monkeypatch):
		calls = []

		def fake_run(cmd, **kwargs):
			calls.append(cmd)
			return subprocess.CompletedProcess(cmd, 0, stdout="s \"not-an-object-path\"\n")

		monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
		assert compare_mod._session_idle_hint() is None
		assert len(calls) == 1              # never asked for IdleHint on a bogus path

	def test_session_idle_hint_is_undetermined_when_the_property_read_fails(self, compare_mod, monkeypatch):
		def fake_run(cmd, **kwargs):
			if "GetSessionByPID" in cmd:
				return subprocess.CompletedProcess(cmd, 0, stdout='o "/org/freedesktop/login1/session/_2"\n')
			return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Access denied")

		monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
		assert compare_mod._session_idle_hint() is None

	def test_send_to_ui_is_a_noop_without_an_overlay(self, compare_mod):
		assert "gtk_proc" not in vars(compare_mod)
		assert compare_mod.send_to_ui("M", "hello") is None

	def test_send_to_ui_skips_an_overlay_that_already_exited(self, compare_mod):
		proc = MagicMock()
		proc.poll.return_value = 1
		compare_mod.gtk_proc = proc
		compare_mod.send_to_ui("M", "hello")
		proc.stdin.write.assert_not_called()

	def test_make_snapshot_falls_back_to_now_without_scan_timings(self, compare_mod, monkeypatch):
		generate = MagicMock()
		monkeypatch.setattr(compare_mod.snapshot, "generate", generate)
		monkeypatch.setattr(compare_mod, "timings", {"st": 0.0}, raising=False)
		monkeypatch.setattr(compare_mod, "snapframes", ["f"], raising=False)
		monkeypatch.setattr(compare_mod, "frames", 5, raising=False)
		monkeypatch.setattr(compare_mod, "lowest_certainty", 0.25, raising=False)
		compare_mod.make_snapshot("TEST")
		frames_arg, lines = generate.call_args.args
		assert frames_arg == ["f"]
		assert lines[0] == "TEST LOGIN"
		assert lines[3].startswith("Frames: 5 (")
		assert lines[-1] == "Best certainty value: 2.5"
