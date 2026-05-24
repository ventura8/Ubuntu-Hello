"""Tests for onboarding.py camera preview functionality."""
import sys
import os
from unittest.mock import patch, MagicMock, mock_open
import pytest

import onboarding

def test_on_camera_selection_changed():
    with patch("onboarding.gtk.Builder") as mock_builder_cls, \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        # mock the selection
        mock_selection = MagicMock()
        mock_listmodel = MagicMock()
        mock_iter = MagicMock()
        
        # get_selected_rows and get_selected
        mock_selection.get_selected.return_value = (mock_listmodel, mock_iter)
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row1"])
        mock_listmodel.get_iter.return_value = mock_iter
        mock_listmodel.get_value.side_effect = lambda it, col: {
            0: "Camera Name",
            2: "/dev/video0",
            3: True
        }[col]
        
        with patch("threading.Thread") as mock_thread:
            ob.on_camera_selection_changed(mock_selection)
            assert ob.current_preview_path == "/dev/video0"
            mock_thread.assert_called_once()

def test_open_camera_for_preview():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.current_preview_path = "/dev/video0"
        
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 100.0
        
        # We change current_preview_path immediately to break the loop
        def side_effect(*args, **kwargs):
            ob.current_preview_path = None
            return (False, None)
        mock_cap.read.side_effect = side_effect
        
        with patch("cv2.VideoCapture", return_value=mock_cap) as mock_video_capture:
            ob.open_camera_for_preview("/dev/video0")
            mock_video_capture.assert_called_once_with("/dev/video0")
            mock_cap.release.assert_called_once()

def test_update_preview_image_widget():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.preview_image = MagicMock()
        ob.current_preview_path = "/dev/video0"
        
        mock_pix = MagicMock()
        res = ob.update_preview_image_widget("/dev/video0", mock_pix)
        assert res is False
        ob.preview_image.set_from_pixbuf.assert_called_once_with(mock_pix)

def test_stop_preview():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        mock_cap = MagicMock()
        ob.preview_capture = mock_cap
        ob.preview_image = MagicMock()
        ob.current_preview_path = "/dev/video0"
        
        mock_thread = MagicMock()
        ob.preview_thread = mock_thread
        
        ob.stop_preview()
        assert ob.current_preview_path is None
        assert ob.preview_capture is None
        mock_thread.join.assert_called_once_with(timeout=2.0)
        ob.preview_image.clear.assert_called_once()

def test_go_next_slide_stops_preview():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        ob.window.current_slide = 2
        
        # Mock treeview selection so execute_slide3 doesn't fail on unpacking
        mock_selection = MagicMock()
        mock_listmodel = MagicMock()
        mock_selection.get_selected.return_value = (mock_listmodel, None)
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row1"])
        ob.treeview = MagicMock()
        ob.treeview.get_selection.return_value = mock_selection
        
        with patch.object(ob, "stop_preview") as mock_stop:
            ob.go_next_slide()
            mock_stop.assert_called_once()

def test_execute_slide1_already_downloaded():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("os.path.exists", return_value=True):
        ob = onboarding.OnboardingWindow()
        ob.downloadoutputlabel = MagicMock()
        ob.nextbutton = MagicMock()
        ob.window = MagicMock()
        
        with patch.object(ob, "enable_next") as mock_enable:
            ob.execute_slide1()
            mock_enable.assert_called_once()

def test_execute_slide1_start_download():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("os.path.exists", return_value=False), \
         patch("subprocess.Popen") as mock_popen, \
         patch("threading.Thread") as mock_thread, \
         patch("gi.repository.GObject.timeout_add") as mock_timeout_add:
        ob = onboarding.OnboardingWindow()
        
        ob.execute_slide1()
        mock_popen.assert_called_once()
        mock_thread.assert_called_once()
        mock_timeout_add.assert_called_once()

def test_read_download_thread():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        import queue
        ob.download_queue = queue.Queue()
        ob.proc = MagicMock()
        ob.proc.stdout.readline.side_effect = [b"downloading file...\n", b""]
        
        ob.read_download_thread()
        
        assert ob.download_queue.get() == "downloading file...\n"
        assert ob.download_queue.get() is None

def test_update_download_gui_running():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.downloadoutputlabel = MagicMock()
        ob.download_lines = []
        
        import queue
        ob.download_queue = queue.Queue()
        ob.download_queue.put("progress line 1\n")
        
        res = ob.update_download_gui()
        assert res is True
        assert ob.download_lines == ["progress line 1\n"]
        ob.downloadoutputlabel.set_text.assert_called_with("progress line 1\n")

def test_update_download_gui_finished_success():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.downloadoutputlabel = MagicMock()
        ob.nextbutton = MagicMock()
        ob.window = MagicMock()
        ob.proc = MagicMock()
        ob.proc.wait.return_value = 0
        
        import queue
        ob.download_queue = queue.Queue()
        ob.download_queue.put(None)
        
        with patch.object(ob, "enable_next") as mock_enable:
            res = ob.update_download_gui()
            assert res is False
            mock_enable.assert_called_once()
            ob.downloadoutputlabel.set_text.assert_called_with("Done!\nClick Next to continue")

