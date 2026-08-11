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
