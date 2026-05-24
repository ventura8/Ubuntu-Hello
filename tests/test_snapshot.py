"""Tests for snapshot.py module."""
import os
import sys
import importlib
import types
from pathlib import PurePath
from unittest.mock import patch, MagicMock, mock_open
import pytest


def _load_snapshot_module():
    """Load snapshot.py with the core paths_factory."""
    # Save the original paths_factory
    original_pf = sys.modules.get('paths_factory')
    
    try:
        # Load core paths_factory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths_factory_path = os.path.join(src_dir, "ubuntu-hello", "src", "paths_factory.py")
        snapshot_path = os.path.join(src_dir, "ubuntu-hello", "src", "snapshot.py")
        
        spec = importlib.util.spec_from_file_location(
            "paths_factory",
            paths_factory_path
        )
        core_pf = importlib.util.module_from_spec(spec)
        sys.modules['paths_factory'] = core_pf
        spec.loader.exec_module(core_pf)
        
        # Now load snapshot.py - it will find the core paths_factory in sys.modules
        snapshot_spec = importlib.util.spec_from_file_location(
            "snapshot_core",
            snapshot_path
        )
        snapshot_mod = importlib.util.module_from_spec(snapshot_spec)
        snapshot_spec.loader.exec_module(snapshot_mod)
        return snapshot_mod
    finally:
        # Restore original
        if original_pf is not None:
            sys.modules['paths_factory'] = original_pf


_snapshot_mod = None

def _get_snapshot():
    global _snapshot_mod
    if _snapshot_mod is None:
        _snapshot_mod = _load_snapshot_module()
    return _snapshot_mod


class TestSnapshot:
    def test_generate_empty_frames(self):
        mod = _get_snapshot()
        result = mod.generate([], ["line1"])
        assert result is None

    def test_generate_single_frame(self):
        import numpy as np
        mod = _get_snapshot()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        with patch.object(mod.cv2, "copyMakeBorder", return_value=np.zeros((200, 100, 3), dtype=np.uint8)), \
             patch.object(mod.cv2, "putText"), \
             patch("os.path.exists", return_value=True), \
             patch.object(mod.cv2, "imwrite") as mock_write, \
             patch.object(mod.paths_factory, "snapshots_dir_path", return_value=PurePath("/tmp/snaps")), \
             patch.object(mod.paths_factory, "snapshot_path", return_value="/tmp/snaps/test.jpg"):
            result = mod.generate([frame], ["Line 1", "Line 2"])
            assert result == "/tmp/snaps/test.jpg"
            mock_write.assert_called_once()

    def test_generate_multiple_frames_with_logo(self):
        import numpy as np
        mod = _get_snapshot()
        frame1 = np.zeros((300, 300, 3), dtype=np.uint8)
        frame2 = np.zeros((300, 300, 3), dtype=np.uint8)
        
        with patch.object(mod.cv2, "copyMakeBorder", return_value=np.zeros((400, 600, 3), dtype=np.uint8)), \
             patch.object(mod.cv2, "imread", return_value=np.zeros((57, 180, 3), dtype=np.uint8)), \
             patch.object(mod.cv2, "putText"), \
             patch("os.path.exists", return_value=True), \
             patch.object(mod.cv2, "imwrite") as mock_write, \
             patch.object(mod.paths_factory, "snapshots_dir_path", return_value=PurePath("/tmp/snaps")), \
             patch.object(mod.paths_factory, "snapshot_path", return_value="/tmp/snaps/test.jpg"), \
             patch.object(mod.paths_factory, "logo_path", return_value="/logo.png"):
            result = mod.generate([frame1, frame2], ["Line 1"])
            assert result == "/tmp/snaps/test.jpg"

    def test_generate_creates_dir(self):
        import numpy as np
        mod = _get_snapshot()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        with patch.object(mod.cv2, "copyMakeBorder", return_value=np.zeros((200, 100, 3), dtype=np.uint8)), \
             patch.object(mod.cv2, "putText"), \
             patch("os.path.exists", return_value=False), \
             patch("os.makedirs") as mock_mkdirs, \
             patch.object(mod.cv2, "imwrite"), \
             patch.object(mod.paths_factory, "snapshots_dir_path", return_value=PurePath("/tmp/snaps")), \
             patch.object(mod.paths_factory, "snapshot_path", return_value="/tmp/snaps/test.jpg"):
            mod.generate([frame], ["Line 1"])
            mock_mkdirs.assert_called()
