"""Tests for ubuntu-hello command line interface (cli.py) and subcommands."""
import sys
import os
import importlib
import builtins
import io
import pytest
from unittest.mock import patch, MagicMock

# Keep reference to real open to delegate in tests
real_open = builtins.open

# Ensure cli can be reloaded without side effects
def reload_cli():
    # Clean up cli submodules from sys.modules to force their top-level code to rerun on import
    for key in list(sys.modules.keys()):
        if key.startswith("cli.") or key == "cli_main":
            del sys.modules[key]
            
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Load the core paths_factory explicitly to prevent GTK shadowing
    core_pf_path = os.path.join(src_dir, "ubuntu-hello", "src", "paths_factory.py")
    spec_pf = importlib.util.spec_from_file_location("paths_factory", core_pf_path)
    mod_pf = importlib.util.module_from_spec(spec_pf)
    sys.modules["paths_factory"] = mod_pf
    spec_pf.loader.exec_module(mod_pf)
            
    cli_path = os.path.join(src_dir, "ubuntu-hello", "src", "cli.py")
    spec = importlib.util.spec_from_file_location("cli_main", cli_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_cli_no_args(capsys):
    with patch("sys.argv", ["ubuntu-hello"]):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 0
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "current active user:" in captured
        assert "Command line interface for Ubuntu Hello" in captured

def test_cli_help(capsys):
    with patch("sys.argv", ["ubuntu-hello", "--help"]):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 0
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Command line interface for Ubuntu Hello" in captured

def test_cli_invalid_user(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "invalid@user", "version"]):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Invalid username format" in captured

def test_cli_not_root(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "version"]), \
         patch("os.geteuid", return_value=1000):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Please run this command as root" in captured

def test_cli_root_user(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "root", "version"]), \
         patch("os.geteuid", return_value=0):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Can't run ubuntu-hello commands as root" in captured

def test_cli_version(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "version"]), \
         patch("os.geteuid", return_value=0), \
         patch("os.path.exists", return_value=False):
        reload_cli()
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Ubuntu Hello" in captured

def test_cli_list_no_models(capsys):
    def mock_open_err(file, *args, **kwargs):
        if "testuser.dat" in str(file):
            raise FileNotFoundError()
        return real_open(file, *args, **kwargs)

    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "list"]), \
         patch("os.geteuid", return_value=0), \
         patch("os.path.exists", side_effect=lambda p: "models" in str(p)), \
         patch("builtins.open", side_effect=mock_open_err):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "No face model known for the user testuser" in captured

def test_cli_list_no_models_plain(capsys):
    def mock_open_err(file, *args, **kwargs):
        if "testuser.dat" in str(file):
            raise FileNotFoundError()
        return real_open(file, *args, **kwargs)

    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "--plain", "list"]), \
         patch("os.geteuid", return_value=0), \
         patch("os.path.exists", side_effect=lambda p: "models" in str(p)), \
         patch("builtins.open", side_effect=mock_open_err):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert captured.strip() == ""

def test_cli_list_not_initialized(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "list"]), \
         patch("os.geteuid", return_value=0), \
         patch("os.path.exists", return_value=False):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Face models have not been initialized yet" in captured

def test_cli_list_success(capsys):
    mock_models_json = '[{"id": 1, "time": 1600000000, "label": "test_label"}]'
    def mock_open_ok(file, *args, **kwargs):
        if "testuser.dat" in str(file):
            return io.StringIO(mock_models_json)
        return real_open(file, *args, **kwargs)

    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "list"]), \
         patch("os.geteuid", return_value=0), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=mock_open_ok):
        try:
            reload_cli()
        except SystemExit as e:
            assert e.code == 0
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Known face models for testuser:" in captured
        assert "test_label" in captured

def test_cli_list_success_plain(capsys):
    mock_models_json = '[{"id": 1, "time": 1600000000, "label": "test_label"}]'
    def mock_open_ok(file, *args, **kwargs):
        if "testuser.dat" in str(file):
            return io.StringIO(mock_models_json)
        return real_open(file, *args, **kwargs)

    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "--plain", "list"]), \
         patch("os.geteuid", return_value=0), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=mock_open_ok):
        try:
            reload_cli()
        except SystemExit as e:
            assert e.code == 0
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "1," in captured
        assert "test_label" in captured
        assert "Known face models" not in captured

def test_cli_set_insufficient_args(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "set", "certainty"]), \
         patch("os.geteuid", return_value=0):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Please add a setting you would like to change" in captured

def test_cli_set_not_found(capsys):
    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "set", "non_existent_key", "val"]), \
         patch("os.geteuid", return_value=0), \
         patch("fileinput.input", return_value=[]):
        with pytest.raises(SystemExit) as excinfo:
            reload_cli()
        assert excinfo.value.code == 1
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert 'Could not find a "non_existent_key" config option' in captured

def test_cli_set_success(capsys):
    config_lines = ["certainty = 3.5\n", "other = val\n"]
    with patch("sys.argv", ["ubuntu-hello", "-U", "testuser", "set", "certainty", "4.2"]), \
         patch("os.geteuid", return_value=0), \
         patch("fileinput.input", side_effect=[config_lines, config_lines]) as mock_fileinput:
        try:
            reload_cli()
        except SystemExit as e:
            assert e.code == 0
        captured = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else capsys.readouterr().out
        assert "Config option updated" in captured
