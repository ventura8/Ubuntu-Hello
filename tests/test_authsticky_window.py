"""Tests for authsticky.py and window.py standalone functions."""
import os
import sys
import importlib
import types
from unittest.mock import patch, MagicMock
import pytest


def _load_authsticky_functions():
    """Load authsticky.py module but skip module-level StickyWindow() instantiation."""
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_path = os.path.join(src_dir, "ubuntu-hello-gtk", "src", "authsticky.py")
    with open(src_path, "r") as f:
        source = f.read()
    
    # Remove the last two lines that instantiate StickyWindow at module-level
    lines = source.split("\n")
    # Find and remove "window = StickyWindow()" and "signal.signal(..." at module level
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("window = StickyWindow()"):
            filtered_lines.append("# " + line)  # Comment out
        elif stripped.startswith("signal.signal("):
            filtered_lines.append("# " + line)  # Comment out
        else:
            filtered_lines.append(line)
    
    modified_source = "\n".join(filtered_lines)
    
    # Compile and execute in a new module
    mod = types.ModuleType("authsticky_test")
    mod.__file__ = src_path
    exec(compile(modified_source, src_path, "exec"), mod.__dict__)
    return mod


# Load once for all tests
_authsticky = None

def _get_authsticky():
    global _authsticky
    if _authsticky is None:
        _authsticky = _load_authsticky_functions()
    return _authsticky


# ── authsticky.py functions ─────────────────────────────────────────

class TestAuthStickyGetRealUser:
    def test_sudo_user(self):
        mod = _get_authsticky()
        with patch.dict(os.environ, {"SUDO_USER": "alice"}, clear=False):
            result = mod.get_real_user()
            assert result == "alice"

    def test_pkexec_uid(self):
        mod = _get_authsticky()
        with patch.dict(os.environ, {"SUDO_USER": "root", "PKEXEC_UID": "1000"}, clear=False), \
             patch("pwd.getpwuid") as mock_pwd:
            mock_pwd.return_value = MagicMock(pw_name="bob")
            result = mod.get_real_user()
            assert result == "bob"

    def test_no_env_fallback_to_getlogin(self):
        mod = _get_authsticky()
        with patch.dict(os.environ, {}, clear=True), \
             patch("os.getlogin", return_value="charlie"):
            result = mod.get_real_user()
            assert result == "charlie"

    def test_all_fail_to_user_env(self):
        mod = _get_authsticky()
        with patch.dict(os.environ, {"USER": "dave"}, clear=True), \
             patch("os.getlogin", side_effect=Exception("no login")):
            result = mod.get_real_user()
            assert result == "dave"

    def test_loginctl_fallback(self):
        mod = _get_authsticky()
        with patch.dict(os.environ, {}, clear=True), \
             patch("os.getlogin", side_effect=Exception), \
             patch("subprocess.check_output", return_value="1 1 eve seat0\n"):
            result = mod.get_real_user()
            assert result == "eve"


# ── get_theme_preference ────────────────────────────────────────────

class TestGetThemePreference:
    def test_root_dark_theme(self):
        mod = _get_authsticky()
        with patch("os.geteuid", return_value=0), \
             patch.object(mod, "get_real_user", return_value="testuser"), \
             patch("subprocess.check_output", return_value="'prefer-dark'\n"):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_root_light_theme(self):
        mod = _get_authsticky()
        with patch("os.geteuid", return_value=0), \
             patch.object(mod, "get_real_user", return_value="testuser"), \
             patch("subprocess.check_output", return_value="'prefer-light'\n"):
            result = mod.get_theme_preference()
            assert result == "light"

    def test_root_no_user(self):
        mod = _get_authsticky()
        with patch("os.geteuid", return_value=0), \
             patch.object(mod, "get_real_user", return_value="root"):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_root_gtk_dark_theme(self):
        mod = _get_authsticky()
        call_count = [0]
        def check_output_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "'default'\n"  # color-scheme via dconf
            elif call_count[0] == 2:
                return "'Yaru-dark'\n"  # gtk-theme via dconf
            return "''\n"
        
        with patch("os.geteuid", return_value=0), \
             patch.object(mod, "get_real_user", return_value="testuser"), \
             patch("subprocess.check_output", side_effect=check_output_side_effect):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_root_all_subprocess_fail(self):
        mod = _get_authsticky()
        with patch("os.geteuid", return_value=0), \
             patch.object(mod, "get_real_user", return_value="testuser"), \
             patch("subprocess.check_output", side_effect=Exception("fail")):
            result = mod.get_theme_preference()
            assert result == "light"

    def test_exception_returns_dark(self):
        mod = _get_authsticky()
        with patch("os.geteuid", side_effect=Exception("fail")):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_non_root_schema_missing(self):
        mod = _get_authsticky()
        mock_schema_source = MagicMock()
        mock_schema_source.list_schemas.return_value = ([], [])
        
        with patch("os.geteuid", return_value=1000), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_schema_source):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_non_root_prefer_dark(self):
        mod = _get_authsticky()
        mock_schema_source = MagicMock()
        mock_schema_source.list_schemas.return_value = (["org.gnome.desktop.interface"], [])
        
        mock_settings = MagicMock()
        mock_settings.get_string.side_effect = lambda key: "prefer-dark" if key == "color-scheme" else "Yaru"
        
        with patch("os.geteuid", return_value=1000), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_schema_source), \
             patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_non_root_theme_dark(self):
        mod = _get_authsticky()
        mock_schema_source = MagicMock()
        mock_schema_source.list_schemas.return_value = (["org.gnome.desktop.interface"], [])
        
        mock_settings = MagicMock()
        mock_settings.get_string.side_effect = lambda key: "default" if key == "color-scheme" else "Yaru-dark"
        
        with patch("os.geteuid", return_value=1000), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_schema_source), \
             patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            result = mod.get_theme_preference()
            assert result == "dark"

    def test_non_root_light(self):
        mod = _get_authsticky()
        mock_schema_source = MagicMock()
        mock_schema_source.list_schemas.return_value = (["org.gnome.desktop.interface"], [])
        
        mock_settings = MagicMock()
        mock_settings.get_string.side_effect = lambda key: "default" if key == "color-scheme" else "Yaru-light"
        
        with patch("os.geteuid", return_value=1000), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_schema_source), \
             patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            result = mod.get_theme_preference()
            assert result == "light"


