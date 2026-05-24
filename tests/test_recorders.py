import sys
import os
from unittest.mock import MagicMock, patch, mock_open, ANY
import pytest
import configparser

# Make sure recorders submodules can be imported
import recorders
from recorders.video_capture import VideoCapture
from recorders.pyv4l2_reader import pyv4l2_reader
from recorders.ffmpeg_reader import ffmpeg_reader

def test_video_capture_init_opencv():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")
    config.set("video", "recording_plugin", "opencv")
    config.set("video", "device_fps", "30")
    config.set("video", "frame_width", "640")
    config.set("video", "frame_height", "480")
    config.set("video", "force_mjpeg", "True")

    with patch("os.path.exists", return_value=True), \
         patch("cv2.VideoCapture") as mock_cv_capture:
        
        mock_cap_instance = mock_cv_capture.return_value
        
        vc = VideoCapture(config)
        
        assert vc.fps == 30
        assert vc.fw == 640
        assert vc.fh == 480
        mock_cv_capture.assert_called_once_with("/dev/video0", ANY)
        mock_cap_instance.set.assert_any_call(ANY, 30)
        mock_cap_instance.set.assert_any_call(ANY, 640)
        mock_cap_instance.set.assert_any_call(ANY, 480)
        mock_cap_instance.grab.assert_called_once()

def test_video_capture_init_ffmpeg():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")
    config.set("video", "recording_plugin", "ffmpeg")

    with patch("os.path.exists", return_value=True), \
         patch("recorders.ffmpeg_reader.ffmpeg_reader.grab"), \
         patch("recorders.ffmpeg_reader.ffmpeg_reader.probe"):
        vc = VideoCapture(config)
        assert isinstance(vc.internal, ffmpeg_reader)

def test_video_capture_init_pyv4l2():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")
    config.set("video", "recording_plugin", "pyv4l2")

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open()), \
         patch("fcntl.ioctl", return_value=0), \
         patch("recorders.pyv4l2_reader.pyv4l2_reader.grab"):
        vc = VideoCapture(config)
        assert isinstance(vc.internal, pyv4l2_reader)

def test_video_capture_init_missing_device():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video99")

    with patch("os.path.exists", return_value=False), \
         patch("sys.exit") as mock_exit:
        vc = VideoCapture(config)
        mock_exit.assert_called_once_with(14)

def test_video_capture_read_frame():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")

    with patch("os.path.exists", return_value=True), \
         patch("cv2.VideoCapture") as mock_cv_capture, \
         patch("cv2.cvtColor") as mock_cvt:
        
        mock_cap = mock_cv_capture.return_value
        mock_cap.read.return_value = (True, "fake_frame")
        mock_cvt.return_value = "fake_gray"
        
        vc = VideoCapture(config)
        frame, gs = vc.read_frame()
        
        assert frame == "fake_frame"
        assert gs == "fake_gray"

def test_video_capture_read_frame_fail():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")

    with patch("os.path.exists", return_value=True), \
         patch("cv2.VideoCapture") as mock_cv_capture, \
         patch("sys.exit") as mock_exit:
        
        mock_cap = mock_cv_capture.return_value
        mock_cap.read.return_value = (False, None)
        
        vc = VideoCapture(config)
        vc.read_frame()
        mock_exit.assert_called_once_with(14)

def test_video_capture_release():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")

    with patch("os.path.exists", return_value=True), \
         patch("cv2.VideoCapture") as mock_cv_capture:
        
        mock_cap = mock_cv_capture.return_value
        vc = VideoCapture(config)
        vc.release()
        mock_cap.release.assert_called()

