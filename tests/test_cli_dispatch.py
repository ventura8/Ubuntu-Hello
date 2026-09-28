"""Tests for the `ubuntu-hello` entry point (ubuntu-hello/src/cli.py): user detection,
config restore, subcommand dispatch and the version fallback.

Complements tests/test_cli.py. Subcommand modules are served by a recording
import hook, so dispatch is observed without running any camera code.
"""
import builtins
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import config_ensure

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI_PATH = PROJECT_ROOT / "ubuntu-hello" / "src" / "cli.py"
PROJECT_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip().splitlines()[0].strip()


class RecordingFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
	"""Serves empty modules for the given names and records which were imported."""

	def __init__(self, names):
		self.names = set(names)
		self.imported = []

	def find_spec(self, fullname, path=None, target=None):
		if fullname in self.names:
			return importlib.machinery.ModuleSpec(fullname, self)
		return None

	def create_module(self, spec):
		return None

	def exec_module(self, module):
		self.imported.append(module.__name__)


SUBCOMMAND_MODULES = ["cli.add", "cli.clear", "cli.config", "cli.disable", "cli.keyring",
                      "cli.list", "cli.remove", "cli.set", "cli.snap", "cli.test"]


@pytest.fixture
def entry(monkeypatch):
	"""Run cli.py as the `ubuntu-hello` script with a given argv, as root by default."""
	old_umask = os.umask(0o022)
	os.umask(old_umask)
	monkeypatch.setattr(builtins, "ubuntu_hello_args", None, raising=False)
	monkeypatch.setattr(builtins, "ubuntu_hello_user", None, raising=False)
	for name in ("SUDO_USER", "DOAS_USER", "PKEXEC_UID"):
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setattr("getpass.getuser", lambda: "alice")
	monkeypatch.setattr("os.geteuid", lambda: 0)
	restored = []
	monkeypatch.setattr(config_ensure, "ensure_system_config", lambda: restored.append(True))

	finder = RecordingFinder(SUBCOMMAND_MODULES)
	# Importing a stub submodule also binds it on the real `cli` package; Python
	# 3.10's patch("cli.keyring.x") resolves through those attributes, so they are
	# put back afterwards along with sys.modules.
	cli_package = importlib.import_module("cli")
	missing = object()
	saved_attrs = {name: getattr(cli_package, name.split(".")[1], missing) for name in SUBCOMMAND_MODULES}
	for name in SUBCOMMAND_MODULES:
		monkeypatch.delitem(sys.modules, name, raising=False)
	monkeypatch.setattr(sys, "meta_path", [finder] + sys.meta_path)

	def run(*argv):
		monkeypatch.setattr(sys, "argv", ["ubuntu-hello", *argv])
		spec = importlib.util.spec_from_file_location("cli_main", CLI_PATH)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module

	yield SimpleNamespace(run=run, finder=finder, restored=restored)
	for name in SUBCOMMAND_MODULES:
		sys.modules.pop(name, None)
		attr = name.split(".")[1]
		if saved_attrs[name] is missing:
			if hasattr(cli_package, attr):
				delattr(cli_package, attr)
		else:
			setattr(cli_package, attr, saved_attrs[name])
	os.umask(old_umask)


@pytest.mark.parametrize("command, module", [
	("add", "cli.add"),
	("clear", "cli.clear"),
	("config", "cli.config"),
	("disable", "cli.disable"),
	("keyring", "cli.keyring"),
	("list", "cli.list"),
	("remove", "cli.remove"),
	("set", "cli.set"),
	("snapshot", "cli.snap"),
	("test", "cli.test"),
])
def test_command_dispatches_to_its_module(entry, command, module):
	entry.run(command, "extra", "-y")
	assert entry.finder.imported == [module]
	assert entry.restored == [True]
	assert builtins.ubuntu_hello_user == "alice"
	assert builtins.ubuntu_hello_args.arguments == ["extra"]
	assert builtins.ubuntu_hello_args.y is True


def test_user_flag_overrides_detected_user(entry):
	entry.run("-U", "bob", "clear")
	assert builtins.ubuntu_hello_user == "bob"


def test_sudo_user_wins_over_login_name(entry, monkeypatch):
	monkeypatch.setenv("SUDO_USER", "carol")
	monkeypatch.setenv("DOAS_USER", "dave")
	entry.run("list")
	assert builtins.ubuntu_hello_user == "carol"


def test_pkexec_uid_is_resolved_to_a_name(entry, monkeypatch, capsys):
	monkeypatch.setenv("PKEXEC_UID", "1234")
	looked_up = []
	monkeypatch.setattr("pwd.getpwuid", lambda uid: looked_up.append(uid) or ("erin", "x", uid))
	with pytest.raises(SystemExit) as exc:
		entry.run()
	assert exc.value.code == 0
	assert looked_up == [1234]
	assert "current active user: erin" in capsys.readouterr().out


def test_undetermined_user_exits(entry, monkeypatch, capsys):
	monkeypatch.setattr("getpass.getuser", lambda: "")
	with pytest.raises(SystemExit) as exc:
		entry.run("list")
	assert exc.value.code == 1
	assert "Could not determine user, please use the --user flag" in capsys.readouterr().out
	assert entry.finder.imported == []


def test_config_restore_failure_exits_before_dispatch(entry, monkeypatch, capsys):
	def fail():
		raise PermissionError("read-only file system")

	monkeypatch.setattr(config_ensure, "ensure_system_config", fail)
	with pytest.raises(SystemExit) as exc:
		entry.run("list")
	assert exc.value.code == 1
	assert "Failed to restore /etc/ubuntu-hello/config.ini: read-only file system" in capsys.readouterr().out
	assert entry.finder.imported == []


def test_version_from_paths_module(entry, monkeypatch, capsys):
	monkeypatch.setattr(sys.modules["paths"], "version", "9.8.7")
	entry.run("version")
	assert capsys.readouterr().out.strip() == "Ubuntu Hello 9.8.7"


def test_unconfigured_version_falls_back_to_version_file(entry, monkeypatch, capsys):
	# A source tree that meson has not configured still has "@VERSION@" in paths.py.
	monkeypatch.setattr(sys.modules["paths"], "version", "@VERSION@")
	entry.run("version")
	assert capsys.readouterr().out.strip() == "Ubuntu Hello " + PROJECT_VERSION


def test_broken_paths_module_falls_back_to_version_file(entry, monkeypatch, capsys):
	class BrokenPaths:
		@property
		def version(self):
			raise RuntimeError("paths.py is corrupt")

	monkeypatch.setitem(sys.modules, "paths", BrokenPaths())
	entry.run("version")
	assert capsys.readouterr().out.strip() == "Ubuntu Hello " + PROJECT_VERSION


def test_missing_version_file_reports_unknown(entry, monkeypatch, capsys):
	monkeypatch.setattr(sys.modules["paths"], "version", "")
	monkeypatch.setattr("os.path.isfile", lambda path: False)
	entry.run("version")
	assert capsys.readouterr().out.strip() == "Ubuntu Hello unknown"


def test_unreadable_version_file_reports_unknown(entry, monkeypatch, capsys):
	monkeypatch.setattr(sys.modules["paths"], "version", "")
	real_open = builtins.open

	def guarded_open(file, *args, **kwargs):
		if str(file).endswith("VERSION"):
			raise PermissionError(file)
		return real_open(file, *args, **kwargs)

	monkeypatch.setattr("builtins.open", guarded_open)
	entry.run("version")
	assert capsys.readouterr().out.strip() == "Ubuntu Hello unknown"
