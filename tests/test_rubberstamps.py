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
        """A failsafe rule whose stamp crashes must ABORT, not authenticate.

        This asserted exit(0) until 2026-09-16: `except Exception: continue` skipped the
        result check and fell through to the success exit, so a crashing liveness check
        let the user in. `failsafe` means the check must pass or authentication fails.
        """
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
            mock_exit.assert_called_once_with(15)

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


class TestLivenessFailsClosedOnCrash:
    """A liveness check that STARTED and then crashed must deny under a failsafe rule.

    `except Exception: continue` skipped the result check below it and fell through to
    sys.exit(0), so a crashing check authenticated the user. Configuration errors
    (unknown stamp, unparseable rule) keep the upstream warn-and-skip behaviour so a
    renamed stamp cannot lock anyone out. Names come from Howdy: `failsafe` aborts when
    the check does not pass, `faildeadly` lets authentication through anyway.
    """

    def _execute(self, rule, run_impl, monkeypatch):
        import rubberstamps
        config = MagicMock()
        config.get.return_value = rule
        config.getboolean.return_value = False

        class Stamp:
            def declare_config(self):
                pass

            run = run_impl

        module = MagicMock()
        module.hotkey = Stamp
        monkeypatch.setattr(rubberstamps, "SourceFileLoader",
                            lambda *a, **k: MagicMock(load_module=lambda: module))
        with pytest.raises(SystemExit) as exit_info:
            rubberstamps.execute(config, None, {"video_capture": MagicMock(), "face_detector": MagicMock(),
                                                "pose_predictor": MagicMock(), "clahe": MagicMock()})
        return exit_info.value.code

    def test_crashing_stamp_denies_when_failsafe(self, monkeypatch):
        def boom(self):
            raise RuntimeError("camera exploded")
        assert self._execute("hotkey 5s failsafe", boom, monkeypatch) == 15

    def test_crashing_stamp_passes_when_faildeadly(self, monkeypatch):
        def boom(self):
            raise RuntimeError("camera exploded")
        assert self._execute("hotkey 5s faildeadly", boom, monkeypatch) == 0

    def test_stamp_returning_false_still_denies(self, monkeypatch):
        assert self._execute("hotkey 5s failsafe", lambda self: False, monkeypatch) == 15


class TestChallengeReachesTheUser:
    """The auth overlay never gets a display (PAM hands compare.py PATH only), so the
    stamp's prompt is mirrored onto the desktop notification card."""

    def _stamp(self, notifier):
        import rubberstamps
        s = rubberstamps.RubberStamp()
        s.config = MagicMock()
        s.config.getboolean.return_value = False
        s.gtk_proc = None
        s.notifier = notifier
        return s

    def test_main_text_and_subtext_are_sent_to_the_card(self):
        notifier = MagicMock()
        s = self._stamp(notifier)
        s.set_ui_text("Nod to confirm", s.UI_TEXT)
        s.set_ui_text("Shake your head to abort", s.UI_SUBTEXT)
        assert notifier.liveness.call_args_list[-1].args == ("Nod to confirm", "Shake your head to abort")

    def test_a_broken_card_never_affects_authentication(self):
        notifier = MagicMock()
        notifier.liveness.side_effect = RuntimeError("bus gone")
        s = self._stamp(notifier)
        s.set_ui_text("Nod to confirm", s.UI_TEXT)   # must not raise

    def test_no_notifier_is_fine(self):
        s = self._stamp(None)
        s.set_ui_text("Nod to confirm", s.UI_TEXT)

    def test_execute_passes_the_notifier_to_the_stamp(self, monkeypatch):
        import rubberstamps
        config = MagicMock()
        config.get.return_value = "hotkey 5s failsafe"
        config.getboolean.return_value = False
        seen = {}

        class Stamp:
            def declare_config(self):
                pass

            def run(self):
                seen["notifier"] = self.notifier
                return True

        module = MagicMock()
        module.hotkey = Stamp
        monkeypatch.setattr(rubberstamps, "SourceFileLoader",
                            lambda *a, **k: MagicMock(load_module=lambda: module))
        notifier = MagicMock()
        with pytest.raises(SystemExit):
            rubberstamps.execute(config, None, {"video_capture": MagicMock(), "face_detector": MagicMock(),
                                                "pose_predictor": MagicMock(), "clahe": MagicMock()}, notifier=notifier)
        assert seen["notifier"] is notifier


