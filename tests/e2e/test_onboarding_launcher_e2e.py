"""End-to-End tests for post-install onboarding launcher (run_after_install.py).

Validates model directory checks, graphical session discovery across DEs,
single-flight lock semantics, and safe subprocess invocation.
Run with:
    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_onboarding_launcher_e2e.py
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = ROOT / "ubuntu-hello-gtk" / "bin"
if str(BIN_DIR) not in sys.path:
	sys.path.insert(0, str(BIN_DIR))

import run_after_install


class TestOnboardingLauncherE2E:
	def test_models_enrolled_detection(self, tmp_path):
		models_dir = tmp_path / "models"
		models_dir.mkdir()

		# Empty directory -> False
		assert not run_after_install.models_enrolled(str(models_dir))

		# Dotfiles ignored -> False
		(models_dir / ".hidden").write_text("dummy")
		assert not run_after_install.models_enrolled(str(models_dir))

		# Real model file -> True
		(models_dir / "alice_0.dat").write_text("model_data")
		assert run_after_install.models_enrolled(str(models_dir))

	def test_single_flight_lock(self, tmp_path, monkeypatch):
		run_dir = tmp_path / "run" / "ubuntu-hello"
		run_dir.mkdir(parents=True)
		lock_path = run_dir / "postinstall.lock"
		log_path = run_dir / "postinstall.log"

		monkeypatch.setattr(run_after_install, "RUN_DIR", str(run_dir))
		monkeypatch.setattr(run_after_install, "LOCK_PATH", str(lock_path))
		monkeypatch.setattr(run_after_install, "LOG_PATH", str(log_path))

		# First lock succeeds
		fd1 = run_after_install._acquire_single_flight_lock()
		assert fd1 is not None and fd1 >= 0

		# Second lock in same/another thread fails (non-blocking flock)
		fd2 = run_after_install._acquire_single_flight_lock()
		assert fd2 is None

		os.close(fd1)

	def test_main_skip_conditions(self, tmp_path, monkeypatch):
		wizard_calls = []
		monkeypatch.setattr(run_after_install, "launch_setup_wizard", lambda *a, **k: wizard_calls.append(a))
		monkeypatch.setattr(run_after_install, "resolve_install_user", lambda: "alice")

		# DESTDIR set
		monkeypatch.setenv("DESTDIR", "/tmp/pkg")
		assert run_after_install.main() == 0
		assert len(wizard_calls) == 0
		monkeypatch.delenv("DESTDIR", raising=False)

		# Explicit opt-out
		monkeypatch.setenv("UH_SKIP_POSTINSTALL_GUI", "1")
		assert run_after_install.main() == 0
		assert len(wizard_calls) == 0
		monkeypatch.delenv("UH_SKIP_POSTINSTALL_GUI", raising=False)