def test_update_download_gui_finished_failure():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.downloadoutputlabel = MagicMock()
        ob.proc = MagicMock()
        ob.proc.wait.return_value = 1 # error
        
        import queue
        ob.download_queue = queue.Queue()
        ob.download_queue.put(None)
        
        with patch.object(ob, "show_error") as mock_show_error:
            res = ob.update_download_gui()
            assert res is False
            mock_show_error.assert_called_once()

def test_execute_slide4():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        # Mock treeview selection
        mock_selection = MagicMock()
        mock_listmodel = MagicMock()
        mock_iter = MagicMock()
        mock_selection.get_selected.return_value = (mock_listmodel, mock_iter)
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row1"])
        ob.treeview = MagicMock()
        ob.treeview.get_selection.return_value = mock_selection
        mock_listmodel.get_value.side_effect = lambda it, col: "/dev/video0" if col == 2 else None
        
        ob.slide4_preview_image = MagicMock()
        
        with patch("subprocess.Popen") as mock_popen, \
             patch("threading.Thread") as mock_thread, \
             patch.object(ob, "stop_preview") as mock_stop_preview:
            ob.execute_slide4()
            mock_popen.assert_called_once()
            mock_stop_preview.assert_called_once()
            mock_thread.assert_called_once()
            assert ob.preview_image == ob.slide4_preview_image
            assert ob.current_preview_path == "/dev/video0"

def test_on_scanbutton_click():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.proc = MagicMock()
        ob.proc.wait.return_value = 0
        
        # Mock dialogue stuff so timeout add does not fail
        ob.dialog = MagicMock()
        
        with patch.object(ob, "stop_preview") as mock_stop_preview, \
             patch("gi.repository.GObject.timeout_add") as mock_timeout:
            ob.on_scanbutton_click(None)
            mock_stop_preview.assert_called_once()
            mock_timeout.assert_called_once()

def test_scan_cameras_thread():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("os.listdir", return_value=["device1", "device2"]), \
         patch("subprocess.check_output", return_value=b"product_name=\"My IR Camera\""), \
         patch("cv2.VideoCapture") as mock_vc, \
         patch("gi.repository.GLib.idle_add") as mock_idle_add:
        
        ob = onboarding.OnboardingWindow()
        
        mock_cap = MagicMock()
        mock_cap.read.return_value = (True, MagicMock())
        mock_vc.return_value = mock_cap
        
        ob.scan_cameras_thread()
        mock_idle_add.assert_called_once()
        args = mock_idle_add.call_args[0]
        assert args[0] == ob.update_camera_list_gui
        assert len(args[1]) == 2

def test_update_camera_list_gui():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("threading.Thread") as mock_thread:
        ob = onboarding.OnboardingWindow()
        ob.loadinglabel = MagicMock()
        ob.devicelistbox = MagicMock()
        
        device_rows = [["My IR Camera", "/dev/video0", 5, "Yes"]]
        ob.update_camera_list_gui(device_rows)
        
        ob.loadinglabel.hide.assert_called_once()
        ob.devicelistbox.add.assert_called_once()
        assert ob.current_preview_path == "/dev/video0"
        mock_thread.assert_called_once()

def test_execute_slide3_is_gray():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("cv2.VideoCapture") as mock_vc:
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        
        mock_selection = MagicMock()
        mock_listmodel = MagicMock()
        mock_iter = MagicMock()
        mock_selection.get_selected.return_value = (mock_listmodel, mock_iter)
        mock_listmodel.get_value.side_effect = lambda it, col: {
            2: "/dev/video0",
            3: True
        }[col]
        
        ob.treeview = MagicMock()
        ob.treeview.get_selection.return_value = mock_selection
        
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_vc.return_value = mock_cap
        
        ob.execute_slide3()
        assert ob.capture == mock_cap
        mock_cap.read.assert_called_once()

def test_execute_slide3_not_gray():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        
        mock_selection = MagicMock()
        mock_listmodel = MagicMock()
        mock_iter = MagicMock()
        mock_selection.get_selected.return_value = (mock_listmodel, mock_iter)
        mock_listmodel.get_value.side_effect = lambda it, col: {
            2: "/dev/video0",
            3: False
        }[col]
        
        ob.treeview = MagicMock()
        ob.treeview.get_selection.return_value = mock_selection
        
        with patch.object(ob, "go_next_slide") as mock_go_next:
            ob.execute_slide3()
            mock_go_next.assert_called_once()

