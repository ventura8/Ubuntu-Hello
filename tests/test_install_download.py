"""Tests for install_config.py and download_models.py."""
import sys
import os
from unittest.mock import patch, mock_open, MagicMock, call
import pytest

from install_config import main as install_config_main
from download_models import main as download_models_main


# ── install_config ──────────────────────────────────────────────────

class TestInstallConfig:
    def test_usage_error(self):
        with patch("sys.argv", ["install_config.py"]):
            with pytest.raises(SystemExit):
                install_config_main()

    def test_dry_run(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {"MESON_INSTALL_DRY_RUN": "1"}):
            # Should return early without doing anything
            install_config_main()

    def test_fresh_install(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs") as mock_mkdirs, \
             patch("os.path.exists", side_effect=lambda p: p != "/etc/uh/config.ini"), \
             patch("shutil.copy") as mock_copy, \
             patch("os.chmod") as mock_chmod, \
             patch("builtins.open", mock_open()):
            install_config_main()
            mock_copy.assert_called_once_with("src.ini", "/etc/uh/config.ini")

    def test_existing_config_with_polkit_migration(self):
        import configparser

        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("os.chmod"), \
             patch("builtins.open", mock_open()), \
             patch("configparser.ConfigParser.read"), \
             patch("configparser.ConfigParser.has_option", return_value=True), \
             patch("configparser.ConfigParser.get", return_value="something, polkit-1"), \
             patch("configparser.ConfigParser.set") as mock_set, \
             patch("configparser.ConfigParser.write"):
            install_config_main()

    def test_destdir(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {"DESTDIR": "/tmp/staging"}, clear=True), \
             patch("os.makedirs") as mock_mkdirs, \
             patch("os.path.exists", return_value=False), \
             patch("shutil.copy") as mock_copy, \
             patch("os.chmod"), \
             patch("builtins.open", mock_open()):
            install_config_main()
            # Should have prepended DESTDIR
            mock_copy.assert_called_once()
            args = mock_copy.call_args[0]
            assert "/tmp/staging" in args[1]

    def test_existing_config_no_ignore_services(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("os.chmod"), \
             patch("builtins.open", mock_open()), \
             patch("configparser.ConfigParser.read"), \
             patch("configparser.ConfigParser.has_option", return_value=False):
            install_config_main()

    def test_polkit_override_oserror(self):
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return  # makedirs for conf_dir
            elif call_count[0] == 2:
                return  # makedirs for override_dir
            raise OSError("test")

        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {"DESTDIR": "/staging"}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("shutil.copy"), \
             patch("os.chmod"), \
             patch("builtins.open", side_effect=OSError("test")):
            # Should handle OSError gracefully for override file
            install_config_main()

    def test_chmod_oserror(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", side_effect=lambda p: p != "/etc/uh/config.ini"), \
             patch("shutil.copy"), \
             patch("os.chmod", side_effect=OSError("Permission denied")), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run"):
            # Should not crash on chmod OSError
            install_config_main()

    def test_config_migration_exception(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("os.chmod"), \
             patch("builtins.open", mock_open()), \
             patch("configparser.ConfigParser.read", side_effect=Exception("Read error")), \
             patch("subprocess.run"):
            # Should print warning but complete
            install_config_main()

    def test_systemd_socket_success(self):
        with patch("sys.argv", ["install_config.py", "src.ini", "/etc/uh"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("shutil.copy"), \
             patch("os.chmod"), \
             patch("builtins.open", mock_open()), \
             patch("subprocess.run") as mock_run:
            install_config_main()
            assert mock_run.call_count == 2
            mock_run.assert_has_calls([
                call(["systemctl", "daemon-reload"], check=True),
                call(["systemctl", "enable", "--now", "polkit-agent-helper.socket"], check=True)
            ])


# ── download_models ─────────────────────────────────────────────────

class TestDownloadModels:
    def test_usage_error(self):
        with patch("sys.argv", ["download_models.py"]):
            with pytest.raises(SystemExit):
                download_models_main()

    def test_dry_run(self):
        with patch("sys.argv", ["download_models.py", "/data"]), \
             patch.dict(os.environ, {"MESON_INSTALL_DRY_RUN": "1"}):
            with pytest.raises(SystemExit) as exc_info:
                download_models_main()
            assert exc_info.value.code == 0

    def test_models_already_exist(self):
        with patch("sys.argv", ["download_models.py", "/data"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            # All models exist -> skip download
            download_models_main()

    def test_download_success(self):
        exists_calls = []
        def exists_side_effect(p):
            exists_calls.append(p)
            # Model files don't exist, but tmp files exist for cleanup
            if p.endswith(".tmp"):
                return True
            if p.endswith(".dat"):
                return False
            return True

        with patch("sys.argv", ["download_models.py", "/data"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", side_effect=exists_side_effect), \
             patch("urllib.request.urlopen") as mock_urlopen, \
             patch("builtins.open", mock_open()), \
             patch("bz2.BZ2File", return_value=mock_open(read_data=b"data")()), \
             patch("shutil.copyfileobj"), \
             patch("os.remove"):
            mock_response = MagicMock()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            download_models_main()

    def test_download_failure(self):
        with patch("sys.argv", ["download_models.py", "/data"]), \
             patch.dict(os.environ, {}, clear=True), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("urllib.request.urlopen", side_effect=Exception("Network error")), \
             patch("os.remove"):
            # Should print warning but not crash
            download_models_main()

    def test_destdir(self):
        with patch("sys.argv", ["download_models.py", "/data"]), \
             patch.dict(os.environ, {"DESTDIR": "/staging"}, clear=True), \
             patch("os.makedirs") as mock_mkdirs, \
             patch("os.path.exists", return_value=True):
            download_models_main()
            mock_mkdirs.assert_called_once_with("/staging/data", exist_ok=True)
