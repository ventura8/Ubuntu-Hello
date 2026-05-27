"""Tests for tab_keyring.py, tab_models.py, tab_video.py, and auth_helper.py."""
import os
import sys
import ctypes
import configparser
import subprocess
from unittest.mock import patch, MagicMock, mock_open
import pytest

import auth_helper
import tab_keyring
import tab_models
import tab_video



# ── xor_crypt ───────────────────────────────────────────────────────

class TestXorCrypt:
    def test_basic_encryption(self):
        result = tab_keyring.xor_crypt("abc", "key")
        assert isinstance(result, str)
        # Each char produces 2 hex digits
        assert len(result) == 6

    def test_roundtrip(self):
        """XOR with same key twice should give back original (as hex decode)."""
        key = "mysecretkey"
        plaintext = "hello"
        encrypted = tab_keyring.xor_crypt(plaintext, key)
        # Decrypt: convert hex back to bytes, XOR again
        decrypted_bytes = bytes.fromhex(encrypted)
        decrypted = ''.join(chr(b ^ ord(key[i % len(key)])) for i, b in enumerate(decrypted_bytes))
        assert decrypted == plaintext

    def test_empty_string(self):
        result = tab_keyring.xor_crypt("", "key")
        assert result == ""

    def test_single_char(self):
        result = tab_keyring.xor_crypt("A", "B")
        expected = f"{ord('A') ^ ord('B'):02x}"
        assert result == expected


# ── update_keyring_status ───────────────────────────────────────────

class TestUpdateKeyringStatus:
    def _make_self(self, user="testuser", items=1):
        mock = MagicMock()
        mock.active_user = user
        mock.userlist.items = items
        mock.keyring_status_label = MagicMock()
        mock.keyring_enable_button = MagicMock()
        mock.keyring_disable_button = MagicMock()
        return mock

    def test_no_user(self):
        mock = self._make_self(user="", items=0)
        tab_keyring.update_keyring_status(mock)
        mock.keyring_enable_button.set_sensitive.assert_called_with(False)
        mock.keyring_disable_button.set_sensitive.assert_called_with(False)

    def test_tpm_enabled(self):
        mock = self._make_self()
        with patch("os.path.exists", side_effect=lambda p: ".pub" in p):
            tab_keyring.update_keyring_status(mock)
            mock.keyring_enable_button.set_sensitive.assert_called_with(False)
            mock.keyring_disable_button.set_sensitive.assert_called_with(True)

    def test_software_enabled(self):
        mock = self._make_self()
        def exists_side_effect(p):
            if "keyring-keys" in p and "testuser" in p and ".pub" not in p:
                return True
            return False
        with patch("os.path.exists", side_effect=exists_side_effect):
            tab_keyring.update_keyring_status(mock)
            mock.keyring_enable_button.set_sensitive.assert_called_with(False)
            mock.keyring_disable_button.set_sensitive.assert_called_with(True)

    def test_pending(self):
        mock = self._make_self()
        def exists_side_effect(p):
            if "pending" in p:
                return True
            return False
        with patch("os.path.exists", side_effect=exists_side_effect):
            tab_keyring.update_keyring_status(mock)

    def test_disabled(self):
        mock = self._make_self()
        with patch("os.path.exists", return_value=False):
            tab_keyring.update_keyring_status(mock)
            mock.keyring_enable_button.set_sensitive.assert_called_with(True)
            mock.keyring_disable_button.set_sensitive.assert_called_with(False)


# ── on_keyring_enable ───────────────────────────────────────────────

class TestOnKeyringEnable:
    def test_no_user(self):
        mock = MagicMock()
        mock.active_user = ""
        tab_keyring.on_keyring_enable(mock, MagicMock())

    def test_cancel_dialog(self):
        import gi.repository
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls:
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 2  # CANCEL
            dialog.entry1.get_text.return_value = ""
            tab_keyring.on_keyring_enable(mock, MagicMock())


# ── on_keyring_disable ──────────────────────────────────────────────

class TestOnKeyringDisable:
    def test_no_user(self):
        mock = MagicMock()
        mock.active_user = ""
        tab_keyring.on_keyring_disable(mock, MagicMock())

    def test_cancel_confirm(self):
        from gi.repository import Gtk as gtk
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.gtk.MessageDialog") as mock_dialog_cls:
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 4  # NO
            tab_keyring.on_keyring_disable(mock, MagicMock())