def test_slide3_buttons():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.capture = MagicMock()
        ob.window = MagicMock()
        
        with patch.object(ob, "go_next_slide") as mock_go_next:
            ob.slide3_button_yes(None)
            ob.capture.release.assert_called_once()
            mock_go_next.assert_called_once()
            
        ob.capture.reset_mock()
        mock_status = MagicMock()
        mock_yes = MagicMock()
        mock_no = MagicMock()
        ob.builder.get_object.side_effect = lambda name: {
            "leiestatus": mock_status,
            "leieyesbutton": mock_yes,
            "leienobutton": mock_no
        }[name]
        
        ob.slide3_button_no(None)
        ob.capture.release.assert_called_once()
        mock_status.set_markup.assert_called_once()
        mock_yes.hide.assert_called_once()
        mock_no.hide.assert_called_once()

def test_execute_slide5():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        with patch.object(ob, "enable_next") as mock_enable:
            ob.execute_slide5()
            mock_enable.assert_called_once()

def test_execute_slide6_and_tpm():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("threading.Thread") as mock_thread:
        ob = onboarding.OnboardingWindow()
        ob.execute_slide6()
        mock_thread.assert_called_once()
        
def test_detect_tpm_thread():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("os.path.exists", return_value=True), \
         patch("shutil.which", return_value="/usr/bin/tpm2_unseal"), \
         patch("subprocess.run") as mock_run, \
         patch("gi.repository.GLib.idle_add") as mock_idle_add:
        ob = onboarding.OnboardingWindow()
        ob.detect_tpm_thread()
        mock_idle_add.assert_called()

def test_get_real_user():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        with patch.dict("os.environ", {"SUDO_USER": "testuser"}):
            assert ob.get_real_user() == "testuser"
            
        with patch.dict("os.environ", {"PKEXEC_UID": "1001"}), \
             patch("pwd.getpwuid") as mock_pwd:
            mock_pwd.return_value.pw_name = "pkuser"
            assert ob.get_real_user() == "pkuser"

def test_validate_and_save_keyring_active_success_tpm():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.KeyringPasswordDialog") as mock_dialog_cls, \
         patch("onboarding.auth_helper.verify_user_password", return_value=True), \
         patch("os.path.exists", side_effect=lambda p: True if "tpm" in p else False), \
         patch("shutil.which", return_value="/usr/bin/tpm"), \
         patch("subprocess.Popen") as mock_popen, \
         patch("subprocess.run") as mock_run, \
         patch("os.unlink") as mock_unlink, \
         patch("os.makedirs"), \
         patch("os.chmod"):
        ob = onboarding.OnboardingWindow()
        
        mock_checkbox = MagicMock()
        mock_checkbox.get_active.return_value = True
        ob.builder.get_object.return_value = mock_checkbox
        
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.OK
        mock_dialog.entry1.get_text.return_value = "mypassword"
        mock_dialog_cls.return_value = mock_dialog
        
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        
        with patch.object(ob, "get_real_user", return_value="testuser"):
            assert ob.validate_and_save_keyring() is True

def test_validate_and_save_keyring_active_success_software():
    from unittest.mock import mock_open
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.KeyringPasswordDialog") as mock_dialog_cls, \
         patch("onboarding.auth_helper.verify_user_password", return_value=True), \
         patch("os.path.exists", return_value=False), \
         patch("shutil.which", return_value=None), \
         patch("builtins.open", mock_open(read_data="mymachineid\n")):
        ob = onboarding.OnboardingWindow()
        
        mock_checkbox = MagicMock()
        mock_checkbox.get_active.return_value = True
        ob.builder.get_object.return_value = mock_checkbox
        
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.OK
        mock_dialog.entry1.get_text.return_value = "mypassword"
        mock_dialog_cls.return_value = mock_dialog
        
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("os.makedirs"), \
             patch("os.chmod"):
            assert ob.validate_and_save_keyring() is True

def test_execute_slide7():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("subprocess.Popen") as mock_popen, \
         patch("onboarding.gtk.MessageDialog") as mock_dialog:
        ob = onboarding.OnboardingWindow()
        
        mock_radio = MagicMock()
        mock_radio.get_active.return_value = True
        mock_radio.get_group.return_value = [mock_radio]
        
        with patch("onboarding.gtk.Buildable.get_name", return_value="radiobalanced"):
            ob.builder.get_object.side_effect = lambda name: {
                "radiobalanced": mock_radio,
                "cancelbutton": MagicMock(),
                "finishbutton": MagicMock(),
                "navigationbar": MagicMock()
            }.get(name, MagicMock())
            
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc
            
            ob.execute_slide7()
            mock_popen.assert_called_once()
            assert ob.nextbutton.hide.called

def test_on_finishbutton_click():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.gtk.main_quit") as mock_quit:
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        
        ob.on_finishbutton_click(None)
        assert ob.completed is True
        ob.window.destroy.assert_called_once()
        mock_quit.assert_called_once()

def test_exit():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.gtk.main_quit") as mock_quit:
        ob = onboarding.OnboardingWindow()
        ob.completed = True
        
        with patch.object(ob, "stop_preview") as mock_stop:
            ob.exit()
            mock_stop.assert_called_once()
            mock_quit.assert_called_once()

