"""Tests for rubberstamps module: RubberStamp base class, hotkey, nod."""
import sys
import time
from unittest.mock import MagicMock, patch, PropertyMock
import pytest
import configparser

from rubberstamps import RubberStamp, execute
from rubberstamps.hotkey import hotkey
from rubberstamps.nod import nod


# ── RubberStamp base class ──────────────────────────────────────────

class TestRubberStamp:
    def _make_stamp(self, verbose=False):
        stamp = RubberStamp()
        stamp.config = configparser.ConfigParser()
        stamp.config.add_section("debug")
        stamp.config.set("debug", "verbose_stamps", str(verbose))
        stamp.gtk_proc = None
        return stamp

    def test_set_ui_text_main(self):
        stamp = self._make_stamp()
        result = stamp.set_ui_text("Hello", RubberStamp.UI_TEXT)
        # gtk_proc is None so send_ui_raw just returns None
        assert result is None

    def test_set_ui_text_subtext(self):
        stamp = self._make_stamp()
        stamp.set_ui_text("sub", RubberStamp.UI_SUBTEXT)

    def test_set_ui_text_default_type(self):
        stamp = self._make_stamp()
        stamp.set_ui_text("msg")

    def test_send_ui_raw_no_proc(self):
        stamp = self._make_stamp()
        stamp.send_ui_raw("M=test")

    def test_send_ui_raw_with_proc(self):
        stamp = self._make_stamp()
        mock_proc = MagicMock()
        stamp.gtk_proc = mock_proc
        stamp.send_ui_raw("M=test")
        assert mock_proc.stdin.write.called
        assert mock_proc.stdin.flush.called

    def test_send_ui_raw_verbose(self):
        stamp = self._make_stamp(verbose=True)
        stamp.send_ui_raw("M=test")

    def test_ui_text_constants(self):
        assert RubberStamp.UI_TEXT == "ui_text"
        assert RubberStamp.UI_SUBTEXT == "ui_subtext"


# ── execute() function ──────────────────────────────────────────────

