"""Tests for compare.py camera/GTK teardown on exit and SIGTERM."""

import os
import signal
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def compare_mod(monkeypatch):
    """Import compare helpers without running the auth loop."""
    # Heavy native deps are mocked in conftest; still avoid accidental main()
    monkeypatch.setenv("BYPASS_ELEVATE", "1")
    # Ensure a clean import each time
    sys.modules.pop("compare", None)
    import compare

    # Reset module-level cleanup state between tests
    compare._cleaned_up = False
    compare.video_capture = None
    if "gtk_proc" in vars(compare):
        delattr(compare, "gtk_proc")
    return compare


def test_cleanup_releases_camera_and_terminates_gtk(compare_mod):
    mock_capture = MagicMock()
    mock_gtk = MagicMock()
    mock_gtk.wait = MagicMock()

    compare_mod.video_capture = mock_capture
    compare_mod.gtk_proc = mock_gtk

    compare_mod.cleanup()

    mock_capture.release.assert_called_once()
    mock_gtk.terminate.assert_called_once()
    mock_gtk.wait.assert_called()
    assert compare_mod.video_capture is None
    assert compare_mod._cleaned_up is True

    # Idempotent: second cleanup must not touch mocks again
    mock_capture.reset_mock()
    mock_gtk.reset_mock()
    compare_mod.cleanup()
    mock_capture.release.assert_not_called()
    mock_gtk.terminate.assert_not_called()


def test_exit_runs_cleanup_then_sys_exit(compare_mod, monkeypatch):
    mock_capture = MagicMock()
    mock_gtk = MagicMock()
    compare_mod.video_capture = mock_capture
    compare_mod.gtk_proc = mock_gtk

    with pytest.raises(SystemExit) as exc:
        compare_mod.exit(11)

    assert exc.value.code == 11
    mock_capture.release.assert_called_once()
    mock_gtk.terminate.assert_called_once()


def test_sigterm_handler_cleans_up_and_aborts(compare_mod, monkeypatch):
    mock_capture = MagicMock()
    mock_gtk = MagicMock()
    compare_mod.video_capture = mock_capture
    compare_mod.gtk_proc = mock_gtk

    exited = {}

    def fake_exit(code):
        exited["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(compare_mod.os, "_exit", fake_exit)

    with pytest.raises(SystemExit) as exc:
        compare_mod._signal_exit(signal.SIGTERM, None)

    assert exc.value.code == 12
    assert exited["code"] == 12
    mock_capture.release.assert_called_once()
    mock_gtk.terminate.assert_called_once()


def test_install_parent_death_signal_success(compare_mod, monkeypatch):
    calls = []

    class FakeLibc:
        def prctl(self, option, sig):
            calls.append((option, sig))
            return 0

    import ctypes

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: FakeLibc())
    assert compare_mod.install_parent_death_signal(signal.SIGTERM) is True
    assert calls == [(1, signal.SIGTERM)]


def test_install_parent_death_signal_failure(compare_mod, monkeypatch):
    import ctypes

    def boom(*_a, **_k):
        raise OSError("no libc")

    monkeypatch.setattr(ctypes, "CDLL", boom)
    assert compare_mod.install_parent_death_signal() is False


def test_cleanup_kills_gtk_if_terminate_hangs(compare_mod):
    import subprocess

    mock_capture = MagicMock()
    mock_gtk = MagicMock()
    calls = {"n": 0}

    def wait_side_effect(timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="gtk", timeout=timeout)
        return 0

    mock_gtk.wait.side_effect = wait_side_effect
    compare_mod.video_capture = mock_capture
    compare_mod.gtk_proc = mock_gtk

    compare_mod.cleanup()

    mock_gtk.terminate.assert_called_once()
    mock_gtk.kill.assert_called_once()
    mock_capture.release.assert_called_once()
    assert mock_gtk.wait.call_count == 2