# ── tab_models functions ────────────────────────────────────────────

class TestTabModels:
    def test_on_user_change(self):
        mock = MagicMock()
        select = MagicMock()
        select.get_active_text.return_value = "newuser"
        tab_models.on_user_change(mock, select)
        assert mock.active_user == "newuser"
        mock.load_model_list.assert_called_once()
        mock.update_keyring_status.assert_called_once()

    def test_execute_add_success(self):
        box = MagicMock()
        box.active_user = "testuser"
        dialog = MagicMock()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            tab_models.execute_add(box, dialog, "model1")
            dialog.destroy.assert_called_once()
            box.load_model_list.assert_called_once()
            mock_run.assert_called_once_with(["ubuntu-hello", "add", "model1", "-y", "-U", "testuser"], capture_output=True, text=True)

    def test_execute_add_failure(self):
        box = MagicMock()
        box.active_user = "testuser"
        dialog = MagicMock()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch("subprocess.run", return_value=mock_result), \
             patch("tab_models.gtk.MessageDialog") as mock_err:
            err_dialog = mock_err.return_value
            tab_models.execute_add(box, dialog, "model1")
            err_dialog.run.assert_called_once()


# ── tab_video functions ─────────────────────────────────────────────

class TestTabVideo:
    def test_get_camera_devices_no_dir(self):
        with patch("os.path.exists", return_value=False), \
             patch("glob.glob", return_value=[]):
            result = tab_video.get_camera_devices()
            assert result == []

    def test_get_camera_devices_exception(self):
        with patch("os.path.exists", return_value=True), \
             patch("os.listdir", side_effect=Exception("mock error")), \
             patch("glob.glob", return_value=["/dev/video1"]):
            result = tab_video.get_camera_devices()
            assert result == ["/dev/video1"]

    def test_capture_frame_none(self):
        mock = MagicMock()
        mock.capture = None
        assert tab_video.capture_frame(mock) is None

    def test_on_camera_change_exception(self):
        mock = MagicMock()
        mock.populating_cameras = False
        mock.capture = MagicMock()
        mock_combo = MagicMock()
        mock_combo.get_active_text.return_value = "/dev/video0"
        
        mock.cv2 = MagicMock()
        mock.cv2.VideoCapture.side_effect = Exception("mock error")
        mock.config = MagicMock()
        
        with patch("tab_video.paths_factory.config_file_path", return_value="/mock/config.ini"), \
             patch("builtins.open", side_effect=Exception("mock save error")):
            tab_video.on_camera_change(mock, mock_combo)

    def test_get_camera_devices_with_devices(self):
        with patch("os.path.exists", return_value=True), \
             patch("os.listdir", return_value=["dev1", "dev2"]), \
             patch("glob.glob", return_value=["/dev/video0"]):
            result = tab_video.get_camera_devices()
            assert "/dev/v4l/by-path/dev1" in result
            assert "/dev/v4l/by-path/dev2" in result
            assert "/dev/video0" in result

    def test_capture_frame_no_capture(self):
        mock = MagicMock()
        mock.capture = None
        tab_video.capture_frame(mock)

    def test_on_camera_change_populating(self):
        mock = MagicMock()
        mock.populating_cameras = True
        combo = MagicMock()
        tab_video.on_camera_change(mock, combo)
        # Should return early

    def test_on_camera_change_no_path(self):
        mock = MagicMock()
        mock.populating_cameras = False
        combo = MagicMock()
        combo.get_active_text.return_value = None
        tab_video.on_camera_change(mock, combo)

    def test_on_page_switch_not_1(self):
        mock = MagicMock()
        old_capture = MagicMock()
        mock.capture = old_capture
        tab_video.on_page_switch(mock, MagicMock(), MagicMock(), 2)
        old_capture.release.assert_called_once()
        assert mock.capture is None

    def test_on_page_switch_1_success(self):
        mock = MagicMock()
        mock.capture = None
        
        mock_capture = MagicMock()
        mock_capture.get.side_effect = lambda prop: 480 if prop == 4 else 640

        mock_config = configparser.ConfigParser()
        mock_config.add_section("video")
        mock_config.set("video", "device_path", "/dev/video0")
        mock_config.set("video", "recording_plugin", "plugin_xyz")
        
        mock_widgets = {
            "cameraselect": MagicMock(),
            "opencvbox": MagicMock(),
            "videores": MagicMock(),
            "videoresused": MagicMock(),
            "videorecorder": MagicMock()
        }
        mock.builder.get_object.side_effect = lambda name: mock_widgets[name]

        mock_cv2 = sys.modules['cv2']
        mock_cv2.CAP_PROP_FRAME_HEIGHT = 4
        mock_cv2.CAP_PROP_FRAME_WIDTH = 3
        mock_cv2.VideoCapture.return_value = mock_capture

        with patch("tab_video.configparser.ConfigParser", return_value=mock_config), \
             patch("tab_video.paths_factory.config_file_path", return_value="/mock/config.ini"), \
             patch("tab_video.get_camera_devices", return_value=["/dev/video0", "/dev/video1"]), \
             patch("tab_video.gobject.timeout_add") as mock_timeout:
            
            tab_video.on_page_switch(mock, MagicMock(), MagicMock(), 1)
            mock_widgets["cameraselect"].remove_all.assert_called_once()
            mock_widgets["cameraselect"].append_text.assert_any_call("/dev/video0")
            mock_widgets["cameraselect"].set_active.assert_called_with(0)
            assert mock.scaling_factor == 300 / 640.0
            mock_widgets["videores"].set_text.assert_called_with("640x480")
            mock_widgets["videorecorder"].set_text.assert_called_with("plugin_xyz")
            mock_timeout.assert_called_once_with(10, mock.capture_frame)

    def test_on_page_switch_1_opencv_import_fail(self):
        mock = MagicMock()
        mock.capture = MagicMock()
        mock.capture.get.return_value = 100
        mock.cv2 = MagicMock()
        mock.cv2.CAP_PROP_FRAME_HEIGHT = 4
        mock.cv2.CAP_PROP_FRAME_WIDTH = 3
        mock.cv2.VideoCapture.return_value = mock.capture

        mock_config = configparser.ConfigParser()
        mock_config.add_section("video")
        mock_config.set("video", "device_path", "/dev/video0")

        mock_widgets = {
            "cameraselect": MagicMock(),
            "opencvbox": MagicMock(),
            "videores": MagicMock(),
            "videoresused": MagicMock(),
            "videorecorder": MagicMock()
        }
        mock.builder.get_object.side_effect = lambda name: mock_widgets[name]

        with patch.dict(sys.modules, {"cv2": None}), \
             patch("tab_video.configparser.ConfigParser", return_value=mock_config), \
             patch("tab_video.paths_factory.config_file_path", return_value="/mock/config.ini"), \
             patch("tab_video.get_camera_devices", return_value=["/dev/video0"]), \
             patch("tab_video.gobject.timeout_add"):
            tab_video.on_page_switch(mock, MagicMock(), MagicMock(), 1)

    def test_on_camera_change_success(self):
        mock = MagicMock()
        mock.populating_cameras = False
        old_capture = MagicMock()
        mock.capture = old_capture
        
        mock_combo = MagicMock()
        mock_combo.get_active_text.return_value = "/dev/video1"

        mock_capture = MagicMock()
        mock_capture.get.side_effect = lambda prop: 480 if prop == 4 else 640

        mock.config = configparser.ConfigParser()
        mock.config.add_section("video")
        mock.config.set("video", "device_path", "/dev/video0")

        mock_widgets = {
            "videores": MagicMock(),
            "videoresused": MagicMock()
        }
        mock.builder.get_object.side_effect = lambda name: mock_widgets[name]

        mock_cv2 = sys.modules['cv2']
        mock.cv2 = mock_cv2
        mock_cv2.CAP_PROP_FRAME_HEIGHT = 4
        mock_cv2.CAP_PROP_FRAME_WIDTH = 3
        mock_cv2.VideoCapture.return_value = mock_capture

        open_mock = mock_open()

        with patch("builtins.open", open_mock), \
             patch("tab_video.paths_factory.config_file_path", return_value="/mock/config.ini"):
            
            tab_video.on_camera_change(mock, mock_combo)
            old_capture.release.assert_called_once()
            assert mock.config.get("video", "device_path") == "/dev/video1"
            open_mock.assert_called_with("/mock/config.ini", "w")
            mock_widgets["videores"].set_text.assert_called_with("640x480")

    def test_capture_frame_success(self):
        mock = MagicMock()
        mock.capture = MagicMock()
        mock_frame = MagicMock()
        mock.capture.read.return_value = (True, mock_frame)

        mock.cv2 = MagicMock()
        mock_resized = MagicMock()
        mock.cv2.resize.return_value = mock_resized
        mock.cv2.imencode.return_value = (True, b"png_data")

        mock.scaling_factor = 0.5
        mock.opencvimage = MagicMock()

        mock_loader = MagicMock()
        mock_pixbuf_obj = MagicMock()
        mock_loader.get_pixbuf.return_value = mock_pixbuf_obj
        
        with patch("tab_video.pixbuf.PixbufLoader", return_value=mock_loader), \
             patch("tab_video.gobject.timeout_add") as mock_timeout:
            
            tab_video.capture_frame(mock)
            mock.cv2.resize.assert_called_with(mock_frame, None, fx=0.5, fy=0.5, interpolation=mock.cv2.INTER_AREA)
            mock.cv2.imencode.assert_called_with(".png", mock_resized)
            mock_loader.write.assert_called_with(b"png_data")
            mock_loader.close.assert_called_once()
            mock.opencvimage.set_from_pixbuf.assert_called_with(mock_pixbuf_obj)
            mock_timeout.assert_called_once_with(20, mock.capture_frame)

    def test_capture_frame_read_fail(self):
        mock = MagicMock()
        mock.capture = MagicMock()
        mock.capture.read.return_value = (False, None)

        with patch("tab_video.gobject.timeout_add") as mock_timeout:
            tab_video.capture_frame(mock)
            mock_timeout.assert_called_once_with(20, mock.capture_frame)