def test_on_camera_selection_changed_none():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        mock_selection = MagicMock()
        mock_selection.get_selected.return_value = (None, None)
        mock_selection.get_selected_rows.return_value = (None, [])
        
        with patch.object(ob, "stop_preview") as mock_stop:
            ob.on_camera_selection_changed(mock_selection)
            mock_stop.assert_called_once()

def test_go_next_slide_transitions():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        ob.nextbutton = MagicMock()
        ob.slides = [MagicMock() for _ in range(10)]
        ob.slidecontainer = MagicMock()
        
        ob.execute_slide1 = MagicMock()
        ob.execute_slide2 = MagicMock()
        ob.execute_slide3 = MagicMock()
        ob.execute_slide4 = MagicMock()
        ob.execute_slide5 = MagicMock()
        ob.execute_slide6 = MagicMock()
        ob.execute_slide7 = MagicMock()
        
        ob.window.current_slide = 0
        ob.go_next_slide()
        assert ob.window.current_slide == 1
        ob.execute_slide1.assert_called_once()
        
        with patch("gi.repository.GObject.timeout_add") as mock_timeout:
            ob.go_next_slide()
            assert ob.window.current_slide == 2
            mock_timeout.assert_called_once()
            
        with patch.object(ob, "stop_preview") as mock_stop:
            ob.go_next_slide()
            assert ob.window.current_slide == 3
            mock_stop.assert_called_once()
            ob.execute_slide3.assert_called_once()
            
        ob.go_next_slide()
        assert ob.window.current_slide == 4
        ob.execute_slide4.assert_called_once()
        
        ob.go_next_slide()
        assert ob.window.current_slide == 5
        ob.execute_slide5.assert_called_once()
        
        ob.go_next_slide()
        assert ob.window.current_slide == 6
        ob.execute_slide6.assert_called_once()

def test_validate_and_save_keyring_errors():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.KeyringPasswordDialog") as mock_dialog_cls, \
         patch("onboarding.auth_helper.verify_user_password") as mock_verify, \
         patch("os.path.exists", return_value=False), \
         patch("shutil.which", return_value=None), \
         patch("os.unlink") as mock_unlink, \
         patch("onboarding.gtk.MessageDialog"):
        ob = onboarding.OnboardingWindow()
        
        mock_checkbox = MagicMock()
        mock_checkbox.get_active.return_value = False
        ob.builder.get_object.return_value = mock_checkbox
        with patch("os.path.exists", return_value=True), \
             patch.object(ob, "get_real_user", return_value="testuser"):
            assert ob.validate_and_save_keyring() is True
            mock_unlink.assert_called()
            
        mock_checkbox.get_active.return_value = True
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.CANCEL
        mock_dialog_cls.return_value = mock_dialog
        with patch.object(ob, "get_real_user", return_value="testuser"):
            assert ob.validate_and_save_keyring() is False
            
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.OK
        mock_dialog.entry1.get_text.return_value = ""
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch.object(ob, "show_keyring_error") as mock_err:
            assert ob.validate_and_save_keyring() is False
            mock_err.assert_called_once_with("Password cannot be empty")
            
        mock_dialog.entry1.get_text.return_value = "wrongpass"
        mock_verify.return_value = False
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch.object(ob, "show_keyring_error") as mock_err:
            assert ob.validate_and_save_keyring() is False
            mock_err.assert_called_once()

def test_keyring_password_dialog():
    with patch("onboarding.gtk.Dialog.get_content_area", create=True) as mock_get_content, \
         patch("onboarding.gtk.Label") as mock_label, \
         patch("onboarding.gtk.Entry") as mock_entry, \
         patch("onboarding.gtk.Dialog.show_all", create=True):
         
        mock_box = MagicMock()
        mock_get_content.return_value = mock_box
        
        dialog = onboarding.KeyringPasswordDialog(None, "testuser")
        
        # Test activate connector
        mock_entry_instance = mock_entry.return_value
        assert mock_entry_instance.connect.called
        # Call the lambda passed to connect
        activate_handler = mock_entry_instance.connect.call_args[0][1]
        with patch.object(dialog, "response") as mock_response:
            activate_handler(mock_entry_instance)
            mock_response.assert_called_once_with(onboarding.gtk.ResponseType.OK)

def test_go_next_slide_slide6_validation_failure():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        ob.window.current_slide = 6
        ob.nextbutton = MagicMock()
        ob.validate_and_save_keyring = MagicMock(return_value=False)
        ob.enable_next = MagicMock()
        
        ob.go_next_slide()
        ob.enable_next.assert_called_once()

def test_go_next_slide_transition_to_slide7():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.window = MagicMock()
        ob.window.current_slide = 6
        ob.nextbutton = MagicMock()
        ob.validate_and_save_keyring = MagicMock(return_value=True)
        ob.slides = [MagicMock() for _ in range(10)]
        ob.slidecontainer = MagicMock()
        ob.execute_slide7 = MagicMock()
        
        ob.go_next_slide()
        ob.execute_slide7.assert_called_once()