def test_pyv4l2_reader():
    import numpy as np
    import cv2
    with patch("builtins.open", mock_open()) as mock_file, \
         patch("fcntl.ioctl", return_value=0), \
         patch("numpy.frombuffer") as mock_frombuf, \
         patch("recorders.pyv4l2_reader.Frame") as mock_frame_cls, \
         patch("recorders.pyv4l2_reader.cvtColor") as mock_cvt:
        
        reader = pyv4l2_reader("/dev/video0", "v4l2")
        reader.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        reader.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        assert reader.get(cv2.CAP_PROP_FRAME_WIDTH) == 640
        assert reader.get(cv2.CAP_PROP_FRAME_HEIGHT) == 480

        # Test grab and read
        mock_frame = mock_frame_cls.return_value
        mock_frame.get_frame.return_value = b"raw_data"
        mock_frombuf.return_value = np.zeros((352, 352, 3), dtype=np.uint8)
        
        reader.grab()
        reader.release()

def test_pyv4l2_reader_probe_fallback():
    with patch("builtins.open", mock_open()), \
         patch("fcntl.ioctl", return_value=-1), \
         patch("ffmpeg.probe") as mock_ffmpeg_probe:
        
        mock_ffmpeg_probe.return_value = {
            "streams": [{"height": "480", "width": "640"}]
        }
        
        reader = pyv4l2_reader("/dev/video0", "v4l2")
        assert reader.height == 480
        assert reader.width == 640
        mock_ffmpeg_probe.assert_called_once_with("/dev/video0")

def test_ffmpeg_reader_init():
    """Test ffmpeg_reader initialization."""
    reader = ffmpeg_reader("/dev/video0", "v4l2")
    assert reader.device_path == "/dev/video0"
    assert reader.device_format == "v4l2"
    assert reader.numframes == 10
    assert reader.video == ()
    assert reader.num_frames_read == 0
    assert reader.height == 0
    assert reader.width == 0
    assert reader.init_camera is True

def test_ffmpeg_reader_set_get():
    """Test ffmpeg_reader set/get methods."""
    import cv2
    reader = ffmpeg_reader("/dev/video0", "v4l2")
    reader.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    assert reader.get(cv2.CAP_PROP_FRAME_WIDTH) == 320
    reader.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    assert reader.get(cv2.CAP_PROP_FRAME_HEIGHT) == 240
    # Unknown prop returns None
    assert reader.get(999) is None

def test_ffmpeg_reader_probe():
    """Test ffmpeg_reader probe method."""
    with patch("recorders.ffmpeg_reader.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"", b" 640x480")
        mock_process.poll.return_value = 1
        mock_popen.return_value = mock_process
        
        reader = ffmpeg_reader("/dev/video0", "v4l2")
        reader.probe()
        # In the source code: (height, width) = probe[0].split("x")
        # " 640x480" -> strip -> "640" and "480" -> height=640, width=480
        assert reader.height == 640
        assert reader.width == 480

def test_ffmpeg_reader_release():
    """Test ffmpeg_reader release method."""
    reader = ffmpeg_reader("/dev/video0", "v4l2")
    reader.video = "some_data"
    reader.num_frames_read = 5
    reader.release()
    assert reader.video == ()
    assert reader.num_frames_read == 0

def test_ffmpeg_reader_record():
    """Test ffmpeg_reader record method."""
    import numpy
    with patch("recorders.ffmpeg_reader.Popen") as mock_popen, \
         patch("ffmpeg.input") as mock_input, \
         patch("numpy.frombuffer") as mock_frombuf:
        
        # Setup probe mock
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"", b" 640x480")
        mock_process.poll.return_value = 1
        mock_popen.return_value = mock_process
        
        mock_input.return_value.output.return_value.run.return_value = (b"\x00" * (480 * 640 * 3 * 2), None)
        
        mock_array = MagicMock()
        mock_frombuf.return_value = mock_array
        mock_array.reshape.return_value = mock_array
        
        reader = ffmpeg_reader("/dev/video0", "v4l2")
        reader.set(3, 480)  # CAP_PROP_FRAME_WIDTH
        reader.set(4, 640)  # CAP_PROP_FRAME_HEIGHT
        reader.record(2)
        
        assert reader.num_frames_read == 0
        mock_frombuf.assert_called_once()