def test_session_idle_hint_uses_absolute_busctl_path(compare_mod, monkeypatch):
    # compare.py runs as root during PAM authentication, so this must not
    # resolve "busctl" via an inherited PATH.
    import subprocess

    seen_argv0 = []

    def fake_run(cmd, **kwargs):
        seen_argv0.append(cmd[0])
        if "GetSessionByPID" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='o "/org/freedesktop/login1/session/_31"\n')
        return subprocess.CompletedProcess(cmd, 0, stdout="b true\n")

    monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
    compare_mod._session_idle_hint()

    assert seen_argv0 == [compare_mod.BUSCTL_PATH, compare_mod.BUSCTL_PATH]
    assert compare_mod.BUSCTL_PATH.startswith("/")


def test_session_idle_hint_parses_true(compare_mod, monkeypatch):
    import subprocess

    def fake_run(cmd, **kwargs):
        if "GetSessionByPID" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='o "/org/freedesktop/login1/session/_31"\n')
        if "get-property" in cmd:
            assert cmd[cmd.index("get-property") + 1] == "org.freedesktop.login1"
            assert "/org/freedesktop/login1/session/_31" in cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="b true\n")
        raise AssertionError(f"unexpected busctl invocation: {cmd}")

    monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
    assert compare_mod._session_idle_hint() is True


def test_session_idle_hint_parses_false(compare_mod, monkeypatch):
    import subprocess

    def fake_run(cmd, **kwargs):
        if "GetSessionByPID" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='o "/org/freedesktop/login1/session/_31"\n')
        return subprocess.CompletedProcess(cmd, 0, stdout="b false\n")

    monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
    assert compare_mod._session_idle_hint() is False


def test_session_idle_hint_returns_none_on_busctl_failure(compare_mod, monkeypatch):
    import subprocess

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
    assert compare_mod._session_idle_hint() is None


def test_session_idle_hint_returns_none_on_missing_busctl(compare_mod, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("busctl not found")

    monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
    assert compare_mod._session_idle_hint() is None


@pytest.mark.parametrize("malformed_stdout", [
    "s true\n",       # wrong D-Bus type signature
    "x true\n",
    "true\n",          # missing type prefix
    "b true extra\n",  # trailing garbage
    "b maybe\n",       # not a boolean literal
    "\n",
    "",
])
def test_session_idle_hint_returns_none_on_malformed_output(compare_mod, monkeypatch, malformed_stdout):
    import subprocess

    def fake_run(cmd, **kwargs):
        if "GetSessionByPID" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='o "/org/freedesktop/login1/session/_31"\n')
        return subprocess.CompletedProcess(cmd, 0, stdout=malformed_stdout)

    monkeypatch.setattr(compare_mod.subprocess, "run", fake_run)
    # Malformed output must never be interpreted as True (that would abort a
    # legitimate scan) -- it must come back as "undetermined".
    assert compare_mod._session_idle_hint() is None


def test_watch_session_idle_sends_sigterm_on_idle_transition(compare_mod, monkeypatch):
    # Not-idle first, then idle: a genuine False -> True transition should
    # abort. Cleanup must NOT be called directly on this thread (that would
    # race the main thread's concurrent camera/GTK access) -- instead the
    # process is sent SIGTERM so the existing main-thread signal handler
    # performs cleanup.
    readings = iter([False, True])
    monkeypatch.setattr(compare_mod, "_session_idle_hint", lambda: next(readings))
    monkeypatch.setattr(compare_mod.time, "sleep", lambda *_a: None)

    sent = {}

    def fake_kill(pid, sig):
        sent["pid"] = pid
        sent["sig"] = sig

    monkeypatch.setattr(compare_mod.os, "kill", fake_kill)
    monkeypatch.setattr(compare_mod.os, "getpid", lambda: 4242)

    compare_mod._watch_session_idle(poll_interval=0)

    assert sent == {"pid": 4242, "sig": compare_mod.signal.SIGTERM}