class TestDeadOverlayNeverBreaksAuth:
    """compare.py runs with PATH only, so the GTK overlay exits immediately and its pipe
    breaks. That raised out of set_ui_text and the stamp was reported as crashed."""

    def _stamp(self, proc):
        import rubberstamps
        s = rubberstamps.RubberStamp()
        s.config = MagicMock()
        s.config.getboolean.return_value = False
        s.gtk_proc = proc
        s.notifier = None
        return s

    def test_broken_pipe_is_swallowed_and_the_overlay_is_dropped(self):
        proc = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError(32, "Broken pipe")
        s = self._stamp(proc)
        s.set_ui_text("Nod to confirm", s.UI_TEXT)     # must not raise
        assert s.gtk_proc is None, "a dead overlay must not be written to again"
        s.set_ui_text("again", s.UI_TEXT)

    def test_closed_stdin_is_swallowed(self):
        proc = MagicMock()
        proc.stdin.write.side_effect = ValueError("I/O operation on closed file")
        s = self._stamp(proc)
        s.set_ui_text("Nod to confirm", s.UI_TEXT)
        assert s.gtk_proc is None


class TestNodRobustness:
    """Properties the frame-to-frame version did not have.

    Eye distance is 100 px in these fixtures, so `min_distance=12` means the nose must
    travel 12 px from its anchor. The detector only finds a face in roughly a quarter
    of the frames on an IR camera, which is what made a per-frame delta unreliable.
    """

    def _nod(self, positions, timeout=5.0, min_distance=12, faces_per_frame=1):
        n = nod()
        n.config = configparser.ConfigParser()
        n.config.add_section("debug")
        n.config.set("debug", "verbose_stamps", "False")
        n.gtk_proc = None
        n.notifier = None
        n.options = {"timeout": timeout, "failsafe": True}
        n.declare_config()
        n.options["min_distance"] = min_distance
        n.video_capture = MagicMock()
        n.video_capture.read_frame.return_value = (True, MagicMock())
        n.face_detector = MagicMock(return_value=["face"] * faces_per_frame)
        n.clahe = MagicMock()
        n.set_ui_text = MagicMock()

        def landmarks(pos):
            x, y = pos
            lm = MagicMock()
            parts = {0: MagicMock(x=200), 2: MagicMock(x=100), 4: MagicMock(x=x, y=y)}
            lm.part.side_effect = lambda i: parts.get(i, MagicMock())
            return lm

        # Cycle: the mocked loop runs far more iterations than there are positions,
        # and a jitter fixture has to keep jittering for the whole window.
        import itertools
        frames = itertools.cycle([landmarks(p) for p in positions])
        n.pose_predictor = MagicMock(side_effect=lambda *a, **k: next(frames))
        return n

    def test_a_slow_nod_still_registers(self):
        """Each step is only 5 px, under the 12 px bar, but the nose travels 20 px down
        and 20 px back. A per-frame delta never saw this; displacement from an anchor does."""
        down = [(150, 150 + step) for step in range(0, 25, 5)]
        up = [(150, 170 - step) for step in range(0, 45, 5)]
        n = self._nod(down + up)
        with patch("time.sleep"):
            assert n.run() is True

    def test_jitter_below_the_threshold_never_confirms(self):
        """Holding still: the nose wobbles a few pixels around one spot for the whole
        window. This used to accumulate into a 'nod' and authenticate the user."""
        jitter = [(150 + (i % 3) - 1, 150 + (i % 5) - 2) for i in range(60)]
        n = self._nod(jitter, timeout=0.3)
        with patch("time.sleep"):
            assert n.run() is False

    def test_a_nod_with_sideways_drift_is_not_read_as_a_shake(self):
        """Vertical travel dominates, so the confirm axis wins even though the head
        also drifts sideways. Reading it as a shake aborted authentication."""
        positions = [(150, 150), (154, 175), (158, 150), (162, 175)]
        n = self._nod(positions)
        with patch("time.sleep"):
            assert n.run() is True

    def test_a_deliberate_shake_still_aborts(self):
        positions = [(150, 150), (185, 152), (145, 154), (185, 152)]
        n = self._nod(positions)
        with patch("time.sleep"):
            assert n.run() is False

    def test_eye_distance_is_used_as_a_magnitude(self, monkeypatch):
        """A mirrored camera reports the eyes in the other order, making the scale
        negative; the movement then became a huge number and any jitter passed."""
        n = self._nod([(150, 150), (150, 156)])
        lm = MagicMock()
        parts = {0: MagicMock(x=100), 2: MagicMock(x=200), 4: MagicMock(x=150, y=156)}
        lm.part.side_effect = lambda i: parts.get(i, MagicMock())
        n.pose_predictor = MagicMock(return_value=lm)
        n.options["timeout"] = 0.2
        with patch("time.sleep"):
            # 6 px of 100 px eye distance is 6%, under the 12% bar: no confirmation
            assert n.run() is False
