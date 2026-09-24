"""End-to-End scenario tests for the Setup Wizard (SUW / Onboarding).

Executes against real GTK 4 widgets, real Builder .ui XML parsing, and real video/crypto pipelines.
Run with:
    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_onboarding_e2e.py
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from pathlib import Path
import pytest
import numpy as np
import cv2

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

import onboarding
import gtk4compat
import paths_factory
import auth_helper
import keyring_crypto


class TestOnboardingWindowConstructAndTheme:
	def test_window_constructs_real_ui(self, isolated_fs, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			assert ob.window is not None
			assert ob.window.get_visible()
			assert ob.slidecontainer is not None
			assert ob.nextbutton is not None
			assert ob.nextbutton.get_visible()
			assert ob.version_label is not None
			assert ob.version_label.get_text()
			assert len(ob.slides) == 8
			assert ob.window.current_slide == 0
			assert ob.slides[0].get_visible()
			for i in range(1, 8):
				assert not ob.slides[i].get_visible()
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_centering_and_css_loaded(self, isolated_fs, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			gtk_pump()
			width, height = ob.window.get_default_size()
			assert width >= 750
			assert height >= 600
		finally:
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide1Datafiles:
	def test_slide1_cached_landmarks(self, isolated_fs, tmp_path, monkeypatch, gtk_pump):
		dlib_dir = tmp_path / "dlib-data"
		dlib_dir.mkdir(parents=True, exist_ok=True)
		(dlib_dir / "shape_predictor_5_face_landmarks.dat").write_text("model-data")
		monkeypatch.setattr(paths_factory, "dlib_data_dir_path", lambda: dlib_dir)

		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 1
			assert ob.slides[1].get_visible()
			assert "downloaded" in ob.downloadoutputlabel.get_text().casefold()
			assert ob.nextbutton.get_sensitive()
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_slide1_download_simulation(self, isolated_fs, tmp_path, monkeypatch, gtk_pump):
		dlib_dir = tmp_path / "dlib-data"
		dlib_dir.mkdir(parents=True, exist_ok=True)
		monkeypatch.setattr(paths_factory, "dlib_data_dir_path", lambda: dlib_dir)

		# Provide a mock install.sh script that outputs lines and exits 0
		install_sh = dlib_dir / "install.sh"
		install_sh.write_text("#!/bin/sh\necho 'Downloading landmarks...'\necho '100%'\nexit 0\n")
		install_sh.chmod(0o755)

		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.go_next_slide()
			# Wait for download thread and queue processing
			if ob.proc:
				ob.proc.wait(timeout=5)
			for _ in range(20):
				gtk_pump(10)
				if "done" in ob.downloadoutputlabel.get_text().casefold():
					break
				time.sleep(0.05)

			assert ob.window.current_slide == 1
			assert "done" in ob.downloadoutputlabel.get_text().casefold()
			assert ob.nextbutton.get_sensitive()
		finally:
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide2CameraScan:
	def test_scan_cameras_and_populate_camera_table(self, isolated_fs, real_video_frames, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			# Mock /dev/v4l/by-path listing with 2 devices
			fake_devices = ["pci-0000:00:14.0-usb-0:1:1.0-video-index0", "pci-0000:00:14.0-usb-0:2:1.0-video-index0"]
			orig_listdir = os.listdir

			def fake_listdir(*args, **kwargs):
				if args and "by-path" in str(args[0]):
					return list(fake_devices)
				return orig_listdir(*args, **kwargs)

			monkeypatch.setattr(os, "listdir", fake_listdir)
			import glob as _glob
			monkeypatch.setattr(_glob, "glob", lambda pattern: [])   # no real /dev/video* nodes in the test

			class FakeCapture:
				def __init__(self, path):
					self.path = str(path)

				def read(self):
					if "0:1:" in self.path:
						return True, real_video_frames["ir"].copy()
					return True, real_video_frames["color"].copy()

				def isOpened(self):
					return True

				def release(self):
					pass

				def get(self, prop):
					return 480 if prop == cv2.CAP_PROP_FRAME_HEIGHT else 640

			monkeypatch.setattr(cv2, "VideoCapture", lambda path: FakeCapture(str(path)))
			monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"E: ID_V4L_PRODUCT=IR Camera Pro\n")

			# Navigate to slide 2
			ob.window.current_slide = 1
			ob.go_next_slide()

			# Wait for scan thread to populate the camera table with 2 rows
			for _ in range(50):
				gtk_pump(10)
				if getattr(ob, "cameras", None) is not None and len(ob.cameras) == 2:
					break
				time.sleep(0.05)

			assert ob.window.current_slide == 2
			assert getattr(ob, "cameras", None) is not None
			model = ob.cameras
			assert len(model) == 2
			assert model[0][3] is True  # is_gray boolean
			assert model.selected_index() == 0
			assert ob.nextbutton.get_sensitive()
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide3IREmitter:
	def test_slide3_ir_camera_yes_flow(self, isolated_fs, fake_video_capture, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.cameras = gtk4compat.ColumnList(["Camera", "Recommended"])
			ob.cameras.append(["IR Camera", "Yes, compatible", "/dev/video0", True])
			ob.cameras.select(0)

			monkeypatch.setattr(cv2, "VideoCapture", lambda path: fake_video_capture(path))

			ob.window.current_slide = 2
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 3

			# User clicks Yes button
			yes_btn = ob.builder.get_object("leieyesbutton")
			ob.slide3_button_yes(yes_btn)
			gtk_pump()
			assert ob.window.current_slide == 4
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()

	def test_slide3_non_ir_camera_auto_skips(self, isolated_fs, fake_video_capture, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.cameras = gtk4compat.ColumnList(["Camera", "Recommended"])
			ob.cameras.append(["RGB Camera", "No, not infrared", "/dev/video1", False])
			ob.cameras.select(0)

			monkeypatch.setattr(cv2, "VideoCapture", lambda path: fake_video_capture(path))

			ob.window.current_slide = 2
			ob.go_next_slide()
			gtk_pump()
			# Automatically skips slide 3 directly to slide 4!
			assert ob.window.current_slide == 4
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide4And5FaceScan:
	def test_slide4_scan_button_runs_add(self, isolated_fs, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.cameras = gtk4compat.ColumnList(["Camera", "Recommended"])
			ob.cameras.append(["IR Camera", "Yes", "/dev/video0", True])
			ob.cameras.select(0)

			# Ensure subprocess Popen succeeds for ubuntu-hello set device_path
			real_popen = subprocess.Popen
			monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT))

			ob.window.current_slide = 3
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 4

			# Intercept ubuntu-hello add: replay the guided-capture protocol slowly
			# enough for the page to show every prompt (enroll.run_add streams stdout).
			added_labels = []
			guide_lines = ["@guide center\n", "Please look straight into the camera\n", "@progress 1/13\n",
			               "@guide left\n", "@progress 2/13\n", "@progress 3/13\n",
			               "@guide right\n", "@progress 4/13\n",
			               "@guide up\n", "@progress 5/13\n",
			               "@guide down\n", "@progress 6/13\n",
			               "Captured 6 face samples\nScan complete\n"]

			class FakeAdd:
				returncode = 0

				def __init__(self, cmd):
					added_labels.append(cmd[-1])

				@property
				def stdout(self):
					for line in guide_lines:
						time.sleep(0.03)
						yield line

				def wait(self):
					return 0

			def fake_popen(cmd, *a, **k):
				if "add" in cmd:
					return FakeAdd(cmd)
				return real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

			monkeypatch.setattr(subprocess, "Popen", fake_popen)
			instruction = ob.builder.get_object("slide4_instruction_label")
			seen_prompts, seen_buttons = [], []
			# Record every prompt the page showed (the main loop pumps may miss a fast one).
			real_guide = ob.on_scan_guide

			def recording_guide(key):
				result = real_guide(key)
				if instruction.get_visible():
					seen_prompts.append(instruction.get_text())
				return result
			monkeypatch.setattr(ob, "on_scan_guide", recording_guide)
			# Same for the button: polling its label between main-loop pumps misses a
			# value the worker sets and replaces in between, which is exactly what
			# happens when the machine is loaded. Record every label instead.

			# Pass 1: click scan, let the scheduled run_add fire
			scan_btn = ob.builder.get_object("scanbutton")
			real_set_label = scan_btn.set_label

			def recording_set_label(text):
				seen_buttons.append(text)
				return real_set_label(text)
			monkeypatch.setattr(scan_btn, "set_label", recording_set_label)
			skip_btn = ob.builder.get_object("skipsecondbutton")
			assert not skip_btn.get_visible()  # first scan is required; only the second can be skipped
			assert "few angles" in ob.builder.get_object("label5").get_text()  # guided capture explained up front
			assert "second, optional scan" in ob.builder.get_object("label5").get_text()
			ob.on_scanbutton_click(scan_btn)
			deadline = time.monotonic() + 20
			while time.monotonic() < deadline:
				gtk_pump(10)
				if ob.scan_pass == 2:
					break
				time.sleep(0.01)
			gtk_pump(10)

			# Live guidance (Windows Hello style) was shown on the page while add ran…
			for prompt in ("Look straight at the camera", "Turn your head slightly to the left",
			               "Turn your head slightly to the right", "Tilt your chin up a little",
			               "Tilt your chin down a little"):
				assert prompt in seen_prompts, (prompt, seen_prompts)
			assert any(b.startswith("Recording… ") and b.endswith(" of 13") for b in seen_buttons), seen_buttons
			# …and is hidden again once the model is saved
			assert not instruction.get_visible()

			# First model saved -> still on slide 4, now in second-model mode
			assert added_labels == ["Setup lighting 1"]
			assert ob.window.current_slide == 4
			assert ob.scan_pass == 2
			assert skip_btn.get_visible()
			assert scan_btn.get_sensitive()
			assert scan_btn.get_label() == "Scan second model"
			assert "Second scan" in ob.builder.get_object("label4").get_text()
			assert "change the light" in ob.builder.get_object("label5").get_text()
			assert "face login already works" in ob.builder.get_object("label5").get_text()

			# Pass 2: scan again -> advances to slide 5
			ob.on_scanbutton_click(scan_btn)
			for _ in range(200):
				gtk_pump(10)
				if ob.window.current_slide == 5:
					break
				time.sleep(0.02)

			assert added_labels == ["Setup lighting 1", "Setup lighting 2"]
			assert ob.window.current_slide == 5
			assert ob.nextbutton.get_sensitive()
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()

	def test_slide4_skip_second_model_advances(self, isolated_fs, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.cameras = gtk4compat.ColumnList(["Camera", "Recommended"])
			ob.cameras.append(["IR Camera", "Yes", "/dev/video0", True])
			ob.cameras.select(0)
			real_popen = subprocess.Popen
			monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
			monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))

			ob.window.current_slide = 3
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 4
			skip_btn = ob.builder.get_object("skipsecondbutton")
			assert not skip_btn.get_visible()

			# A skip click before any model is enrolled does nothing
			ob.on_skipsecondbutton_click(None)
			gtk_pump(10)
			assert ob.window.current_slide == 4
			assert ob.models_enrolled == 0

			ob.on_add_finished(0, "Scan complete\n")  # pass 1 saved -> second-scan mode, Skip appears
			gtk_pump(10)
			assert ob.scan_pass == 2
			assert ob.models_enrolled == 1
			assert skip_btn.get_visible()

			skip_btn.emit("clicked")
			for _ in range(40):
				gtk_pump(10)
				if ob.window.current_slide == 5:
					break
				time.sleep(0.02)
			assert ob.window.current_slide == 5
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()


class TestOnboardingNavigation:
	def test_cancel_only_on_first_page_and_back(self, isolated_fs, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			cancel = ob.builder.get_object("cancelbutton")
			back = ob.builder.get_object("backbutton")
			assert cancel.get_visible()
			assert not back.get_visible()

			# Slide 1 (datafiles already present -> no download)
			monkeypatch.setattr(os.path, "exists", lambda p: True)
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 1
			assert not cancel.get_visible()
			assert back.get_visible()

			back.emit("clicked")
			gtk_pump()
			assert ob.window.current_slide == 0
			assert ob.slides[0].get_visible()
			assert not ob.slides[1].get_visible()
			assert cancel.get_visible()
			assert not back.get_visible()
			assert ob.nextbutton.get_sensitive()
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide6KeyringUnlock:
	def test_slide6_user_detection_and_tpm_phrasing(self, isolated_fs, monkeypatch, gtk_pump):
		monkeypatch.setenv("SUDO_USER", "testuser")
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			user = ob.get_real_user()
			assert user == "testuser"

			ob.window.current_slide = 5
			ob.go_next_slide()
			gtk_pump(20)

			assert ob.window.current_slide == 6
			desc_label = ob.builder.get_object("keyring_desc_label")
			assert desc_label is not None
			text = desc_label.get_text() or ""
			assert "ubuntu hello" in text.casefold() or "keyring" in text.casefold() or "wallet" in text.casefold()
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_slide6_keyring_save_and_disable(self, isolated_fs, monkeypatch, gtk_pump):
		monkeypatch.setenv("SUDO_USER", "testuser")
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.window.current_slide = 6
			checkbox = ob.builder.get_object("keyring_checkbox")

			# 1. Unchecked -> cleans files and returns True
			checkbox.set_active(False)
			res = ob.validate_and_save_keyring()
			assert res is True

			# 2. Checked: Negative case - Dialog Cancelled
			checkbox.set_active(True)
			class CancelledDialog:
				def __init__(self, *a, **k):
					self.entry1 = Gtk.Entry()
					self.entry1.set_text("pass")
				def run(self):
					return Gtk.ResponseType.CANCEL
				def destroy(self):
					pass
			monkeypatch.setattr(onboarding, "KeyringPasswordDialog", CancelledDialog)
			assert ob.validate_and_save_keyring() is False

			# 3. Checked: Negative case - Empty Password
			class EmptyPassDialog:
				def __init__(self, *a, **k):
					self.entry1 = Gtk.Entry()
					self.entry1.set_text("")
				def run(self):
					return Gtk.ResponseType.OK
				def destroy(self):
					pass
			monkeypatch.setattr(onboarding, "KeyringPasswordDialog", EmptyPassDialog)
			assert ob.validate_and_save_keyring() is False

			# 4. Checked: Negative case - Password verification failed
			class ValidPassDialog:
				def __init__(self, *a, **k):
					self.entry1 = Gtk.Entry()
					self.entry1.set_text("secret")
				def run(self):
					return Gtk.ResponseType.OK
				def destroy(self):
					pass
			monkeypatch.setattr(onboarding, "KeyringPasswordDialog", ValidPassDialog)
			monkeypatch.setattr(auth_helper, "verify_user_password", lambda u, p: False)
			assert ob.validate_and_save_keyring() is False

			# 5. Checked: Negative case - Subprocess failure (non-zero return code)
			monkeypatch.setattr(auth_helper, "verify_user_password", lambda u, p: True)
			monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(["ubuntu-hello"], 1, stderr="Subprocess error"))
			assert ob.validate_and_save_keyring() is False

			# 6. Checked: Success case
			monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(["ubuntu-hello"], 0))
			assert ob.validate_and_save_keyring() is True
		finally:
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide7SensitivityFinish:
	def test_slide7_certainty_and_finish_button(self, isolated_fs, monkeypatch, gtk_pump, tmp_path):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.window.current_slide = 6
			ob.builder.get_object("keyring_checkbox").set_active(False)

			# The wizard writes the config directly through the comment-preserving
			# editor: `ubuntu-hello set` can only replace a key the file already has,
			# so a config written before `confirmations` existed would half-apply.
			config = tmp_path / "config.ini"
			config.write_text("[video]\ncertainty = 4.2\n", encoding="utf-8")
			monkeypatch.setattr(onboarding.paths_factory, "config_file_path", lambda: str(config))

			# Select balanced radio
			radio_balanced = ob.builder.get_object("radiobalanced")
			if radio_balanced:
				radio_balanced.set_active(True)

			ob.go_next_slide()
			gtk_pump()

			assert ob.window.current_slide == 7
			assert ob.slides[7].get_visible()
			finish_btn = ob.builder.get_object("finishbutton")
			assert finish_btn.get_visible()
			assert not ob.nextbutton.get_visible()
			# Arriving writes nothing: the level used to be saved the moment this
			# page appeared, so a choice made on it afterwards was lost.
			assert "certainty = 4.2" in config.read_text(encoding="utf-8")

			# The user changes their mind on the page itself, and turns the nod on.
			ob.builder.get_object("radiosecure").set_active(True)
			switch = ob.builder.get_object("liveness_switch")
			assert not switch.get_active(), "the nod ships off"
			switch.set_active(True)
			gtk_pump(20)

			ob.on_finishbutton_click(finish_btn)
			assert ob.completed is True
			# A level is two settings: how close the match must be, and how many
			# separate frames have to agree before it is trusted. Plus the nod.
			written = config.read_text(encoding="utf-8")
			assert "certainty = %s" % onboarding.SECURITY_PRESETS["radiosecure"] in written
			assert "confirmations = %d" % onboarding.SECURITY_CONFIRMATIONS["radiosecure"] in written
			assert "enabled = true" in written
			assert "failsafe" in written
			assert "faildeadly" not in written
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()