def test_update_download_gui_exceptions_and_slicing():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_error") as mock_show_error:
        ob = onboarding.OnboardingWindow()
        ob.downloadoutputlabel = MagicMock()
        ob.proc = MagicMock()
        
        # 1. wait raises exception
        ob.download_queue = MagicMock()
        ob.download_queue.get_nowait.side_effect = [None]
        ob.proc.wait.side_effect = Exception("timeout")
        res = ob.update_download_gui()
        assert res is False
        mock_show_error.assert_called_once()
        
        # 2. status != 0
        mock_show_error.reset_mock()
        ob.download_queue.get_nowait.side_effect = [None]
        ob.proc.wait.side_effect = None
        ob.proc.wait.return_value = 1
        res = ob.update_download_gui()
        assert res is False
        mock_show_error.assert_called_once()
        
        # 3. lines slicing (> 10 lines)
        import queue
        ob.download_queue = queue.Queue()
        for i in range(15):
            ob.download_queue.put(f"line {i}\n")
        ob.download_lines = []
        res = ob.update_download_gui()
        assert res is True
        assert len(ob.download_lines) == 10
        assert ob.download_lines[-1] == "line 14\n"

def test_execute_slide2():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("threading.Thread") as mock_thread:
        ob = onboarding.OnboardingWindow()
        ob.execute_slide2()
        mock_thread.assert_called_once()

def test_scan_cameras_thread_exceptions():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("gi.repository.GLib.idle_add") as mock_idle:
        ob = onboarding.OnboardingWindow()
        
        # 1. import cv2 exception
        with patch.dict("sys.modules", {"cv2": None}):
            ob.scan_cameras_thread()
            mock_idle.assert_called_once()
            
        # 2. listdir exception
        mock_idle.reset_mock()
        with patch("os.listdir", side_effect=Exception("no dir")):
            ob.scan_cameras_thread()
            mock_idle.assert_called_once()

def test_scan_cameras_thread_udevadm_and_incompatible():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("gi.repository.GLib.idle_add") as mock_idle:
        ob = onboarding.OnboardingWindow()
        
        # We simulate devices in listdir
        with patch("os.listdir", return_value=["device1"]), \
             patch("time.sleep"), \
             patch("subprocess.check_output") as mock_check_output, \
             patch("os.path.realpath") as mock_real_path, \
             patch("cv2.VideoCapture") as mock_vc:
             
            # udevadm product match
            mock_check_output.return_value = b"ID_MODEL=product_test\nPRODUCT=1/2/3\n"
            
            # Videocapture fails to open
            mock_cap = MagicMock()
            mock_cap.read.return_value = (False, None)
            mock_vc.return_value = mock_cap
            
            ob.scan_cameras_thread()
            # It should have updated with incompatible status
            mock_idle.assert_called_once()
            device_rows = mock_idle.call_args[0][1]
            assert device_rows[0][2] == -9
            
            # Videocapture opens but is color (np.all fails)
            mock_idle.reset_mock()
            import numpy as np
            mock_cap.read.return_value = (True, np.ones((10, 10, 3)))
            # We modify color of one channel to make it color
            mock_cap.read.return_value[1][0, 0, 0] = 0
            
            ob.scan_cameras_thread()
            device_rows = mock_idle.call_args[0][1]
            assert device_rows[0][2] == -5

def test_scan_cameras_thread_udevadm_exception():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("gi.repository.GLib.idle_add") as mock_idle:
        ob = onboarding.OnboardingWindow()
        
        with patch("os.listdir", return_value=["device1"]), \
             patch("time.sleep"), \
             patch("subprocess.check_output", side_effect=Exception("udevadm error")), \
             patch("os.path.realpath") as mock_real_path, \
             patch("cv2.VideoCapture") as mock_vc:
             
            mock_cap = MagicMock()
            mock_cap.read.return_value = (False, None)
            mock_vc.return_value = mock_cap
            
            ob.scan_cameras_thread()
            assert mock_idle.called

def test_execute_slide3_errors():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_error") as mock_show_error:
        ob = onboarding.OnboardingWindow()
        
        # 1. import cv2 fails
        with patch.dict("sys.modules", {"cv2": None}):
            ob.treeview = MagicMock()
            ob.treeview.get_selection.return_value.get_selected.return_value = (None, None)
            ob.treeview.get_selection.return_value.get_selected_rows.return_value = (None, [])
            ob.execute_slide3()
            mock_show_error.assert_called()

        # 2. treeiter is None and get_iter throws exception
        mock_show_error.reset_mock()
        ob.treeview = MagicMock()
        mock_selection = ob.treeview.get_selection.return_value
        mock_selection.get_selected.return_value = (None, None)
        mock_model = MagicMock()
        mock_selection.get_selected_rows.return_value = (mock_model, ["row1"])
        mock_model.get_iter.side_effect = Exception("get_iter fail")
        ob.execute_slide3()
        mock_show_error.assert_called_once_with("Error selecting camera")
        
        # 3. capture cannot be opened
        mock_show_error.reset_mock()
        ob.treeview = MagicMock()
        mock_model = MagicMock()
        mock_iter = MagicMock()
        ob.treeview.get_selection.return_value.get_selected.return_value = (mock_model, mock_iter)
        mock_model.get_value.side_effect = lambda it, col: "/dev/video0" if col == 2 else True
        
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        with patch("cv2.VideoCapture", return_value=mock_cap):
            ob.execute_slide3()
            mock_show_error.assert_called_once_with("The selected camera cannot be opened", "Try to select another one")

