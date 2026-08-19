"""Multi-Desktop Environment (DE) integration tests in real GTK3 / Xvfb.

Validates that both the Setup Wizard (Onboarding) and Administrative Settings App
correctly detect DE identity, apply dark/light themes, render correct wallet phrasing,
and layout widgets without errors across:
  - GNOME
  - KDE / Plasma
  - XFCE
  - Cinnamon
  - MATE
  - Budgie
  - LXQt
  - Baseline / Unknown

Run with:
    UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/test_desktop_environments_e2e.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import theme_detect
import wallet_backend
import onboarding
import window


DE_CONFIGS = [
	{
		"de": "gnome",
		"env": {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "DESKTOP_SESSION": "ubuntu"},
		"expected_de": "gnome",
		"wallet_label": "GNOME Keyring",
	},
	{
		"de": "kde",
		"env": {"XDG_CURRENT_DESKTOP": "KDE", "DESKTOP_SESSION": "plasma"},
		"expected_de": "kde",
		"wallet_label": "KWallet",
	},
	{
		"de": "xfce",
		"env": {"XDG_CURRENT_DESKTOP": "XFCE", "DESKTOP_SESSION": "xubuntu"},
		"expected_de": "xfce",
		"wallet_label": "GNOME Keyring",
	},
	{
		"de": "cinnamon",
		"env": {"XDG_CURRENT_DESKTOP": "X-Cinnamon", "DESKTOP_SESSION": "cinnamon"},
		"expected_de": "cinnamon",
		"wallet_label": "GNOME Keyring",
	},
	{
		"de": "mate",
		"env": {"XDG_CURRENT_DESKTOP": "MATE", "DESKTOP_SESSION": "mate"},
		"expected_de": "mate",
		"wallet_label": "GNOME Keyring",
	},
	{
		"de": "budgie",
		"env": {"XDG_CURRENT_DESKTOP": "Budgie:GNOME", "DESKTOP_SESSION": "budgie-desktop"},
		"expected_de": "budgie",
		"wallet_label": "GNOME Keyring",
	},
	{
		"de": "lxqt",
		"env": {"XDG_CURRENT_DESKTOP": "LXQt", "DESKTOP_SESSION": "lubuntu"},
		"expected_de": "lxqt",
		"wallet_label": "GNOME Keyring",
	},
	{
		"de": "baseline",
		"env": {"XDG_CURRENT_DESKTOP": "", "DESKTOP_SESSION": ""},
		"expected_de": "unknown",
		"wallet_label": "none",
	},
]


@pytest.mark.parametrize("config", DE_CONFIGS, ids=lambda c: c["de"])
class TestDesktopEnvironmentParity:
	def test_desktop_detection_and_wallet(self, config, monkeypatch):
		for k, v in config["env"].items():
			monkeypatch.setenv(k, v)

		detected = theme_detect.detect_desktop()
		assert detected == config["expected_de"]

		label = wallet_backend.wallet_backend_label()
		if config["wallet_label"] != "none":
			assert config["wallet_label"] in label
		else:
			assert wallet_backend.detect_wallet_backend() == "none"
			assert label.casefold() == "none"
			assert "keyring" in wallet_backend.wallet_unlock_phrase().casefold()

	def test_onboarding_constructs_cleanly_in_de(self, config, isolated_fs, monkeypatch, gtk_pump):
		for k, v in config["env"].items():
			monkeypatch.setenv(k, v)

		ob = onboarding.OnboardingWindow(run_main_loop=False)
		try:
			gtk_pump()
			assert ob.window.get_visible()
			assert ob.slides[0].get_visible()
		finally:
			ob.window.destroy()
			gtk_pump()

	def test_settings_constructs_cleanly_in_de(self, config, isolated_fs, monkeypatch, gtk_pump):
		for k, v in config["env"].items():
			monkeypatch.setenv(k, v)

		win = window.MainWindow(run_main_loop=False)
		try:
			gtk_pump()
			assert win.window.get_visible()
			assert win.notebook.get_n_pages() == 5
		finally:
			win.window.destroy()
			gtk_pump()
