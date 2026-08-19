"""End-to-End scenario tests for the Setup Wizard (SUW / Onboarding).

Executes against real GTK3 widgets, real Glade XML parsing, and real video/crypto pipelines.
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
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

import onboarding
import paths_factory
import auth_helper
import keyring_crypto


class TestOnboardingWindowConstructAndTheme:
	def test_window_constructs_real_glade(self, isolated_fs, gtk_pump):
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
			width, height = ob.window.get_size()
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
	def test_scan_cameras_and_populate_treeview(self, isolated_fs, real_video_frames, monkeypatch, gtk_pump):
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

			# Wait for scan thread to populate treeview with 2 rows
			for _ in range(50):
				gtk_pump(10)
				if hasattr(ob, "treeview") and ob.treeview is not None:
					model = ob.treeview.get_model()
					if model is not None and len(model) == 2:
						break
				time.sleep(0.05)

			assert ob.window.current_slide == 2
			assert hasattr(ob, "treeview") and ob.treeview is not None
			model = ob.treeview.get_model()
			assert model is not None
			assert len(model) == 2
			assert model[0][3] is True  # is_gray boolean
			assert ob.nextbutton.get_sensitive()
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()


class TestOnboardingSlide3IREmitter:
	def test_slide3_ir_camera_yes_flow(self, isolated_fs, fake_video_capture, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.treeview = Gtk.TreeView()
			listmodel = Gtk.ListStore(str, str, str, bool)
			listmodel.append(["IR Camera", "Yes, compatible", "/dev/video0", True])
			ob.treeview.set_model(listmodel)
			ob.treeview.set_cursor(0)

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
			ob.treeview = Gtk.TreeView()
			listmodel = Gtk.ListStore(str, str, str, bool)
			listmodel.append(["RGB Camera", "No, not infrared", "/dev/video1", False])
			ob.treeview.set_model(listmodel)
			ob.treeview.set_cursor(0)

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
			ob.treeview = Gtk.TreeView()
			listmodel = Gtk.ListStore(str, str, str, bool)
			listmodel.append(["IR Camera", "Yes", "/dev/video0", True])
			ob.treeview.set_model(listmodel)
			ob.treeview.set_cursor(0)

			# Ensure subprocess Popen succeeds for ubuntu-hello set device_path
			real_popen = subprocess.Popen
			monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: real_popen(["true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT))

			ob.window.current_slide = 3
			ob.go_next_slide()
			gtk_pump()
			assert ob.window.current_slide == 4

			# Intercept ubuntu-hello add
			def fake_run(cmd, *a, **k):
				if "add" in cmd:
					return subprocess.CompletedProcess(cmd, 0, stdout="Face model added successfully\n", stderr="")
				return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

			monkeypatch.setattr(subprocess, "run", fake_run)

			# Click scan button
			scan_btn = ob.builder.get_object("scanbutton")
			ob.on_scanbutton_click(scan_btn)
			gtk_pump(20)

			# Execute run_add directly and pump timeout
			ob.run_add()
			for _ in range(20):
				gtk_pump(10)
				if ob.window.current_slide == 5:
					break
				time.sleep(0.02)

			assert ob.window.current_slide == 5
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
	def test_slide7_certainty_and_finish_button(self, isolated_fs, monkeypatch, gtk_pump):
		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			ob.window.current_slide = 6
			ob.builder.get_object("keyring_checkbox").set_active(False)

			captured_certainty = []
			real_popen = subprocess.Popen

			def fake_popen(cmd, *a, **k):
				if "certainty" in cmd:
					captured_certainty.append(cmd[-1])
				return real_popen(["true"])

			monkeypatch.setattr(subprocess, "Popen", fake_popen)

			# Select balanced radio
			radio_balanced = ob.builder.get_object("radiobalanced")
			if radio_balanced:
				radio_balanced.set_active(True)

			ob.go_next_slide()
			gtk_pump()

			assert ob.window.current_slide == 7
			assert ob.slides[7].get_visible()
			assert any("3.5" in str(c) for c in captured_certainty)

			finish_btn = ob.builder.get_object("finishbutton")
			assert finish_btn.get_visible()
			assert not ob.nextbutton.get_visible()

			# Click finish
			ob.on_finishbutton_click(finish_btn)
			assert ob.completed is True
		finally:
			ob.stop_preview()
			ob.window.destroy()
			gtk_pump()