def test_watch_session_idle_does_not_abort_when_already_idle_at_start(compare_mod, monkeypatch):
    # Waking a lock screen commonly starts a new auth attempt while IdleHint
    # is still true from before it was cleared -- that must NOT be treated
    # as a cancel signal, or every normal wake-and-unlock would self-abort.
    call_count = {"n": 0}

    def fake_hint():
        call_count["n"] += 1
        if call_count["n"] > 5:
            # Stop the test's loop; real code only exits via SIGTERM or a
            # None run of failures, so simulate the scan finishing instead.
            compare_mod._cleaned_up = True
        return True

    monkeypatch.setattr(compare_mod, "_session_idle_hint", fake_hint)
    monkeypatch.setattr(compare_mod.time, "sleep", lambda *_a: None)

    sent = {"called": False}
    monkeypatch.setattr(compare_mod.os, "kill", lambda *a: sent.__setitem__("called", True))

    compare_mod._watch_session_idle(poll_interval=0)

    assert sent["called"] is False


def test_watch_session_idle_does_not_signal_once_cleanup_already_ran(compare_mod, monkeypatch):
    # _session_idle_hint() can block for a couple of seconds (two busctl
    # calls). If the main thread independently finishes a successful/failed
    # exit (which sets _cleaned_up) *during* that blocking call, the watcher
    # must not fire SIGTERM once it gets a stale/late "idle" reading --
    # otherwise it can turn a successful exit(0) into an aborted exit(12).
    readings = iter([False, True])

    def fake_hint():
        value = next(readings)
        if value is True:
            # Simulate cleanup() completing concurrently while this call
            # was "blocked" in the real busctl subprocess calls.
            compare_mod._cleaned_up = True
        return value

    monkeypatch.setattr(compare_mod, "_session_idle_hint", fake_hint)
    monkeypatch.setattr(compare_mod.time, "sleep", lambda *_a: None)

    sent = {"called": False}
    monkeypatch.setattr(compare_mod.os, "kill", lambda *a: sent.__setitem__("called", True))

    compare_mod._watch_session_idle(poll_interval=0)

    assert sent["called"] is False


def test_signal_exit_is_noop_once_cleanup_already_ran(compare_mod, monkeypatch):
    # Backstop for the same race from the signal-handler side: if cleanup()
    # already ran (e.g. a natural successful exit already decided the exit
    # code, or another signal got here first), a SIGTERM arriving after that
    # must not call cleanup() again or force os._exit(12) -- that would
    # override an already-decided exit code.
    compare_mod._cleaned_up = True

    cleanup_calls = []
    exit_calls = []
    monkeypatch.setattr(compare_mod, "cleanup", lambda: cleanup_calls.append(1))
    monkeypatch.setattr(compare_mod.os, "_exit", lambda code: exit_calls.append(code))

    compare_mod._signal_exit(compare_mod.signal.SIGTERM, None)

    assert cleanup_calls == []
    assert exit_calls == []


def test_watch_session_idle_gives_up_after_repeated_failures(compare_mod, monkeypatch):
    calls = {"n": 0}

    def fake_hint():
        calls["n"] += 1
        return None

    monkeypatch.setattr(compare_mod, "_session_idle_hint", fake_hint)
    monkeypatch.setattr(compare_mod.time, "sleep", lambda *_a: None)

    # Must return on its own (not loop forever) once idle state can't be
    # determined a few times in a row.
    compare_mod._watch_session_idle(poll_interval=0)
    assert calls["n"] == 3


def test_watch_session_idle_never_raises_on_unexpected_error(compare_mod, monkeypatch):
    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(compare_mod, "_session_idle_hint", boom)

    # Must not propagate -- this runs on a daemon thread and must never
    # crash or interfere with the normal auth flow.
    compare_mod._watch_session_idle(poll_interval=0)


