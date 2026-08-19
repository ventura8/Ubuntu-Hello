"""Shared fixtures and setup for real GTK3 E2E test suites (xvfb).

These tests run against real gi.repository.Gtk and system components.
Run with:
    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/
"""
from __future__ import annotations

import os
import sys
import tempfile
import configparser
from pathlib import Path
import pytest

if os.environ.get("UH_REAL_GTK") != "1":
	pytest.skip("E2E tests require UH_REAL_GTK=1 (real gi.repository.Gtk)", allow_module_level=True)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import numpy as np

try:
	import cv2
except ImportError:
	pytest.skip("E2E tests require cv2 (python3-opencv)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
GTK_SRC = ROOT / "ubuntu-hello-gtk" / "src"
CORE_SRC = ROOT / "ubuntu-hello" / "src"

for p in (str(GTK_SRC), str(CORE_SRC)):
	if p not in sys.path:
		sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def default_e2e_environment(monkeypatch):
	"""Ensure baseline desktop environment variables for E2E tests."""
	monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
	monkeypatch.setenv("DESKTOP_SESSION", "ubuntu")
	monkeypatch.setenv("UH_DONT_AUTO_LAUNCH", "1")


@pytest.fixture(autouse=True)
def auto_dialog_response(monkeypatch):
	"""Auto-respond to modal dialogs during headless E2E testing to prevent hangs."""
	monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.OK)


@pytest.fixture
def gtk_pump():
	"""Helper to flush all pending GTK events and background callbacks."""
	import time
	def _pump(iterations: int = 10):
		for _ in range(iterations):
			while Gtk.events_pending():
				Gtk.main_iteration_do(False)
			time.sleep(0.01)
	return _pump


class FakeVideoCapture:
	def __init__(self, path=0, is_opened=True, frame=None):
		self.path = path
		self._is_opened = is_opened
		self.frame = frame if frame is not None else np.zeros((480, 640, 3), dtype=np.uint8)
		self.released = False

	def isOpened(self):
		return self._is_opened and not self.released

	def read(self):
		if not self.isOpened():
			return False, None
		return True, self.frame.copy()

	def release(self):
		self.released = True

	def get(self, prop_id):
		if prop_id == 3:  # cv2.CAP_PROP_FRAME_WIDTH
			return float(self.frame.shape[1])
		if prop_id == 4:  # cv2.CAP_PROP_FRAME_HEIGHT
			return float(self.frame.shape[0])
		return 0.0


@pytest.fixture
def fake_video_capture():
	return FakeVideoCapture


@pytest.fixture
def isolated_fs(tmp_path, monkeypatch):
	"""Establish an isolated /etc/ubuntu-hello filesystem layout."""
	etc_dir = tmp_path / "etc_ubuntu_hello"
	etc_dir.mkdir(parents=True, exist_ok=True)
	models_dir = etc_dir / "models"
	models_dir.mkdir(parents=True, exist_ok=True)
	keyring_dir = etc_dir / "keyring-keys"
	keyring_dir.mkdir(parents=True, exist_ok=True)
	tpm_dir = etc_dir / "tpm-keys"
	tpm_dir.mkdir(parents=True, exist_ok=True)
	pending_dir = etc_dir / "keyring-caching-pending"
	pending_dir.mkdir(parents=True, exist_ok=True)

	config_file = etc_dir / "config.ini"
	cfg = configparser.ConfigParser()
	cfg.add_section("core")
	cfg.set("core", "certainty", "3.5")
	cfg.set("core", "device_path", "/dev/video0")
	cfg.set("core", "workaround", "off")
	cfg.set("core", "skip_face_after_failure", "true")
	with open(config_file, "w", encoding="utf-8") as fh:
		cfg.write(fh)

	user_prefs = tmp_path / "preferences.ini"
	monkeypatch.setenv("UH_PREFERENCES_FILE", str(user_prefs))

	# Patch paths module
	import paths
	monkeypatch.setattr(paths, "config_dir", etc_dir)
	monkeypatch.setattr(paths, "user_models_dir", models_dir)

	import paths_factory
	monkeypatch.setattr(paths_factory, "config_file_path", lambda: str(config_file))
	monkeypatch.setattr(paths_factory, "user_models_dir_path", lambda: models_dir)
	monkeypatch.setattr(paths_factory, "keyring_keys_dir_path", lambda: str(keyring_dir))
	monkeypatch.setattr(paths_factory, "tpm_keys_dir_path", lambda: str(tpm_dir))
	monkeypatch.setattr(paths_factory, "keyring_pending_dir_path", lambda: str(pending_dir))

	return {
		"root": tmp_path,
		"etc": etc_dir,
		"models": models_dir,
		"keyring": keyring_dir,
		"tpm": tpm_dir,
		"pending": pending_dir,
		"config_file": config_file,
		"preferences_file": user_prefs,
	}


@pytest.fixture
def real_video_frames():
	"""Produce real OpenCV BGR and IR frames."""
	# 3-channel distinct RGB (color webcam)
	color_frame = np.zeros((480, 640, 3), dtype=np.uint8)
	color_frame[:, :, 0] = 200  # Blue
	color_frame[:, :, 1] = 50   # Green
	color_frame[:, :, 2] = 10   # Red

	# 3-channel identical channels (infrared webcam output in OpenCV BGR)
	ir_frame = np.zeros((480, 640, 3), dtype=np.uint8)
	ir_frame[:, :, 0] = 128
	ir_frame[:, :, 1] = 128
	ir_frame[:, :, 2] = 128

	return {
		"color": color_frame,
		"ir": ir_frame,
	}
