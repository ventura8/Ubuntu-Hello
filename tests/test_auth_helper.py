import ctypes
import os
import sys
from unittest.mock import patch, MagicMock, mock_open
import pytest
import auth_helper

def test_verify_user_password_success():
    mock_libpam = MagicMock()
    mock_libc = MagicMock()
    
    mock_libpam.pam_start.return_value = 0
    mock_libpam.pam_authenticate.return_value = 0
    mock_libpam.pam_end.return_value = 0
    
    real_malloc = ctypes.CDLL(ctypes.util.find_library("c")).malloc
    mock_libc.malloc.side_effect = lambda size: real_malloc(size)
    
    captured_conv = []
    original_byref = ctypes.byref
    def mock_byref(obj, *args, **kwargs):
        if hasattr(obj, "conv"):
            captured_conv.append(obj)
        return original_byref(obj, *args, **kwargs)
        
    with patch("os.path.exists", return_value=True), \
         patch("ctypes.util.find_library", side_effect=lambda name: f"lib{name}.so"), \
         patch("ctypes.CDLL", side_effect=lambda path: mock_libc if "libc.so" in path else mock_libpam), \
         patch("ctypes.byref", side_effect=mock_byref):
         
        res = auth_helper.verify_user_password("testuser", "my_secure_password")
        assert res is True
        assert len(captured_conv) == 1
        
        conv_func = captured_conv[0].conv
        
        # Dynamically extract the PamMessage and PamResponse classes to avoid class identity mismatch
        PamMessage = conv_func.argtypes[1]._type_._type_
        PamResponse = conv_func.argtypes[2]._type_._type_
            
        # Message style 1 (PAM_PROMPT_ECHO_OFF)
        msg1 = PamMessage(msg_style=1, msg=b"Password: ")
        msg1_ptr = ctypes.pointer(msg1)
        msg1_arr = (ctypes.POINTER(PamMessage) * 1)(msg1_ptr)
        msg_p1 = ctypes.cast(msg1_arr, ctypes.POINTER(ctypes.POINTER(PamMessage)))
        resp_p1 = ctypes.pointer(ctypes.POINTER(PamResponse)())
        
        cb_res = conv_func(1, msg_p1, resp_p1, None)
        assert cb_res == 0
        assert resp_p1.contents[0].resp == b"my_secure_password"
        
        # Message style 3 (PAM_TEXT_INFO)
        msg3 = PamMessage(msg_style=3, msg=b"Info: ")
        msg3_ptr = ctypes.pointer(msg3)
        msg3_arr = (ctypes.POINTER(PamMessage) * 1)(msg3_ptr)
        msg_p3 = ctypes.cast(msg3_arr, ctypes.POINTER(ctypes.POINTER(PamMessage)))
        resp_p3 = ctypes.pointer(ctypes.POINTER(PamResponse)())
        
        cb_res3 = conv_func(1, msg_p3, resp_p3, None)
        assert cb_res3 == 0
        assert resp_p3.contents[0].resp is None

def test_verify_user_password_creates_service_file():
    mock_libpam = MagicMock()
    mock_libc = MagicMock()
    mock_libpam.pam_start.return_value = 0
    mock_libpam.pam_authenticate.return_value = 0
    mock_libpam.pam_end.return_value = 0
    
    with patch("os.path.exists", return_value=False), \
         patch("builtins.open", mock_open()) as mock_file, \
         patch("os.chmod") as mock_chmod, \
         patch("ctypes.util.find_library", side_effect=lambda name: f"lib{name}.so"), \
         patch("ctypes.CDLL", side_effect=lambda path: mock_libc if "libc.so" in path else mock_libpam):
         
        res = auth_helper.verify_user_password("testuser", "pass")
        assert res is True
        mock_file.assert_called_once_with("/etc/pam.d/ubuntu-hello-verify", "w")
        mock_chmod.assert_called_once_with("/etc/pam.d/ubuntu-hello-verify", 0o644)