def test_auth_overlay_uses_absolute_gtk_path(compare_mod):
    """Root helper must never resolve ubuntu-hello-gtk via PATH (privesc)."""
    assert compare_mod.GTK_BIN_PATH == "/usr/bin/ubuntu-hello-gtk"
    src = open(compare_mod.__file__, encoding="utf-8").read()
    assert 'Popen(["ubuntu-hello-gtk"' not in src
    assert 'Popen([GTK_BIN_PATH, "--start-auth-ui"]' in src


def test_signal_exit_sends_cancelled_notification(compare_mod, monkeypatch):
    """SIGTERM mid-scan (Esc / session idle) updates the card to 'cancelled' before exiting."""
    notifier = MagicMock()
    compare_mod.notifier = notifier
    monkeypatch.setattr(compare_mod, "cleanup", MagicMock())
    monkeypatch.setattr(compare_mod.os, "_exit", MagicMock())
    compare_mod._signal_exit(signal.SIGTERM, None)
    notifier.cancelled.assert_called_once()
    compare_mod.os._exit.assert_called_once_with(12)


def test_notify_helper_is_noop_without_notifier_and_swallows_errors(compare_mod, capsys):
    compare_mod.notifier = None
    compare_mod._notify("success")  # no notifier -> nothing
    broken = MagicMock()
    broken.success.side_effect = RuntimeError("bus gone")
    compare_mod.notifier = broken
    compare_mod._notify("success", 1, 2)  # must never raise into the auth path
    assert "Notification failed" in capsys.readouterr().out


def test_success_card_is_sent_only_after_liveness_decides(compare_mod):
    """Source-level guard for the review finding: rubberstamps.execute() exits
    the process itself (0 = all stamps passed, 15 = rejected); the success
    card must be emitted inside that SystemExit handling, never before, and
    exit 15 must produce the rejection card."""
    src = open(compare_mod.__file__, encoding="utf-8").read()
    stamps = src.index("rubberstamps.execute(config, gtk_proc, {")
    handler = src.index("except SystemExit as stamp_exit:", stamps)
    assert 'notify_success()' in src[handler:handler + 400]
    assert '_notify("rejected")' in src[handler:handler + 400]
    # The only success notification before the rubberstamp block is the deferred definition
    before = src[:stamps]
    assert before.count('_notify(\n\t\t\t\t\t\t"success"') == 1 and "def notify_success():" in before
    assert "notify_success()" not in before.split("def notify_success():")[1].split("# Make snapshot")[0]


def test_no_model_card_is_reachable(compare_mod):
    """The notifier is built before the model file is read, so a missing
    model produces the 'authentication unavailable' card (exit 10)."""
    src = open(compare_mod.__file__, encoding="utf-8").read()
    notifier_at = src.index("notifier = AuthNotifier(")
    models_at = src.index("models = json.load(open(paths_factory.user_model_path(user)))")
    assert notifier_at < models_at
    assert src.count('_notify("no_model")\n\t\texit(10)') == 2


def test_target_user_language_preference_applied_after_validation(compare_mod):
    src = open(compare_mod.__file__, encoding="utf-8").read()
    validate = src.index('print("Invalid username format")')
    pref = src.index('os.environ["UH_TARGET_USER"] = user')
    assert validate < pref < src.index("notifier = AuthNotifier(")
    assert "i18n.reload_from_preferences()" in src


def test_auth_overlay_is_skipped_without_a_display(compare_mod, monkeypatch):
	"""Under PAM the module gives compare.py no display; spawning the GTK overlay
	there crashed it and left an apport report on every headless / SSH sudo."""
	monkeypatch.delenv("DISPLAY", raising=False)
	monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
	assert compare_mod._display_available() is False
	monkeypatch.setenv("DISPLAY", ":0")
	assert compare_mod._display_available() is True
	monkeypatch.delenv("DISPLAY", raising=False)
	monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
	assert compare_mod._display_available() is True