class TestExecute:
    def _make_config(self, rules=""):
        config = configparser.ConfigParser()
        config.add_section("debug")
        config.set("debug", "verbose_stamps", "False")
        config.add_section("rubberstamps")
        config.set("rubberstamps", "stamp_rules", rules)
        return config

    def test_execute_empty_rules(self):
        config = self._make_config("")
        with patch("sys.exit") as mock_exit:
            execute(config, None, {})
            mock_exit.assert_called_once_with(0)

    def test_execute_invalid_rule(self):
        config = self._make_config("invalid rule format!!!")
        with patch("sys.exit") as mock_exit:
            execute(config, None, {})
            mock_exit.assert_called_once_with(0)

    def test_execute_stamp_not_installed(self):
        config = self._make_config("nonexistent 5.0 failsafe")
        with patch("os.listdir", return_value=["hotkey.py", "nod.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("sys.exit") as mock_exit:
            execute(config, None, {})
            mock_exit.assert_called_once_with(0)

    def test_execute_success_with_hotkey_stamp(self):
        config = self._make_config("hotkey 5.0 failsafe confirm_key=a timeout=2.5")
        mock_module = MagicMock()
        mock_hotkey_class = MagicMock()
        mock_module.hotkey = mock_hotkey_class
        mock_instance = mock_hotkey_class.return_value
        mock_instance.options = {}
        def mock_declare():
            mock_instance.options["confirm_key"] = "enter"
        mock_instance.declare_config.side_effect = mock_declare
        mock_instance.run.return_value = True

        with patch("os.listdir", return_value=["hotkey.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("rubberstamps.SourceFileLoader") as mock_loader_class, \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            
            mock_loader_instance = mock_loader_class.return_value
            mock_loader_instance.load_module.return_value = mock_module
            
            with pytest.raises(SystemExit):
                execute(config, None, {
                    "video_capture": None,
                    "face_detector": None,
                    "pose_predictor": None,
                    "clahe": None
                })
            mock_exit.assert_called_once_with(0)
            mock_instance.declare_config.assert_called_once()
            mock_instance.run.assert_called_once()
            assert mock_instance.options["confirm_key"] == "a"
            assert mock_instance.options["timeout"] == 2.5

    def test_execute_stamp_returns_false(self):
        config = self._make_config("hotkey 5.0 failsafe")
        mock_module = MagicMock()
        mock_hotkey_class = MagicMock()
        mock_module.hotkey = mock_hotkey_class
        mock_instance = mock_hotkey_class.return_value
        mock_instance.options = {}
        mock_instance.run.return_value = False

        with patch("os.listdir", return_value=["hotkey.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("rubberstamps.SourceFileLoader") as mock_loader_class, \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            
            mock_loader_instance = mock_loader_class.return_value
            mock_loader_instance.load_module.return_value = mock_module
            
            with pytest.raises(SystemExit):
                execute(config, None, {
                    "video_capture": None,
                    "face_detector": None,
                    "pose_predictor": None,
                    "clahe": None
                })
            mock_exit.assert_called_once_with(15)

    def test_execute_class_not_found(self):
        config = self._make_config("hotkey 5.0 failsafe")
        mock_module = MagicMock()
        del mock_module.hotkey

        with patch("os.listdir", return_value=["hotkey.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("rubberstamps.SourceFileLoader") as mock_loader_class, \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            
            mock_loader_instance = mock_loader_class.return_value
            mock_loader_instance.load_module.return_value = mock_module
            
            with pytest.raises(SystemExit):
                execute(config, None, {
                    "video_capture": None,
                    "face_detector": None,
                    "pose_predictor": None,
                    "clahe": None
                })
            mock_exit.assert_called_once_with(0)

    def test_execute_declare_config_exception(self):
        config = self._make_config("hotkey 5.0 failsafe")
        mock_module = MagicMock()
        mock_hotkey_class = MagicMock()
        mock_module.hotkey = mock_hotkey_class
        mock_instance = mock_hotkey_class.return_value
        mock_instance.options = {}
        mock_instance.declare_config.side_effect = Exception("declare fail")

        with patch("os.listdir", return_value=["hotkey.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("rubberstamps.SourceFileLoader") as mock_loader_class, \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            
            mock_loader_instance = mock_loader_class.return_value
            mock_loader_instance.load_module.return_value = mock_module
            
            with pytest.raises(SystemExit):
                execute(config, None, {
                    "video_capture": None,
                    "face_detector": None,
                    "pose_predictor": None,
                    "clahe": None
                })
            mock_exit.assert_called_once_with(0)

    def test_execute_run_exception(self):
        config = self._make_config("hotkey 5.0 failsafe")
        mock_module = MagicMock()
        mock_hotkey_class = MagicMock()
        mock_module.hotkey = mock_hotkey_class
        mock_instance = mock_hotkey_class.return_value
        mock_instance.options = {}
        mock_instance.run.side_effect = Exception("run fail")

        with patch("os.listdir", return_value=["hotkey.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("rubberstamps.SourceFileLoader") as mock_loader_class, \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            
            mock_loader_instance = mock_loader_class.return_value
            mock_loader_instance.load_module.return_value = mock_module
            
            with pytest.raises(SystemExit):
                execute(config, None, {
                    "video_capture": None,
                    "face_detector": None,
                    "pose_predictor": None,
                    "clahe": None
                })
            mock_exit.assert_called_once_with(0)

    def test_execute_unknown_option(self):
        config = self._make_config("hotkey 5.0 failsafe unknown_opt=1")
        mock_module = MagicMock()
        mock_hotkey_class = MagicMock()
        mock_module.hotkey = mock_hotkey_class
        mock_instance = mock_hotkey_class.return_value
        mock_instance.options = {}
        mock_instance.run.return_value = True

        with patch("os.listdir", return_value=["hotkey.py"]), \
             patch("os.path.isfile", return_value=True), \
             patch("rubberstamps.SourceFileLoader") as mock_loader_class, \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            
            mock_loader_instance = mock_loader_class.return_value
            mock_loader_instance.load_module.return_value = mock_module
            
            with pytest.raises(SystemExit):
                execute(config, None, {
                    "video_capture": None,
                    "face_detector": None,
                    "pose_predictor": None,
                    "clahe": None
                })
            mock_exit.assert_called_once_with(0)


# ── hotkey class ────────────────────────────────────────────────────

class TestHotkey:
    def _make_hotkey(self, timeout=1.0, failsafe=True):
        h = hotkey()
        h.config = configparser.ConfigParser()
        h.config.add_section("debug")
        h.config.set("debug", "verbose_stamps", "False")
        h.gtk_proc = None
        h.opencv = {}
        h.options = {"timeout": timeout, "failsafe": failsafe}
        h.declare_config()
        return h

    def test_declare_config(self):
        h = self._make_hotkey()
        assert h.options["abort_key"] == "esc"
        assert h.options["confirm_key"] == "enter"

    def test_on_key(self):
        h = self._make_hotkey()
        h.on_key("abort")
        assert h.pressed_key == "abort"
        h.on_key("confirm")
        assert h.pressed_key == "confirm"

    def test_run_abort(self):
        h = self._make_hotkey(timeout=0.3, failsafe=True)
        import keyboard
        keyboard.add_hotkey = MagicMock()

        def fake_sleep(t):
            h.pressed_key = "abort"

        with patch("time.sleep", side_effect=fake_sleep):
            result = h.run()
            assert result is False

    def test_run_confirm(self):
        h = self._make_hotkey(timeout=0.3, failsafe=True)
        import keyboard
        keyboard.add_hotkey = MagicMock()

        def fake_sleep(t):
            h.pressed_key = "confirm"

        with patch("time.sleep", side_effect=fake_sleep):
            result = h.run()
            assert result is True

    def test_run_timeout_failsafe(self):
        h = self._make_hotkey(timeout=0.05, failsafe=True)
        import keyboard
        keyboard.add_hotkey = MagicMock()

        with patch("time.sleep"):
            result = h.run()
            assert result is False

    def test_run_timeout_faildeadly(self):
        h = self._make_hotkey(timeout=0.05, failsafe=False)
        import keyboard
        keyboard.add_hotkey = MagicMock()

        with patch("time.sleep"):
            result = h.run()
            assert result is True

    def test_run_import_keyboard_exception(self):
        h = self._make_hotkey()
        def mock_import(name, *args, **kwargs):
            if name == "keyboard":
                raise ImportError("mock error")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import), \
             patch("sys.exit", side_effect=SystemExit) as mock_exit, \
             pytest.raises(SystemExit):
            h.run()
        mock_exit.assert_called_once_with(1)


# ── nod class ───────────────────────────────────────────────────────

class TestNod:
    def _make_nod(self, timeout=1.0, failsafe=True):
        n = nod()
        n.config = configparser.ConfigParser()
        n.config.add_section("debug")
        n.config.set("debug", "verbose_stamps", "False")
        n.gtk_proc = None
        n.options = {"timeout": timeout, "failsafe": failsafe}
        n.declare_config()
        n.video_capture = MagicMock()
        n.face_detector = MagicMock()
        n.pose_predictor = MagicMock()
        n.clahe = MagicMock()
        return n

    def test_declare_config(self):
        n = self._make_nod()
        assert n.options["min_distance"] == 6
        assert n.options["min_directions"] == 2

    def test_run_timeout_failsafe(self):
        n = self._make_nod(timeout=0.05, failsafe=True)
        n.video_capture.read_frame.return_value = (MagicMock(), MagicMock())
        n.face_detector.return_value = []
        
        result = n.run()
        assert result is False

    def test_run_timeout_faildeadly(self):
        n = self._make_nod(timeout=0.05, failsafe=False)
        n.video_capture.read_frame.return_value = (MagicMock(), MagicMock())
        n.face_detector.return_value = []
        
        result = n.run()
        assert result is True

    def test_run_no_face(self):
        n = self._make_nod(timeout=0.1, failsafe=True)
        n.video_capture.read_frame.return_value = (MagicMock(), MagicMock())
        n.clahe.apply.return_value = MagicMock()
        n.face_detector.return_value = []  # No faces detected
        
        result = n.run()
        assert result is False

    def test_run_multiple_faces(self):
        n = self._make_nod(timeout=0.1, failsafe=True)
        n.video_capture.read_frame.return_value = (MagicMock(), MagicMock())
        n.clahe.apply.return_value = MagicMock()
        n.face_detector.return_value = [MagicMock(), MagicMock()]  # Two faces
        
        result = n.run()
        assert result is False

    def test_run_nod_yes(self):
        n = self._make_nod(timeout=2.0, failsafe=True)
        n.video_capture.read_frame.return_value = (True, MagicMock())
        n.face_detector.return_value = ["face_loc"]
        
        def make_landmarks(nose_x, nose_y):
            landmarks = MagicMock()
            part0 = MagicMock()
            part0.x = 200
            part2 = MagicMock()
            part2.x = 100
            part4 = MagicMock()
            part4.x = nose_x
            part4.y = nose_y
            
            def part_side_effect(idx):
                if idx == 0:
                    return part0
                elif idx == 2:
                    return part2
                elif idx == 4:
                    return part4
                return MagicMock()
            
            landmarks.part.side_effect = part_side_effect
            return landmarks
        
        landmarks_list = [
            make_landmarks(150, 150),
            make_landmarks(150, 160),
            make_landmarks(150, 140),
        ]
        n.pose_predictor.side_effect = landmarks_list
        n.set_ui_text = MagicMock()
        
        with patch("time.sleep"):
            result = n.run()
            assert result is True
            n.set_ui_text.assert_any_call("Confirmed authentication", n.UI_TEXT)

    def test_run_shake_no(self):
        n = self._make_nod(timeout=2.0, failsafe=True)
        n.video_capture.read_frame.return_value = (True, MagicMock())
        n.face_detector.return_value = ["face_loc"]
        
        def make_landmarks(nose_x, nose_y):
            landmarks = MagicMock()
            part0 = MagicMock()
            part0.x = 200
            part2 = MagicMock()
            part2.x = 100
            part4 = MagicMock()
            part4.x = nose_x
            part4.y = nose_y
            
            def part_side_effect(idx):
                if idx == 0:
                    return part0
                elif idx == 2:
                    return part2
                elif idx == 4:
                    return part4
                return MagicMock()
            
            landmarks.part.side_effect = part_side_effect
            return landmarks
        
        landmarks_list = [
            make_landmarks(150, 150),
            make_landmarks(160, 150),
            make_landmarks(140, 150),
        ]
        n.pose_predictor.side_effect = landmarks_list
        n.set_ui_text = MagicMock()
        
        with patch("time.sleep"):
            result = n.run()
            assert result is False
            n.set_ui_text.assert_any_call("Aborted authentication", n.UI_TEXT)

    def test_run_same_direction_nods(self):
        n = self._make_nod(timeout=2.0, failsafe=True)
        n.options["min_directions"] = 2
        n.options["min_distance"] = 6
        n.video_capture.read_frame.return_value = (True, MagicMock())
        n.face_detector.return_value = ["face_loc"]
        
        def make_landmarks(nose_x, nose_y):
            landmarks = MagicMock()
            part0 = MagicMock()
            part0.x = 200
            part2 = MagicMock()
            part2.x = 100
            part4 = MagicMock()
            part4.x = nose_x
            part4.y = nose_y
            
            def part_side_effect(idx):
                if idx == 0:
                    return part0
                elif idx == 2:
                    return part2
                elif idx == 4:
                    return part4
                return MagicMock()
            
            landmarks.part.side_effect = part_side_effect
            return landmarks
        
        landmarks_list = [
            make_landmarks(150, 150),
            make_landmarks(150, 160),
            make_landmarks(150, 170),
            make_landmarks(150, 150),
        ]
        n.pose_predictor.side_effect = landmarks_list
        
        with patch("time.sleep"):
            result = n.run()
            assert result is True