def test_verify_user_password_service_file_exception():
    mock_libpam = MagicMock()
    mock_libc = MagicMock()
    mock_libpam.pam_start.return_value = 0
    mock_libpam.pam_authenticate.return_value = 0
    mock_libpam.pam_end.return_value = 0
    
    with patch("os.path.exists", return_value=False), \
         patch("builtins.open", side_effect=Exception("Permission denied")), \
         patch("ctypes.util.find_library", side_effect=lambda name: f"lib{name}.so"), \
         patch("ctypes.CDLL", side_effect=lambda path: mock_libc if "libc.so" in path else mock_libpam):
         
        res = auth_helper.verify_user_password("testuser", "pass")
        assert res is True

def test_verify_user_password_pam_not_found():
    with patch("os.path.exists", return_value=True), \
         patch("ctypes.util.find_library", return_value=None):
        res = auth_helper.verify_user_password("testuser", "pass")
        assert res is False

def test_verify_user_password_libc_not_found():
    with patch("os.path.exists", return_value=True), \
         patch("ctypes.util.find_library", side_effect=lambda name: None if name == "c" else "libpam.so"):
        res = auth_helper.verify_user_password("testuser", "pass")
        assert res is False

def test_verify_user_password_pam_start_fails():
    mock_libpam = MagicMock()
    mock_libc = MagicMock()
    mock_libpam.pam_start.return_value = 1
    
    with patch("os.path.exists", return_value=True), \
         patch("ctypes.util.find_library", side_effect=lambda name: f"lib{name}.so"), \
         patch("ctypes.CDLL", side_effect=lambda path: mock_libc if "libc.so" in path else mock_libpam):
        res = auth_helper.verify_user_password("testuser", "pass")
        assert res is False

def test_verify_user_password_malloc_fails():
    mock_libpam = MagicMock()
    mock_libc = MagicMock()
    mock_libpam.pam_start.return_value = 0
    mock_libpam.pam_authenticate.return_value = 0
    mock_libpam.pam_end.return_value = 0
    
    captured_conv = []
    original_byref = ctypes.byref
    def mock_byref(obj, *args, **kwargs):
        if hasattr(obj, "conv"):
            captured_conv.append(obj)
        return original_byref(obj, *args, **kwargs)
        
    with patch("os.path.exists", return_value=True), \
         patch("ctypes.util.find_library", side_effect=lambda name: f"lib{name}.so"), \
         patch("ctypes.CDLL", side_effect=lambda path: mock_libc if "libc.so" in path else mock_libpam), \
         patch("ctypes.byref", side_effect=mock_byref):
         
        res = auth_helper.verify_user_password("testuser", "pass")
        assert res is True
        conv_func = captured_conv[0].conv
        
        PamMessage = conv_func.argtypes[1]._type_._type_
        PamResponse = conv_func.argtypes[2]._type_._type_
            
        msg1 = PamMessage(msg_style=1, msg=b"Password: ")
        msg1_ptr = ctypes.pointer(msg1)
        msg1_arr = (ctypes.POINTER(PamMessage) * 1)(msg1_ptr)
        msg_p1 = ctypes.cast(msg1_arr, ctypes.POINTER(ctypes.POINTER(PamMessage)))
        resp_p1 = ctypes.pointer(ctypes.POINTER(PamResponse)())
        
        mock_libc.malloc.return_value = None
        cb_res = conv_func(1, msg_p1, resp_p1, None)
        assert cb_res == 1
        
        real_malloc = ctypes.CDLL(ctypes.util.find_library("c")).malloc
        malloc_calls = 0
        def malloc_side_effect(size):
            nonlocal malloc_calls
            malloc_calls += 1
            if malloc_calls == 1:
                return real_malloc(size)
            return None
            
        mock_libc.malloc.side_effect = malloc_side_effect
        cb_res2 = conv_func(1, msg_p1, resp_p1, None)
        assert cb_res2 == 1
