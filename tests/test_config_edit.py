"""Tests for config_edit.py (comment-preserving INI edits) and the Settings Notifications tab."""

import os
from unittest.mock import MagicMock, patch

import pytest

import config_edit

SAMPLE = """# top comment
[core]
# keep
disabled = false

[notifications]
# card on/off
enabled = true
details = false

[rubberstamps]
enabled = false
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(SAMPLE, encoding="utf-8")
    return str(path)


def test_set_option_replaces_in_the_right_section_only(cfg):
    config_edit.set_option(cfg, "notifications", "enabled", "false")
    text = open(cfg, encoding="utf-8").read()
    assert "[notifications]\n# card on/off\nenabled = false\n" in text
    assert "[rubberstamps]\nenabled = false\n" in text      # same key elsewhere untouched
    assert "# top comment" in text and "# keep" in text     # comments survive


def test_set_option_appends_missing_key_to_section(cfg):
    config_edit.set_option(cfg, "notifications", "sound", "false")
    text = open(cfg, encoding="utf-8").read()
    assert "details = false\nsound = false\n\n[rubberstamps]" in text


def test_set_option_appends_missing_section(cfg):
    config_edit.set_option(cfg, "brand_new", "key", "1")
    text = open(cfg, encoding="utf-8").read()
    assert text.endswith("[rubberstamps]\nenabled = false\n\n[brand_new]\nkey = 1\n")


def test_set_option_preserves_mode(cfg):
    os.chmod(cfg, 0o640)
    config_edit.set_option(cfg, "core", "disabled", "true")
    assert oct(os.stat(cfg).st_mode & 0o777) == "0o640"
    assert not [f for f in os.listdir(os.path.dirname(cfg)) if f.startswith(".config.")]


@pytest.mark.parametrize("raw,expected", [("true", True), ("Yes", True), ("1", True), ("on", True),
                                          ("false", False), ("0", False), ("nope", False)])
def test_get_bool_values(cfg, raw, expected):
    config_edit.set_option(cfg, "notifications", "sound", raw)
    assert config_edit.get_bool(cfg, "notifications", "sound", not expected) is expected


def test_get_bool_default_when_missing(cfg, tmp_path):
    assert config_edit.get_bool(cfg, "notifications", "sound", True) is True
    assert config_edit.get_bool(str(tmp_path / "missing.ini"), "notifications", "enabled", False) is False


# ── tab_notifications ─────────────────────────────────────────────────

@pytest.fixture
def tab():
    import tab_notifications
    return tab_notifications


def _win(cfg, switches=None):
    win = MagicMock()
    widgets = {wid: MagicMock() for wid in ("notifications_enabled_switch", "notifications_sound_switch", "notifications_details_switch")}
    win.builder.get_object.side_effect = lambda wid: widgets.get(wid)
    win._loading_notification_settings = False
    return win, widgets


def test_load_reflects_config_and_dependent_sensitivity(cfg, tab):
    config_edit.set_option(cfg, "notifications", "enabled", "false")
    win, w = _win(cfg)
    with patch("tab_notifications.paths_factory.config_file_path", return_value=cfg):
        tab.load_notification_settings(win)
    w["notifications_enabled_switch"].set_active.assert_called_with(False)
    w["notifications_sound_switch"].set_active.assert_called_with(True)     # default
    w["notifications_details_switch"].set_active.assert_called_with(False)
    w["notifications_sound_switch"].set_sensitive.assert_called_with(False)
    w["notifications_details_switch"].set_sensitive.assert_called_with(False)
    assert win._loading_notification_settings is False


def test_handlers_write_config_and_are_no_ops_while_loading(cfg, tab):
    win, w = _win(cfg)
    with patch("tab_notifications.paths_factory.config_file_path", return_value=cfg):
        assert tab.on_notifications_sound_state_set(win, MagicMock(), False) is False
        assert config_edit.get_bool(cfg, "notifications", "sound", True) is False
        tab.on_notifications_details_state_set(win, MagicMock(), True)
        assert config_edit.get_bool(cfg, "notifications", "details", False) is True
        tab.on_notifications_enabled_state_set(win, MagicMock(), False)
        assert config_edit.get_bool(cfg, "notifications", "enabled", True) is False
        w["notifications_sound_switch"].set_sensitive.assert_called_with(False)
        # Programmatic set_active() during load must not write
        win._loading_notification_settings = True
        tab.on_notifications_enabled_state_set(win, MagicMock(), True)
        assert config_edit.get_bool(cfg, "notifications", "enabled", True) is False


def test_write_failure_shows_dialog_instead_of_raising(cfg, tab):
    win, w = _win(cfg)
    with patch("tab_notifications.paths_factory.config_file_path", return_value=cfg), \
         patch("tab_notifications.config_edit.set_option", side_effect=OSError("read-only")), \
         patch("tab_notifications.gtk.MessageDialog") as dialog:
        tab.on_notifications_sound_state_set(win, MagicMock(), True)
    dialog.return_value.run.assert_called_once()


def test_set_option_appends_section_when_file_lacks_trailing_newline(tmp_path):
    path = tmp_path / "c.ini"
    path.write_text("[core]\nx = 1", encoding="utf-8")     # no final newline
    config_edit.set_option(str(path), "notifications", "enabled", "true")
    assert path.read_text(encoding="utf-8") == "[core]\nx = 1\n\n[notifications]\nenabled = true\n"


def test_set_option_cleans_up_temp_file_on_write_failure(cfg):
    with patch("config_edit.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            config_edit.set_option(cfg, "core", "disabled", "true")
    assert not [f for f in os.listdir(os.path.dirname(cfg)) if f.startswith(".config.")]
    assert "disabled = false" in open(cfg, encoding="utf-8").read()   # original untouched


def test_set_option_tolerates_chmod_failure(cfg):
    with patch("config_edit.os.chmod", side_effect=OSError("nope")):
        config_edit.set_option(cfg, "core", "disabled", "true")
    assert config_edit.get_bool(cfg, "core", "disabled", False) is True


def test_load_skips_missing_switch_widgets(cfg, tab):
    win = MagicMock()
    win.builder.get_object.return_value = None      # widgets absent (e.g. old Glade)
    with patch("tab_notifications.paths_factory.config_file_path", return_value=cfg):
        tab.load_notification_settings(win)        # must not raise
    assert win._loading_notification_settings is False


def test_sound_and_details_handlers_are_no_ops_while_loading(cfg, tab):
    win, w = _win(cfg)
    win._loading_notification_settings = True
    with patch("tab_notifications.paths_factory.config_file_path", return_value=cfg), \
         patch("tab_notifications.config_edit.set_option") as set_option:
        tab.on_notifications_sound_state_set(win, MagicMock(), False)
        tab.on_notifications_details_state_set(win, MagicMock(), True)
    set_option.assert_not_called()