# ── StickyWindow class ──────────────────────────────────────────────

class TestStickyWindow:
    def _make_sw(self):
        mod = _get_authsticky()
        StickyWindow = mod.StickyWindow
        # Create a mock-based instance that has the methods we need
        sw = MagicMock(spec=StickyWindow)
        # Set real methods from the class
        sw.catch_stdin = lambda: StickyWindow.catch_stdin(sw)
        sw.exit = lambda widget, context: StickyWindow.exit(sw, widget, context)
        sw.draw = lambda widget, ctx: StickyWindow.draw(sw, widget, ctx)
        sw.message = ""
        sw.subtext = ""
        return sw, mod

    def test_catch_stdin_message(self):
        sw, mod = self._make_sw()
        sw.queue_draw = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = "M=Hello World\n"
            with patch.object(mod.gobject, "timeout_add"):
                sw.catch_stdin()
        assert sw.message == "Hello World"

    def test_catch_stdin_subtext(self):
        sw, mod = self._make_sw()
        sw.queue_draw = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = "S=Sub text here\n"
            with patch.object(mod.gobject, "timeout_add"):
                sw.catch_stdin()
        assert sw.subtext == "Sub text here"

    def test_catch_stdin_empty(self):
        sw, mod = self._make_sw()
        sw.message = "old"
        sw.queue_draw = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = "\n"
            with patch.object(mod.gobject, "timeout_add"):
                sw.catch_stdin()
        assert sw.message == "old"

    def test_catch_stdin_padding(self):
        sw, mod = self._make_sw()
        sw.queue_draw = MagicMock()
        sw.message = "old"

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = "P=_PADDING\n"
            with patch.object(mod.gobject, "timeout_add"):
                sw.catch_stdin()
        # Padding lines don't change message or subtext
        assert sw.message == "old"

    def test_exit(self):
        sw, mod = self._make_sw()
        with patch.object(mod.gtk, 'main_quit'):
            result = sw.exit(None, None)
            assert result is True

    def test_draw_dark_theme(self):
        sw, mod = self._make_sw()
        sw.message = "Test"
        sw.subtext = "Sub"
        sw.logo_surface = MagicMock()
        sw.logo_ratio = 1.0
        sw.get_window = MagicMock(return_value=MagicMock())

        mock_ctx = MagicMock()
        with patch.object(mod, "get_theme_preference", return_value="dark"):
            sw.draw(MagicMock(), mock_ctx)
            mock_ctx.paint.assert_called()
            assert mock_ctx.show_text.call_count >= 2  # message + subtext

    def test_draw_light_no_subtext(self):
        sw, mod = self._make_sw()
        sw.message = "Test"
        sw.subtext = ""
        sw.logo_surface = MagicMock()
        sw.logo_ratio = 1.0
        sw.get_window = MagicMock(return_value=MagicMock())

        mock_ctx = MagicMock()
        with patch.object(mod, "get_theme_preference", return_value="light"):
            sw.draw(MagicMock(), mock_ctx)
            mock_ctx.paint.assert_called()
            assert mock_ctx.show_text.call_count == 1  # only message

    def test_sticky_window_init(self):
        mod = _get_authsticky()
        StickyWindow = mod.StickyWindow
        
        mock_logo_surface = MagicMock()
        mock_logo_surface.get_height.return_value = 80
        
        mock_screen = MagicMock()
        mock_screen.get_width.return_value = 1920
        mock_screen.get_rgba_visual.return_value = "visual"
        
        with patch("cairo.ImageSurface.create_from_png", return_value=mock_logo_surface), \
             patch("paths_factory.logo_path", return_value="/mock/logo.png"), \
             patch("gi.repository.Gdk.Screen.get_default", return_value=mock_screen), \
             patch("gi.repository.GObject.timeout_add"), \
             patch("gi.repository.Gtk.main"):
            
            sw = StickyWindow()
            assert sw.logo_surface == mock_logo_surface
            assert sw.logo_ratio == (100 - 20) / 80.0