def test_ffmpeg_reader_read():
    """Test ffmpeg_reader read method."""
    import numpy
    with patch.object(ffmpeg_reader, 'record') as mock_record, \
         patch.object(ffmpeg_reader, 'probe') as mock_probe:
        
        reader = ffmpeg_reader("/dev/video0", "v4l2")
        
        # First read initializes the camera
        mock_video = MagicMock()
        def set_video(n):
            reader.video = mock_video
        mock_record.side_effect = set_video
        
        ret, frame = reader.read()
        assert ret == 0
        assert reader.init_camera is False

def test_ffmpeg_reader_grab():
    """Test ffmpeg_reader grab redirects to read."""
    with patch.object(ffmpeg_reader, 'read') as mock_read:
        reader = ffmpeg_reader("/dev/video0", "v4l2")
        reader.init_camera = False  # skip init
        reader.grab()
        mock_read.assert_called_once()

def test_ffmpeg_reader_probe_fallback():
    """Test ffmpeg_reader probe method falling back to ffmpeg.probe."""
    with patch("recorders.ffmpeg_reader.Popen") as mock_popen, \
         patch("ffmpeg.probe") as mock_ffmpeg_probe:
        
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.poll.return_value = 0
        mock_popen.return_value = mock_process
        
        mock_ffmpeg_probe.return_value = {
            "streams": [{"height": "480", "width": "640"}]
        }
        
        reader = ffmpeg_reader("/dev/video0", "v4l2")
        reader.probe()
        assert reader.height == 480
        assert reader.width == 640
        mock_ffmpeg_probe.assert_called_once_with("/dev/video0")

def test_ffmpeg_reader_read_various_states():
    """Test ffmpeg_reader read in various states."""
    with patch.object(ffmpeg_reader, 'record') as mock_record, \
         patch.object(ffmpeg_reader, 'probe') as mock_probe:
         
        reader = ffmpeg_reader("/dev/video0", "v4l2")
        reader.init_camera = False
        reader.video = ()
        
        mock_video = MagicMock()
        mock_video.__getitem__.return_value = "frame"
        def set_video(n):
            reader.video = mock_video
        mock_record.side_effect = set_video
        
        ret, frame = reader.read()
        assert frame == "frame"
        
        reader.num_frames_read = 9
        ret, frame = reader.read()
        assert frame == "frame"

def test_video_capture_init_string_config():
    mock_config = MagicMock()
    mock_config.get.return_value = "/dev/video0"
    mock_config.getboolean.return_value = False
    with patch("os.path.exists", return_value=True), \
         patch("cv2.VideoCapture"), \
         patch("configparser.ConfigParser", return_value=mock_config):
        vc = VideoCapture("/mock/config.ini")
        mock_config.read.assert_called_once_with("/mock/config.ini")

def test_video_capture_init_missing_device_no_warn():
    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video99")
    config.set("video", "warn_no_device", "False")

    with patch("os.path.exists", return_value=False), \
         patch("sys.exit") as mock_exit:
        vc = VideoCapture(config)
        mock_exit.assert_called_once_with(14)

def test_video_capture_read_frame_cvt_errors():
    import cv2
    if isinstance(cv2.error, MagicMock):
        class DummyCv2Error(Exception):
            pass
        cv2.error = DummyCv2Error

    config = configparser.ConfigParser()
    config.add_section("video")
    config.set("video", "device_path", "/dev/video0")

    with patch("os.path.exists", return_value=True), \
         patch("cv2.VideoCapture") as mock_cv_capture:
        
        mock_cap = mock_cv_capture.return_value
        mock_cap.read.return_value = (True, "fake_frame")
        
        # 1. RuntimeError test
        with patch("cv2.cvtColor", side_effect=RuntimeError("test")):
            vc = VideoCapture(config)
            frame, gs = vc.read_frame()
            assert frame == "fake_frame"
            assert gs == "fake_frame"
            
        # 2. cv2.error test
        with patch("cv2.cvtColor", side_effect=cv2.error("opencv error")), \
             pytest.raises(cv2.error):
            vc = VideoCapture(config)
            vc.read_frame()