def test_slide3_buttons():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.capture = MagicMock()
        ob.go_next_slide = MagicMock()
        
        ob.slide3_button_no(None)
        ob.capture.release.assert_called_once()
        
        ob.capture.release.reset_mock()
        ob.slide3_button_yes(None)
        ob.capture.release.assert_called_once()
        ob.go_next_slide.assert_called_once()

def test_execute_slide4_iter_none():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_error") as mock_show_error:
        ob = onboarding.OnboardingWindow()
        ob.treeview = MagicMock()
        ob.treeview.get_selection.return_value.get_selected.return_value = (None, None)
        ob.treeview.get_selection.return_value.get_selected_rows.return_value = (None, [])
        
        ob.execute_slide4()
        mock_show_error.assert_called_once_with("Error selecting camera")

def test_execute_slide4_iter_exception():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_error") as mock_show_error:
        ob = onboarding.OnboardingWindow()
        ob.treeview = MagicMock()
        mock_selection = ob.treeview.get_selection.return_value
        mock_selection.get_selected.return_value = (None, None)
        mock_model = MagicMock()
        mock_selection.get_selected_rows.return_value = (mock_model, ["row1"])
        mock_model.get_iter.side_effect = Exception("iter fail")
        
        ob.execute_slide4()
        mock_show_error.assert_called_once_with("Error selecting camera")

def test_run_add_failure():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_error") as mock_show_error:
        ob = onboarding.OnboardingWindow()
        ob.dialog = MagicMock()
        ob.go_next_slide = MagicMock()
        
        with patch("subprocess.getstatusoutput", return_value=(1, "failed to save model")):
            ob.run_add()
            mock_show_error.assert_called_once_with("Can't save face model", "failed to save model")

def test_detect_tpm_thread_installation_and_ui():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("gi.repository.GLib.idle_add") as mock_idle:
        ob = onboarding.OnboardingWindow()
        
        # 1. tpm dev exists, tools missing, apt-get fails
        with patch("os.path.exists", return_value=True), \
             patch("shutil.which", return_value=None), \
             patch("subprocess.run", side_effect=Exception("apt fail")):
            ob.detect_tpm_thread()
            assert mock_idle.called
            
        # 2. tpm dev doesn't exist
        mock_idle.reset_mock()
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", return_value=None):
            ob.detect_tpm_thread()
            update_ui_func = mock_idle.call_args[0][0]
            ob.builder.get_object.reset_mock()
            update_ui_func()
            ob.builder.get_object.return_value.set_markup.assert_called_once()

def test_detect_tpm_thread_success_flow():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("gi.repository.GLib.idle_add") as mock_idle:
        ob = onboarding.OnboardingWindow()
        
        # Simulate tpm dev exists, tools missing, apt-get installation succeeds
        shutil_which_calls = []
        def which_side_effect(cmd):
            shutil_which_calls.append(cmd)
            if len(shutil_which_calls) > 1:
                return "/usr/bin/" + cmd
            return None
            
        with patch("os.path.exists", return_value=True), \
             patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run") as mock_run:
            ob.detect_tpm_thread()
            assert mock_run.called
            update_ui_func = mock_idle.call_args[0][0]
            ob.builder.get_object.reset_mock()
            update_ui_func()
            # check that it set description for tpm active
            markup_text = ob.builder.get_object.return_value.set_markup.call_args[0][0]
            assert "Hardware TPM 2.0 active" in markup_text

def test_on_keyring_checkbox_toggled():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.on_keyring_checkbox_toggled(None)

def test_get_real_user_fallbacks():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("os.getlogin", side_effect=Exception), \
         patch("subprocess.check_output", side_effect=Exception):
        ob = onboarding.OnboardingWindow()
        
        # 1. PKEXEC_UID set, but pwd raises exception
        with patch.dict("os.environ", {"PKEXEC_UID": "1000", "SUDO_USER": "root", "USER": "root"}), \
             patch.dict("sys.modules", {"pwd": None}):
            user = ob.get_real_user()
            assert user == "root"
            
        # 2. os.getlogin raises exception, loginctl check_output raises exception
        with patch.dict("os.environ", {"SUDO_USER": "root", "PKEXEC_UID": "", "USER": "fallback_user"}):
            user = ob.get_real_user()
            assert user == "fallback_user"
            
        # 3. loginctl parses sessions successfully
        with patch("subprocess.check_output", return_value="1 1000 root\n2 1001 logged_user\n"), \
             patch.dict("os.environ", {"SUDO_USER": "root", "PKEXEC_UID": "", "USER": "root"}):
            user = ob.get_real_user()
            assert user == "logged_user"

