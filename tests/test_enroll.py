"""GTK-side runner for `ubuntu-hello add` with live guidance (ubuntu-hello-gtk/src/enroll.py)."""
import subprocess
import threading

import pytest

import enroll


class FakeProc:
	def __init__(self, lines, status=0):
		self.stdout = iter(lines)
		self.returncode = status

	def wait(self):
		return self.returncode


def _sync_dispatch(fn, *args):
	fn(*args)
	return 0


def test_parse_line_protocol():
	assert enroll.parse_line("@guide left\n") == ("guide", "left")
	assert enroll.parse_line("@progress 3/13") == ("progress", (3, 13))
	assert enroll.parse_line("@progress nope") is None
	assert enroll.parse_line("@guide ") is None
	assert enroll.parse_line("Please look straight into the camera") is None
	assert enroll.is_protocol_line("  @guide x") and not enroll.is_protocol_line("hello")


def test_guide_text_matches_every_cli_step_key():
	import enroll_capture
	for key, _prompt in enroll_capture.GUIDE_STEPS:
		assert enroll.guide_text(key), key
	assert enroll.guide_text("bogus") == ""
	assert enroll.progress_text(3, 13) == "Recording… 3 of 13"


def test_run_add_streams_guidance_and_reports_clean_output():
	lines = [
		"@guide center\n",
		"Please look straight into the camera\n",
		"@progress 1/13\n",
		"@guide left\n",
		"Turn your head slightly to the left\n",
		"@progress 2/13\n",
		"@progress 3/13\n",
		"Scan complete\n",
	]
	seen = {"guide": [], "progress": [], "done": None}
	calls = {}

	def popen(cmd, **kw):
		calls["cmd"], calls["kw"] = cmd, kw
		return FakeProc(lines, 0)

	t = enroll.run_add(["ubuntu-hello", "-y", "add", "L"],
	                   lambda k: seen["guide"].append(k),
	                   lambda c, tot: seen["progress"].append((c, tot)),
	                   lambda s, out: seen.__setitem__("done", (s, out)),
	                   popen=popen, dispatch=_sync_dispatch)
	t.join(5)
	assert calls["cmd"] == ["ubuntu-hello", "-y", "add", "L"]
	assert calls["kw"]["stdout"] is subprocess.PIPE and calls["kw"]["stderr"] is subprocess.STDOUT
	assert calls["kw"]["text"] is True and calls["kw"]["bufsize"] == 1  # line-buffered: live updates
	assert seen["guide"] == ["center", "left"]
	assert seen["progress"] == [(1, 13), (2, 13), (3, 13)]
	status, output = seen["done"]
	assert status == 0
	assert "@" not in output  # protocol lines never reach the error dialog
	assert "Please look straight into the camera" in output and "Scan complete" in output


def test_run_add_failure_status_and_output():
	done = []
	enroll.run_add(["x"], lambda k: None, lambda c, t: None, lambda s, o: done.append((s, o)),
	               popen=lambda *a, **k: FakeProc(["No face detected, aborting\n"], 1),
	               dispatch=_sync_dispatch).join(5)
	assert done == [(1, "No face detected, aborting\n")]


def test_run_add_missing_binary_reports_127():
	done = []

	def popen(*a, **k):
		raise FileNotFoundError("ubuntu-hello")

	enroll.run_add(["ubuntu-hello"], lambda k: None, lambda c, t: None, lambda s, o: done.append((s, o)),
	               popen=popen, dispatch=_sync_dispatch).join(5)
	assert done[0][0] == 127 and "ubuntu-hello" in done[0][1]


def test_run_add_runs_off_the_main_thread_and_uses_idle_add_by_default(monkeypatch):
	"""The old subprocess.run(capture_output=True) froze the GTK main loop for the whole scan."""
	dispatched = []
	monkeypatch.setattr(enroll.GLib, "idle_add", lambda fn, *a: dispatched.append((fn, a)) or 0)
	worker_thread = {}

	def popen(*a, **k):
		worker_thread["name"] = threading.current_thread().name
		return FakeProc(["@progress 1/13\n"], 0)

	t = enroll.run_add(["x"], lambda k: None, lambda c, tot: None, lambda s, o: None, popen=popen)
	t.join(5)
	assert worker_thread["name"] != threading.main_thread().name
	assert [a for fn, a in dispatched] == [(1, 13), (0, "")]
	assert t.daemon


@pytest.mark.parametrize("key", ["center", "left", "right", "up", "down"])
def test_guide_texts_are_short_single_line_prompts(key):
	text = enroll.guide_text(key)
	assert "\n" not in text and len(text) < 60
