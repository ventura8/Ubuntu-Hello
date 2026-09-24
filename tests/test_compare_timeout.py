"""Tests for compare recognition timeout warm-up accounting."""

from __future__ import annotations

from unittest.mock import MagicMock

import compare


def test_recognition_timeout_waits_for_first_usable_frame():
    # No usable frame yet: still within acquisition window
    assert (
        compare._recognition_timeout_kind(5.0, 0.0, None, timeout=4.0, acquisition_timeout=10.0)
        is None
    )
    # Acquisition exhausted with no usable frame
    assert (
        compare._recognition_timeout_kind(10.1, 0.0, None, timeout=4.0, acquisition_timeout=10.0)
        == "acquisition"
    )
    # Exact acquisition deadline
    assert (
        compare._recognition_timeout_kind(10.0, 0.0, None, timeout=4.0, acquisition_timeout=10.0)
        == "acquisition"
    )


def test_recognition_timeout_starts_after_scan_start():
    # Usable frame at t=6; recognition window is 4s → still ok at t=9
    assert (
        compare._recognition_timeout_kind(9.0, 0.0, 6.0, timeout=4.0, acquisition_timeout=10.0)
        is None
    )
    # Past recognition window
    assert (
        compare._recognition_timeout_kind(10.1, 0.0, 6.0, timeout=4.0, acquisition_timeout=10.0)
        == "recognition"
    )
    # Exact recognition deadline (scan_start + timeout)
    assert (
        compare._recognition_timeout_kind(10.0, 0.0, 6.0, timeout=4.0, acquisition_timeout=10.0)
        == "recognition"
    )


def test_cleanup_settles_after_camera_release(monkeypatch):
    # monkeypatch restores every attribute below (and re-creates gtk_proc if it
    # was there), so the hand-rolled save/try/finally block is not needed.
    monkeypatch.setattr(compare.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(compare, "_cleaned_up", False, raising=False)
    mock_cap = MagicMock()
    monkeypatch.setattr(compare, "video_capture", mock_cap, raising=False)
    monkeypatch.delattr(compare, "gtk_proc", raising=False)

    compare.cleanup()

    mock_cap.release.assert_called_once()
    assert compare.video_capture is None