# ── Additional tab_keyring tests ─────────────────────────────────────

class TestKeyringDetails:
    def test_keyring_password_dialog(self):
        parent = MagicMock()
        dialog = tab_keyring.KeyringPasswordDialog(parent, "testuser")
        assert dialog is not None

    def test_on_keyring_enable_empty_password(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1  # ResponseType.OK
            dialog.entry1.get_text.return_value = ""  # empty
            
            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_msg_dialog_cls.assert_called_once()
            err_dialog = mock_msg_dialog_cls.return_value
            err_dialog.run.assert_called_once()

    def test_on_keyring_enable_incorrect_password(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=False), \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1  # ResponseType.OK
            dialog.entry1.get_text.return_value = "wrongpass"
            
            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_msg_dialog_cls.assert_called_once()
            err_dialog = mock_msg_dialog_cls.return_value
            err_dialog.run.assert_called_once()

    def test_on_keyring_enable_tpm_success(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        def exists_side_effect(path):
            if path in ["/dev/tpmrm0", "/dev/tpm0"]:
                return True
            return False

        def which_side_effect(cmd):
            if cmd in ["tpm2_createprimary", "tpm2_unseal"]:
                return "/usr/bin/" + cmd
            return None

        mock_popen = MagicMock()
        mock_popen.communicate.return_value = (b"", b"")
        mock_popen.returncode = 0

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", side_effect=exists_side_effect), \
             patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen", return_value=mock_popen), \
             patch("os.makedirs") as mock_makedirs, \
             patch("os.chmod") as mock_chmod, \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_run.assert_called_once()
            mock_popen.communicate.assert_called_once_with(input=b"correctpass")
            mock_msg_dialog_cls.assert_called_once()
            mock.update_keyring_status.assert_called_once()

    def test_on_keyring_enable_tpm_failure(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        def exists_side_effect(path):
            return path in ["/dev/tpmrm0", "/dev/tpm0"] or "primary_" in path

        mock_popen = MagicMock()
        mock_popen.communicate.return_value = (b"", b"tpm error")
        mock_popen.returncode = 1

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", side_effect=exists_side_effect), \
             patch("shutil.which", return_value="/usr/bin/tool"), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen", return_value=mock_popen), \
             patch("os.makedirs"), \
             patch("os.chmod"), \
             patch("os.unlink") as mock_unlink, \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            unlinked_paths = [call[0][0] for call in mock_unlink.call_args_list]
            assert any("primary_" in path for path in unlinked_paths)
            mock_msg_dialog_cls.assert_called_once()

    def test_on_keyring_enable_tpm_install_tools(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        which_calls = [0]
        def which_side_effect(cmd):
            which_calls[0] += 1
            if which_calls[0] > 2:
                return "/usr/bin/" + cmd
            return None

        mock_popen = MagicMock()
        mock_popen.communicate.return_value = (b"", b"")
        mock_popen.returncode = 0

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", side_effect=lambda p: p in ["/dev/tpmrm0"]), \
             patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen", return_value=mock_popen), \
             patch("os.makedirs"), \
             patch("os.chmod"), \
             patch("tab_keyring.gtk.MessageDialog"):
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_run.assert_any_call(["apt-get", "install", "-y", "-qq", "tpm2-tools"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)

    def test_on_keyring_enable_software_success(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        open_mock = mock_open(read_data="machine123\n")

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", open_mock), \
             patch("os.makedirs") as mock_makedirs, \
             patch("os.chmod") as mock_chmod, \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            open_mock.assert_any_call("/etc/machine-id", "r")
            open_mock.assert_any_call("/etc/ubuntu-hello/keyring-keys/testuser", "w")
            mock_msg_dialog_cls.assert_called_once()

    def test_on_keyring_enable_software_no_machine_id(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", side_effect=Exception("Read error")), \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_msg_dialog_cls.assert_called_once()

    def test_on_keyring_enable_software_empty_machine_id(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        open_mock = mock_open(read_data="   \n")

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", open_mock), \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_msg_dialog_cls.assert_called_once()

    def test_on_keyring_enable_software_write_error(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        def open_side_effect(path, mode="r"):
            if "machine-id" in path:
                return mock_open(read_data="machine123")()
            raise PermissionError("Access denied")

        with patch("tab_keyring.KeyringPasswordDialog") as mock_dialog_cls, \
             patch("tab_keyring.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", side_effect=open_side_effect), \
             patch("os.makedirs"), \
             patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls:
            
            dialog = mock_dialog_cls.return_value
            dialog.run.return_value = 1
            dialog.entry1.get_text.return_value = "correctpass"

            tab_keyring.on_keyring_enable(mock, MagicMock())
            mock_msg_dialog_cls.assert_called_once()

    def test_on_keyring_disable_success(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls, \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink") as mock_unlink:
            
            mock_confirm = MagicMock()
            mock_confirm.run.return_value = 3  # ResponseType.YES
            mock_success = MagicMock()
            mock_msg_dialog_cls.side_effect = [mock_confirm, mock_success]

            tab_keyring.on_keyring_disable(mock, MagicMock())

            assert mock_unlink.call_count == 4
            mock_success.run.assert_called_once()
            mock.update_keyring_status.assert_called_once()

    def test_on_keyring_disable_failure(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock.window = MagicMock()

        with patch("tab_keyring.gtk.MessageDialog") as mock_msg_dialog_cls, \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink", side_effect=PermissionError("no permission")):
            
            mock_confirm = MagicMock()
            mock_confirm.run.return_value = 3  # ResponseType.YES
            mock_error = MagicMock()
            mock_msg_dialog_cls.side_effect = [mock_confirm, mock_error]

            tab_keyring.on_keyring_disable(mock, MagicMock())

            mock_error.run.assert_called_once()
            mock.update_keyring_status.assert_called_once()


# ── Additional tab_models tests ──────────────────────────────────────

class TestTabModelsDetails:
    def test_on_user_add_ok(self):
        mock = MagicMock()
        mock.userlist = MagicMock()
        mock.userlist.items = 5
        
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = 1  # ResponseType.OK
        
        mock_entry = MagicMock()
        mock_entry.get_text.return_value = "newuser"

        with patch("tab_models.gtk.MessageDialog", return_value=mock_dialog), \
             patch("tab_models.gtk.Entry", return_value=mock_entry):
            
            tab_models.on_user_add(mock, MagicMock())
            mock.userlist.append_text.assert_called_with("newuser")
            mock.userlist.set_active.assert_called_with(5)
            assert mock.userlist.items == 6
            assert mock.active_user == "newuser"
            mock.load_model_list.assert_called_once()
            mock.update_keyring_status.assert_called_once()

    def test_on_user_add_cancel(self):
        mock = MagicMock()
        mock.userlist = MagicMock()
        mock.userlist.items = 5

        mock_dialog = MagicMock()
        mock_dialog.run.return_value = 2  # ResponseType.CANCEL

        with patch("tab_models.gtk.MessageDialog", return_value=mock_dialog), \
             patch("tab_models.gtk.Entry"):
            
            tab_models.on_user_add(mock, MagicMock())
            mock.userlist.append_text.assert_not_called()

    def test_on_model_add_no_user(self):
        mock = MagicMock()
        mock.userlist.items = 0
        tab_models.on_model_add(mock, MagicMock())

    def test_on_model_add_cancel(self):
        mock = MagicMock()
        mock.userlist.items = 1
        
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = 2  # ResponseType.CANCEL

        with patch("tab_models.gtk.MessageDialog", return_value=mock_dialog), \
             patch("tab_models.gtk.Entry"):
            
            tab_models.on_model_add(mock, MagicMock())

    def test_on_model_add_ok(self):
        mock = MagicMock()
        mock.userlist.items = 1
        
        mock_confirm_dialog = MagicMock()
        mock_confirm_dialog.run.return_value = 1  # ResponseType.OK
        
        mock_creating_dialog = MagicMock()
        
        mock_entry = MagicMock()
        mock_entry.get_text.return_value = "newmodel"

        with patch("tab_models.gtk.MessageDialog", side_effect=[mock_confirm_dialog, mock_creating_dialog]), \
             patch("tab_models.gtk.Entry", return_value=mock_entry), \
             patch("tab_models.gobject.timeout_add") as mock_timeout:
            
            tab_models.on_model_add(mock, MagicMock())
            mock_timeout.assert_called_once()
            args = mock_timeout.call_args[0]
            callback = args[1]
            
            with patch("tab_models.execute_add") as mock_execute_add:
                callback()
                mock_execute_add.assert_called_once_with(mock, mock_creating_dialog, "newmodel")

    def test_on_model_delete_empty(self):
        mock = MagicMock()
        mock_selection = mock.treeview.get_selection.return_value
        mock_selection.get_selected_rows.return_value = (MagicMock(), [])

        tab_models.on_model_delete(mock, MagicMock())

    def test_on_model_delete_cancel(self):
        mock = MagicMock()
        mock_selection = mock.treeview.get_selection.return_value
        
        mock_listmodel = MagicMock()
        mock_listmodel.get_value.side_effect = lambda iter, idx: "1" if idx == 0 else "model1"
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row1"])

        mock_dialog = MagicMock()
        mock_dialog.run.return_value = 2  # ResponseType.CANCEL

        with patch("tab_models.gtk.MessageDialog", return_value=mock_dialog):
            tab_models.on_model_delete(mock, MagicMock())

    def test_on_model_delete_success(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock_selection = mock.treeview.get_selection.return_value
        
        mock_listmodel = MagicMock()
        mock_listmodel.get_value.side_effect = lambda iter, idx: "123" if idx == 0 else "model1"
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row1"])

        mock_dialog = MagicMock()
        mock_dialog.run.return_value = 1  # ResponseType.OK

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success"
        mock_result.stderr = ""

        with patch("tab_models.gtk.MessageDialog", return_value=mock_dialog), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            
            tab_models.on_model_delete(mock, MagicMock())
            mock_run.assert_called_once_with(["ubuntu-hello", "remove", "123", "-y", "-U", "testuser"], capture_output=True, text=True)
            mock.load_model_list.assert_called_once()

    def test_on_model_delete_failure(self):
        mock = MagicMock()
        mock.active_user = "testuser"
        mock_selection = mock.treeview.get_selection.return_value
        
        mock_listmodel = MagicMock()
        mock_listmodel.get_value.side_effect = lambda iter, idx: "123" if idx == 0 else "model1"
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row1"])

        mock_confirm = MagicMock()
        mock_confirm.run.return_value = 1
        mock_error = MagicMock()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "some error"
        
        with patch("tab_models.gtk.MessageDialog", side_effect=[mock_confirm, mock_error]), \
             patch("subprocess.run", return_value=mock_result):
            
            tab_models.on_model_delete(mock, MagicMock())
            mock_error.run.assert_called_once()
            mock.load_model_list.assert_called_once()


# ── TestAuthHelper class ─────────────────────────────────────────────

class TestAuthHelper:
    def test_verify_user_password_no_pam_lib(self):
        with patch("os.path.exists", return_value=True),              patch("ctypes.util.find_library", return_value=None):
            result = auth_helper.verify_user_password("user", "pass")
            assert result is False

    def test_verify_user_password_no_c_lib(self):
        def find_lib_side_effect(lib):
            if lib == "pam":
                return "libpam.so"
            return None
        with patch("os.path.exists", return_value=True),              patch("ctypes.util.find_library", side_effect=find_lib_side_effect):
            result = auth_helper.verify_user_password("user", "pass")
            assert result is False

    def test_verify_user_password_success(self):
        def find_lib_side_effect(lib):
            return f"lib{lib}.so"

        mock_libpam = MagicMock()
        mock_libpam.pam_start.return_value = 0
        mock_libpam.pam_authenticate.return_value = 0
        mock_libpam.pam_end.return_value = 0

        mock_libc = MagicMock()
        allocated_buffers = []
        def mock_malloc(size):
            buf = ctypes.create_string_buffer(size)
            allocated_buffers.append(buf)
            return ctypes.addressof(buf)
        mock_libc.malloc.side_effect = mock_malloc

        captured_classes = {}
        saved_callbacks = []
        original_cfunctype = ctypes.CFUNCTYPE
        def mock_cfunctype(restype, *argtypes):
            real_class = original_cfunctype(restype, *argtypes)
            if len(argtypes) >= 3:
                captured_classes['PamMessage'] = argtypes[1]._type_._type_
                captured_classes['PamResponse'] = argtypes[2]._type_._type_
            class CFuncSubclass(real_class):
                _flags_ = real_class._flags_
                _argtypes_ = real_class._argtypes_
                _restype_ = real_class._restype_
                def __new__(cls, cb):
                    if cb:
                        saved_callbacks.append(cb)
                    return real_class.__new__(cls, cb)
            return CFuncSubclass

        exists_mock = MagicMock(return_value=False)
        open_mock = mock_open()

        with patch("os.path.exists", exists_mock), \
             patch("builtins.open", open_mock), \
             patch("os.chmod") as mock_chmod, \
             patch("ctypes.util.find_library", side_effect=find_lib_side_effect), \
             patch("ctypes.CDLL", side_effect=lambda path: mock_libpam if "pam" in path else mock_libc), \
             patch("ctypes.CFUNCTYPE", side_effect=mock_cfunctype):
            
            def pam_authenticate_side_effect(pamh, flags):
                if saved_callbacks:
                    cb = saved_callbacks[0]
                    PamMessage = captured_classes['PamMessage']
                    PamResponse = captured_classes['PamResponse']

                    msg1 = PamMessage(msg_style=1, msg=b"Password: ")
                    msg2 = PamMessage(msg_style=3, msg=b"Info message")

                    msg_array = (ctypes.POINTER(PamMessage) * 2)(ctypes.pointer(msg1), ctypes.pointer(msg2))
                    msg_p = ctypes.cast(msg_array, ctypes.POINTER(ctypes.POINTER(PamMessage)))
                    
                    resp_array = (ctypes.POINTER(PamResponse) * 1)(ctypes.pointer(PamResponse()))
                    resp_p = ctypes.cast(resp_array, ctypes.POINTER(ctypes.POINTER(PamResponse)))

                    ret = cb(2, msg_p, resp_p, None)
                    assert ret == 0
                return 0

            mock_libpam.pam_authenticate.side_effect = pam_authenticate_side_effect

            result = auth_helper.verify_user_password("testuser", "testpass")
            assert result is True
            exists_mock.assert_called_with("/etc/pam.d/ubuntu-hello-verify")
            open_mock.assert_called_with("/etc/pam.d/ubuntu-hello-verify", "w")
            mock_chmod.assert_called_with("/etc/pam.d/ubuntu-hello-verify", 0o644)

    def test_verify_user_password_conv_error(self):
        def find_lib_side_effect(lib):
            return f"lib{lib}.so"

        mock_libpam = MagicMock()
        mock_libpam.pam_start.return_value = 0
        mock_libpam.pam_authenticate.return_value = 0
        mock_libpam.pam_end.return_value = 0

        mock_libc = MagicMock()
        mock_libc.malloc.return_value = None

        captured_classes = {}
        saved_callbacks = []
        original_cfunctype = ctypes.CFUNCTYPE
        def mock_cfunctype(restype, *argtypes):
            real_class = original_cfunctype(restype, *argtypes)
            if len(argtypes) >= 3:
                captured_classes['PamMessage'] = argtypes[1]._type_._type_
                captured_classes['PamResponse'] = argtypes[2]._type_._type_
            class CFuncSubclass(real_class):
                _flags_ = real_class._flags_
                _argtypes_ = real_class._argtypes_
                _restype_ = real_class._restype_
                def __new__(cls, cb):
                    if cb:
                        saved_callbacks.append(cb)
                    return real_class.__new__(cls, cb)
            return CFuncSubclass

        with patch("os.path.exists", return_value=True), \
             patch("ctypes.util.find_library", side_effect=find_lib_side_effect), \
             patch("ctypes.CDLL", side_effect=lambda path: mock_libpam if "pam" in path else mock_libc), \
             patch("ctypes.CFUNCTYPE", side_effect=mock_cfunctype):
            
            def pam_authenticate_side_effect(pamh, flags):
                if saved_callbacks:
                    cb = saved_callbacks[0]
                    PamMessage = captured_classes['PamMessage']
                    PamResponse = captured_classes['PamResponse']
                    
                    msg1 = PamMessage(msg_style=1, msg=b"Password: ")
                    msg_array = (ctypes.POINTER(PamMessage) * 1)(ctypes.pointer(msg1))
                    msg_p = ctypes.cast(msg_array, ctypes.POINTER(ctypes.POINTER(PamMessage)))
                    resp_array = (ctypes.POINTER(PamResponse) * 1)(ctypes.pointer(PamResponse()))
                    resp_p = ctypes.cast(resp_array, ctypes.POINTER(ctypes.POINTER(PamResponse)))

                    ret = cb(1, msg_p, resp_p, None)
                    assert ret == 1
                return 0

            mock_libpam.pam_authenticate.side_effect = pam_authenticate_side_effect
            auth_helper.verify_user_password("testuser", "testpass")

    def test_verify_user_password_second_malloc_fail(self):
        def find_lib_side_effect(lib):
            return f"lib{lib}.so"

        mock_libpam = MagicMock()
        mock_libpam.pam_start.return_value = 0
        mock_libpam.pam_authenticate.return_value = 0
        mock_libpam.pam_end.return_value = 0

        mock_libc = MagicMock()
        
        allocated_buffers = []
        malloc_calls = [0]
        def mock_malloc(size):
            malloc_calls[0] += 1
            if malloc_calls[0] == 1:
                buf = ctypes.create_string_buffer(size)
                allocated_buffers.append(buf)
                return ctypes.addressof(buf)
            return None # fail second malloc

        mock_libc.malloc.side_effect = mock_malloc

        captured_classes = {}
        saved_callbacks = []
        original_cfunctype = ctypes.CFUNCTYPE
        def mock_cfunctype(restype, *argtypes):
            real_class = original_cfunctype(restype, *argtypes)
            if len(argtypes) >= 3:
                captured_classes['PamMessage'] = argtypes[1]._type_._type_
                captured_classes['PamResponse'] = argtypes[2]._type_._type_
            class CFuncSubclass(real_class):
                _flags_ = real_class._flags_
                _argtypes_ = real_class._argtypes_
                _restype_ = real_class._restype_
                def __new__(cls, cb):
                    if cb:
                        saved_callbacks.append(cb)
                    return real_class.__new__(cls, cb)
            return CFuncSubclass

        with patch("os.path.exists", return_value=True), \
             patch("ctypes.util.find_library", side_effect=find_lib_side_effect), \
             patch("ctypes.CDLL", side_effect=lambda path: mock_libpam if "pam" in path else mock_libc), \
             patch("ctypes.CFUNCTYPE", side_effect=mock_cfunctype):
            
            def pam_authenticate_side_effect(pamh, flags):
                if saved_callbacks:
                    cb = saved_callbacks[0]
                    PamMessage = captured_classes['PamMessage']
                    PamResponse = captured_classes['PamResponse']
                    
                    msg1 = PamMessage(msg_style=1, msg=b"Password: ")
                    msg_array = (ctypes.POINTER(PamMessage) * 1)(ctypes.pointer(msg1))
                    msg_p = ctypes.cast(msg_array, ctypes.POINTER(ctypes.POINTER(PamMessage)))
                    resp_array = (ctypes.POINTER(PamResponse) * 1)(ctypes.pointer(PamResponse()))
                    resp_p = ctypes.cast(resp_array, ctypes.POINTER(ctypes.POINTER(PamResponse)))

                    ret = cb(1, msg_p, resp_p, None)
                    assert ret == 1
                return 0

            mock_libpam.pam_authenticate.side_effect = pam_authenticate_side_effect
            auth_helper.verify_user_password("testuser", "testpass")