def test_validate_and_save_keyring_more_errors():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_keyring_error") as mock_err:
        ob = onboarding.OnboardingWindow()
        
        # 1. user is root
        with patch.object(ob, "get_real_user", return_value="root"):
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once_with("Could not identify non-root system user for keyring unlocking")
            
        # 2. TPM tools exist, tpm2_create fails (throws exception)
        mock_err.reset_mock()
        mock_checkbox = MagicMock()
        mock_checkbox.get_active.return_value = True
        ob.builder.get_object.return_value = mock_checkbox
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.OK
        mock_dialog.entry1.get_text.return_value = "secret"
        
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("onboarding.KeyringPasswordDialog", return_value=mock_dialog), \
             patch("onboarding.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("shutil.which", return_value="some_tool"), \
             patch("subprocess.run", side_effect=Exception("TPM Error")):
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once()
            
        # 3. Software fallback, machine-id empty
        mock_err.reset_mock()
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("onboarding.KeyringPasswordDialog", return_value=mock_dialog), \
             patch("onboarding.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", mock_open(read_data="")), \
             patch("shutil.which", return_value=None):
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once_with("/etc/machine-id is empty")
            
        # 4. Software fallback, machine-id read throws exception
        mock_err.reset_mock()
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("onboarding.KeyringPasswordDialog", return_value=mock_dialog), \
             patch("onboarding.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", side_effect=Exception("Read error")), \
             patch("shutil.which", return_value=None):
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once()

        # 5. Software fallback, writing key_file throws exception
        mock_err.reset_mock()
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("onboarding.KeyringPasswordDialog", return_value=mock_dialog), \
             patch("onboarding.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open") as mock_file_open, \
             patch("shutil.which", return_value=None):
            mock_file_open.side_effect = [mock_open(read_data="12345").return_value, Exception("Write error")]
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once()
            
        # 6. Disable throws exception
        mock_err.reset_mock()
        mock_checkbox.get_active.return_value = False
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink", side_effect=Exception("Delete error")):
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once()

def test_validate_and_save_keyring_tpm_create_failures():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_keyring_error") as mock_err:
        ob = onboarding.OnboardingWindow()
        
        mock_checkbox = MagicMock()
        mock_checkbox.get_active.return_value = True
        ob.builder.get_object.return_value = mock_checkbox
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.OK
        mock_dialog.entry1.get_text.return_value = "secret"
        
        # tpm2_create returns non-zero code
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"tpm error log")
        mock_proc.returncode = 1
        
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("onboarding.KeyringPasswordDialog", return_value=mock_dialog), \
             patch("onboarding.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("shutil.which", return_value="some_tool"), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch("os.unlink", side_effect=[None, Exception("ctx unlink fail")]):
            res = ob.validate_and_save_keyring()
            assert res is False
            mock_err.assert_called_once()

def test_validate_and_save_keyring_apt_install_tpm2_tools():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_keyring_error") as mock_err:
        ob = onboarding.OnboardingWindow()
        
        mock_checkbox = MagicMock()
        mock_checkbox.get_active.return_value = True
        ob.builder.get_object.return_value = mock_checkbox
        mock_dialog = MagicMock()
        mock_dialog.run.return_value = onboarding.gtk.ResponseType.OK
        mock_dialog.entry1.get_text.return_value = "secret"
        
        # tpm rm exists, tools missing, apt-get fails
        with patch.object(ob, "get_real_user", return_value="testuser"), \
             patch("onboarding.KeyringPasswordDialog", return_value=mock_dialog), \
             patch("onboarding.auth_helper.verify_user_password", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("shutil.which", return_value=None), \
             patch("subprocess.run", side_effect=Exception("apt fail")):
            res = ob.validate_and_save_keyring()
            mock_err.assert_called_once()

def test_execute_slide7_errors():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch.object(onboarding.OnboardingWindow, "show_error") as mock_show_error, \
         patch("onboarding.gtk.MessageDialog"):
        ob = onboarding.OnboardingWindow()
        
        # 1. radio_selected is False
        ob.builder.get_object.return_value.get_group.return_value = []
        ob.execute_slide7()
        mock_show_error.assert_called_once_with("Error reading radio buttons")
        
        # 2. proc.wait raises exception
        mock_show_error.reset_mock()
        mock_radio = MagicMock()
        mock_radio.get_active.return_value = True
        onboarding.gtk.Buildable.get_name = MagicMock(return_value="radiofast")
        ob.builder.get_object.return_value.get_group.return_value = [mock_radio]
        
        ob.proc = MagicMock()
        ob.proc.wait.side_effect = Exception("Wait error")
        ob.execute_slide7()

def test_execute_slide7_balanced_and_secure():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.gtk.MessageDialog"):
        ob = onboarding.OnboardingWindow()
        
        # Test radiobalanced
        mock_radio = MagicMock()
        mock_radio.get_active.return_value = True
        onboarding.gtk.Buildable.get_name = MagicMock(return_value="radiobalanced")
        ob.builder.get_object.return_value.get_group.return_value = [mock_radio]
        ob.proc = MagicMock()
        ob.execute_slide7()
        
        # Test radiosecure
        onboarding.gtk.Buildable.get_name = MagicMock(return_value="radiosecure")
        ob.execute_slide7()

def test_show_keyring_error():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.gtk.MessageDialog") as mock_dialog:
        ob = onboarding.OnboardingWindow()
        ob.show_keyring_error("testmsg")
        mock_dialog.assert_called_once()

def test_show_error():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("onboarding.gtk.MessageDialog") as mock_dialog:
        ob = onboarding.OnboardingWindow()
        with patch.object(ob, "exit") as mock_exit:
            ob.show_error("err", "sec")
            mock_dialog.assert_called_once()
            mock_exit.assert_called_once()

def test_on_camera_selection_changed_more():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        # 1. model.get_value throws exception
        mock_selection = MagicMock()
        mock_listmodel = MagicMock()
        mock_iter = MagicMock()
        mock_selection.get_selected.return_value = (mock_listmodel, mock_iter)
        mock_listmodel.get_value.side_effect = Exception("Get value error")
        with patch.object(ob, "stop_preview") as mock_stop:
            ob.on_camera_selection_changed(mock_selection)
            mock_stop.assert_called_once()
            
        # 2. current_preview_path == device_path
        mock_listmodel.get_value.side_effect = None
        mock_listmodel.get_value.return_value = "/dev/video0"
        ob.current_preview_path = "/dev/video0"
        with patch.object(ob, "stop_preview") as mock_stop:
            ob.on_camera_selection_changed(mock_selection)
            mock_stop.assert_not_called()
            
        # 3. treeiter is None but rowlist length is 1
        mock_selection.get_selected.return_value = (mock_listmodel, None)
        mock_selection.get_selected_rows.return_value = (mock_listmodel, ["row0"])
        mock_listmodel.get_iter.return_value = mock_iter
        ob.current_preview_path = "/dev/video1"
        with patch("threading.Thread") as mock_thread:
            ob.on_camera_selection_changed(mock_selection)
            mock_thread.assert_called_once()

def test_open_camera_for_preview_errors():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        
        # 1. cap.isOpened is False
        ob.current_preview_path = "/dev/video0"
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        with patch("cv2.VideoCapture", return_value=mock_cap):
            ob.open_camera_for_preview("/dev/video0")
            mock_cap.release.assert_called_once()
            
        # 2. current_preview_path != device_path immediately
        ob.current_preview_path = "/dev/video1"
        mock_cap.reset_mock()
        mock_cap.isOpened.return_value = True
        with patch("cv2.VideoCapture", return_value=mock_cap):
            ob.open_camera_for_preview("/dev/video0")
            mock_cap.release.assert_called_once()
            
        # 3. cv2.resize throws Exception
        ob.current_preview_path = "/dev/video0"
        mock_cap.reset_mock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 100.0
        
        # Return True for first read, then change current_preview_path to stop loop
        def side_effect(*args, **kwargs):
            ob.current_preview_path = None
            return (True, "frame")
        mock_cap.read.side_effect = side_effect
        
        with patch("cv2.VideoCapture", return_value=mock_cap), \
             patch("cv2.resize", side_effect=Exception("resize error")):
            ob.open_camera_for_preview("/dev/video0")
            mock_cap.release.assert_called_once()

def test_open_camera_for_preview_successful_loop():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"), \
         patch("gi.repository.GLib.idle_add") as mock_idle:
        ob = onboarding.OnboardingWindow()
        ob.preview_image = MagicMock()
        ob.current_preview_path = "/dev/video0"
        
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = lambda prop: 100.0 if prop == 4 else 200.0
        
        read_calls = 0
        def read_side_effect():
            nonlocal read_calls
            read_calls += 1
            if read_calls > 1:
                ob.current_preview_path = None
            return (True, "dummy_frame")
        mock_cap.read.side_effect = read_side_effect
        
        mock_pixbuf_loader = MagicMock()
        
        with patch("cv2.VideoCapture", return_value=mock_cap), \
             patch("cv2.resize") as mock_resize, \
             patch("cv2.imencode", return_value=(True, b"imagedata")), \
             patch("gi.repository.GdkPixbuf.PixbufLoader", return_value=mock_pixbuf_loader):
            ob.open_camera_for_preview("/dev/video0")
            assert mock_resize.called
            assert mock_idle.called

def test_stop_preview_exceptions():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        mock_thread = MagicMock()
        mock_thread.join.side_effect = Exception("join error")
        ob.preview_thread = mock_thread
        ob.preview_image = MagicMock()
        
        ob.stop_preview()
        assert ob.preview_thread is None

def test_exit_func():
    with patch("onboarding.gtk.Builder"), \
         patch("onboarding.paths_factory.onboarding_wireframe_path", return_value="mock.glade"):
        ob = onboarding.OnboardingWindow()
        ob.completed = False
        with pytest.raises(SystemExit):
            ob.exit()
