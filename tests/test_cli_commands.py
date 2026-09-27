"""Tests for the small `ubuntu-hello` subcommands: clear, config, disable, remove, snapshot.

Each subcommand is a script whose top-level code runs on import (cli.py does
`import cli.<name>`), so every test sets the builtins cli.py would set, points
the mocked `paths` module at tmp_path and imports the module fresh.
"""
import builtins
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "ubuntu-hello" / "src"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
	"""Core paths_factory on tmp_path + helpers to set args and run a subcommand."""
	# ubuntu-hello-gtk/src also has a paths_factory (without the model paths);
	# load the core one explicitly, as tests/test_cli.py does.
	spec = importlib.util.spec_from_file_location("paths_factory", SRC_DIR / "paths_factory.py")
	pf = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(pf)
	monkeypatch.setitem(sys.modules, "paths_factory", pf)

	etc = tmp_path / "etc"
	etc.mkdir()
	monkeypatch.setattr(pf.paths, "config_dir", etc)
	monkeypatch.setattr(pf.paths, "user_models_dir", etc / "models")
	monkeypatch.setattr(pf.paths, "log_path", tmp_path / "log")

	def set_args(arguments=(), y=False, plain=False, user="alice"):
		monkeypatch.setattr(builtins, "ubuntu_hello_user", user, raising=False)
		monkeypatch.setattr(
			builtins, "ubuntu_hello_args",
			SimpleNamespace(arguments=list(arguments), y=y, plain=plain, user=user),
			raising=False)

	imported = []

	def run(name):
		sys.modules.pop("cli." + name, None)
		imported.append("cli." + name)
		return importlib.import_module("cli." + name)

	set_args()
	yield SimpleNamespace(tmp=tmp_path, etc=etc, models=etc / "models",
	                      config=etc / "config.ini", set_args=set_args, run=run)
	for key in imported:
		sys.modules.pop(key, None)


def write_models(env, user, models):
	env.models.mkdir(exist_ok=True)
	path = env.models / f"{user}.dat"
	path.write_text(json.dumps(models))
	return path


# ── clear ────────────────────────────────────────────────────────────────


def test_clear_without_models_dir_exits(cli_env, capsys):
	with pytest.raises(SystemExit) as exc:
		cli_env.run("clear")
	assert exc.value.code == 1
	assert "No models created yet" in capsys.readouterr().out


def test_clear_user_without_model_file_exits(cli_env, capsys):
	cli_env.models.mkdir()
	with pytest.raises(SystemExit) as exc:
		cli_env.run("clear")
	assert exc.value.code == 1
	assert "alice has no models or they have been cleared already" in capsys.readouterr().out


def test_clear_with_y_deletes_model_file(cli_env, capsys):
	path = write_models(cli_env, "alice", [{"id": 0, "label": "a", "data": []}])
	other = write_models(cli_env, "bob", [{"id": 0, "label": "b", "data": []}])
	cli_env.set_args(y=True)
	cli_env.run("clear")
	assert not path.exists()
	assert other.exists()
	assert "Models cleared" in capsys.readouterr().out


def test_clear_confirmed_interactively(cli_env, monkeypatch, capsys):
	path = write_models(cli_env, "alice", [])
	prompts = []
	monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "Y")
	cli_env.run("clear")
	assert not path.exists()
	assert prompts == ["Do you want to continue [y/N]: "]
	assert "This will clear all models for alice" in capsys.readouterr().out


def test_clear_declined_keeps_file(cli_env, monkeypatch, capsys):
	path = write_models(cli_env, "alice", [])
	monkeypatch.setattr("builtins.input", lambda prompt: "")
	with pytest.raises(SystemExit) as exc:
		cli_env.run("clear")
	assert exc.value.code == 1
	assert path.exists()
	assert 'Interpreting as a "NO", aborting' in capsys.readouterr().out


# ── remove ───────────────────────────────────────────────────────────────


def test_remove_without_argument_prints_usage(cli_env, capsys):
	with pytest.raises(SystemExit) as exc:
		cli_env.run("remove")
	assert exc.value.code == 1
	out = capsys.readouterr().out
	assert "Please add the ID of the model" in out
	assert "ubuntu-hello remove 0" in out
	assert "ubuntu-hello list" in out


def test_remove_without_models_dir_exits(cli_env, capsys):
	cli_env.set_args(arguments=["0"])
	with pytest.raises(SystemExit) as exc:
		cli_env.run("remove")
	assert exc.value.code == 1
	assert "Face models have not been initialized yet" in capsys.readouterr().out


def test_remove_user_without_model_file_exits(cli_env, capsys):
	cli_env.models.mkdir()
	cli_env.set_args(arguments=["0"])
	with pytest.raises(SystemExit) as exc:
		cli_env.run("remove")
	assert exc.value.code == 1
	assert "No face model known for the user alice" in capsys.readouterr().out


def test_remove_unknown_id_exits_and_keeps_models(cli_env, capsys):
	models = [{"id": 0, "label": "a", "data": []}]
	path = write_models(cli_env, "alice", models)
	cli_env.set_args(arguments=["7"], y=True)
	with pytest.raises(SystemExit) as exc:
		cli_env.run("remove")
	assert exc.value.code == 1
	assert "No model with ID 7 exists for alice" in capsys.readouterr().out
	assert json.loads(path.read_text()) == models


def test_remove_last_model_deletes_file(cli_env, capsys):
	path = write_models(cli_env, "alice", [{"id": 3, "label": "only", "data": []}])
	cli_env.set_args(arguments=["3"], y=True)
	cli_env.run("remove")
	assert not path.exists()
	assert "Removed last model, ubuntu-hello disabled for user" in capsys.readouterr().out


def test_remove_one_of_many_rewrites_file(cli_env, monkeypatch, capsys):
	models = [
		{"id": 0, "label": "first", "data": [[0.1]]},
		{"id": 2, "label": "second", "data": [[0.2]]},
		{"id": 5, "label": "third", "data": [[0.3]]},
	]
	path = write_models(cli_env, "alice", models)
	cli_env.set_args(arguments=["2"])
	monkeypatch.setattr("builtins.input", lambda prompt: "y")
	cli_env.run("remove")
	assert json.loads(path.read_text()) == [models[0], models[2]]
	out = capsys.readouterr().out
	assert 'This will remove the model called "second" for alice' in out
	assert "Removed model 2" in out


def test_remove_declined_keeps_models(cli_env, monkeypatch, capsys):
	models = [{"id": 0, "label": "a", "data": []}, {"id": 1, "label": "b", "data": []}]
	path = write_models(cli_env, "alice", models)
	cli_env.set_args(arguments=["1"])
	monkeypatch.setattr("builtins.input", lambda prompt: "n")
	with pytest.raises(SystemExit) as exc:
		cli_env.run("remove")
	assert exc.value.code == 1
	assert json.loads(path.read_text()) == models
	assert 'Interpreting as a "NO", aborting' in capsys.readouterr().out


# ── disable ──────────────────────────────────────────────────────────────


CONFIG_TEMPLATE = "[core]\n# Disable Ubuntu Hello\ndisabled = {value}\nuse_cnn = false\n"


def test_disable_without_argument_exits(cli_env, capsys):
	cli_env.config.write_text(CONFIG_TEMPLATE.format(value="false"))
	with pytest.raises(SystemExit) as exc:
		cli_env.run("disable")
	assert exc.value.code == 1
	assert "Please add a 0 (enable) or a 1 (disable)" in capsys.readouterr().out


def test_disable_invalid_argument_exits(cli_env, capsys):
	cli_env.config.write_text(CONFIG_TEMPLATE.format(value="false"))
	cli_env.set_args(arguments=["maybe"])
	with pytest.raises(SystemExit) as exc:
		cli_env.run("disable")
	assert exc.value.code == 1
	assert "Please only use 0 (enable) or 1 (disable)" in capsys.readouterr().out
	assert cli_env.config.read_text() == CONFIG_TEMPLATE.format(value="false")


@pytest.mark.parametrize("argument", ["1", "true", "TRUE"])
def test_disable_sets_disabled_true(cli_env, capsys, argument):
	cli_env.config.write_text(CONFIG_TEMPLATE.format(value="false"))
	cli_env.set_args(arguments=[argument])
	cli_env.run("disable")
	assert cli_env.config.read_text() == CONFIG_TEMPLATE.format(value="true")
	assert "Ubuntu Hello has been disabled" in capsys.readouterr().out


@pytest.mark.parametrize("argument", ["0", "False"])
def test_disable_sets_disabled_false(cli_env, capsys, argument):
	cli_env.config.write_text(CONFIG_TEMPLATE.format(value="true"))
	cli_env.set_args(arguments=[argument])
	cli_env.run("disable")
	assert cli_env.config.read_text() == CONFIG_TEMPLATE.format(value="false")
	assert "Ubuntu Hello has been enabled" in capsys.readouterr().out


def test_disable_already_in_requested_state_exits(cli_env, capsys):
	cli_env.config.write_text(CONFIG_TEMPLATE.format(value="true"))
	cli_env.set_args(arguments=["1"])
	with pytest.raises(SystemExit) as exc:
		cli_env.run("disable")
	assert exc.value.code == 1
	assert "The disable option has already been set to true" in capsys.readouterr().out
	assert cli_env.config.read_text() == CONFIG_TEMPLATE.format(value="true")


def test_disable_adds_missing_key_instead_of_wiping_the_config(cli_env, capsys):
	"""Regression: a config without core.disabled used to end up EMPTY (TypeError
	mid fileinput inplace rewrite, after the backup was already gone)."""
	original = "[core]\n# keep me\nuse_cnn = false\n\n[video]\ncertainty = 3.5\n"
	cli_env.config.write_text(original)
	cli_env.set_args(arguments=["1"])
	cli_env.run("disable")
	text = cli_env.config.read_text()
	assert text == "[core]\n# keep me\nuse_cnn = false\ndisabled = true\n\n[video]\ncertainty = 3.5\n"
	assert "Ubuntu Hello has been disabled" in capsys.readouterr().out


def test_disable_missing_key_already_means_enabled(cli_env, capsys):
	"""Like the PAM module, a missing core.disabled is false: enabling is a no-op."""
	original = "[core]\nuse_cnn = false\n"
	cli_env.config.write_text(original)
	cli_env.set_args(arguments=["0"])
	with pytest.raises(SystemExit) as exc:
		cli_env.run("disable")
	assert exc.value.code == 1
	assert "already been set to false" in capsys.readouterr().out
	assert cli_env.config.read_text() == original


@pytest.mark.parametrize("line", ["disabled=false", "disabled   =   false", "disabled = False"])
def test_disable_updates_any_spelling_of_the_key(cli_env, capsys, line):
	"""Regression: only the literal "disabled = <value>" was replaced, so other
	spellings were left untouched while the command still reported success."""
	cli_env.config.write_text("[core]\n%s\n\n[rubberstamps]\nenabled = false\n" % line)
	cli_env.set_args(arguments=["1"])
	cli_env.run("disable")
	text = cli_env.config.read_text()
	assert "disabled = true\n" in text
	assert "false" not in text.split("[rubberstamps]")[0]
	assert text.endswith("[rubberstamps]\nenabled = false\n"), "other sections untouched"
	assert "Ubuntu Hello has been disabled" in capsys.readouterr().out


def test_disable_failed_write_keeps_the_old_config(cli_env, monkeypatch):
	"""The write is atomic: when it fails the previous config survives intact."""
	original = CONFIG_TEMPLATE.format(value="false")
	cli_env.config.write_text(original)
	cli_env.set_args(arguments=["1"])

	def fail_replace(*_args):
		raise OSError("disk full")

	monkeypatch.setattr("config_edit.os.replace", fail_replace)
	with pytest.raises(OSError):
		cli_env.run("disable")
	assert cli_env.config.read_text() == original
	assert sorted(p.name for p in cli_env.config.parent.iterdir() if p.name.startswith(".config.")) == []


# ── config ───────────────────────────────────────────────────────────────


@pytest.fixture
def editor_env(cli_env, monkeypatch):
	"""Controls shutil.which / subprocess.call as cli.config sees them."""
	available = {}
	calls = []
	monkeypatch.setattr("shutil.which", lambda name: available.get(name))
	monkeypatch.setattr("subprocess.call", lambda argv: calls.append(argv) or 0)
	monkeypatch.delenv("EDITOR", raising=False)
	return SimpleNamespace(available=available, calls=calls, env=cli_env)


def test_config_uses_preferred_editor(editor_env, monkeypatch, capsys):
	monkeypatch.setenv("EDITOR", "micro")
	editor_env.available.update({"micro": "micro", "nano": "/usr/bin/nano"})
	editor_env.env.run("config")
	assert editor_env.calls == [["micro", str(editor_env.env.config)]]
	assert "Opening config.ini in micro" in capsys.readouterr().out


def test_config_falls_back_to_nano_when_editor_missing(editor_env, monkeypatch, capsys):
	monkeypatch.setenv("EDITOR", "not-installed")
	editor_env.available.update({"nano": "/usr/bin/nano", "vi": "/usr/bin/vi"})
	editor_env.env.run("config")
	assert editor_env.calls == [["/usr/bin/nano", str(editor_env.env.config)]]
	assert "Opening config.ini in nano" in capsys.readouterr().out


def test_config_falls_back_to_vi(editor_env, capsys):
	editor_env.available["vi"] = "/usr/bin/vi"
	editor_env.env.run("config")
	assert editor_env.calls == [["/usr/bin/vi", str(editor_env.env.config)]]
	assert "Opening config.ini in vi" in capsys.readouterr().out


def test_config_reports_editor_failure(editor_env, monkeypatch, capsys):
	editor_env.available["nano"] = "/usr/bin/nano"

	def boom(argv):
		raise OSError("exec failed")

	monkeypatch.setattr("subprocess.call", boom)
	editor_env.env.run("config")
	assert "Failed to open editor: exec failed" in capsys.readouterr().out


def test_config_without_any_editor_explains(editor_env, capsys):
	editor_env.env.run("config")
	assert editor_env.calls == []
	out = capsys.readouterr().out
	assert "Could not find a suitable text editor" in out
	assert "sudo -E ubuntu-hello config" in out


# ── snapshot ─────────────────────────────────────────────────────────────


def test_snapshot_captures_four_frames_and_reports_file(cli_env, monkeypatch, capsys):
	import snapshot
	from recorders import video_capture

	cli_env.config.write_text("[video]\ndark_threshold = 42\ncertainty = 2.5\n")
	frames = [f"frame{i}" for i in range(6)]
	capture = MagicMock()
	capture.read_frame.side_effect = [(f, "gs") for f in frames]
	capture_cls = MagicMock(return_value=capture)
	monkeypatch.setattr(video_capture, "VideoCapture", capture_cls)
	generate = MagicMock(return_value="/var/log/ubuntu-hello/snapshots/snap.jpg")
	monkeypatch.setattr(snapshot, "generate", generate)

	cli_env.run("snap")

	config = capture_cls.call_args.args[0]
	assert config.getfloat("video", "dark_threshold") == 42
	# One warm-up read for the emitters, then four kept frames.
	assert capture.read_frame.call_count == 5
	kept, lines = generate.call_args.args
	assert kept == frames[1:5]
	assert lines[0] == "GENERATED SNAPSHOT"
	assert lines[1].startswith("Date: ") and lines[1].endswith(" UTC")
	assert lines[2] == "Dark threshold config: 42.0"
	assert lines[3] == "Certainty config: 2.5"
	out = capsys.readouterr().out
	assert "Generated snapshot saved as\n/var/log/ubuntu-hello/snapshots/snap.jpg" in out


def test_snapshot_uses_config_defaults(cli_env, monkeypatch):
	import snapshot
	from recorders import video_capture

	capture = MagicMock()
	capture.read_frame.return_value = ("f", "gs")
	monkeypatch.setattr(video_capture, "VideoCapture", MagicMock(return_value=capture))
	generate = MagicMock(return_value="x.jpg")
	monkeypatch.setattr(snapshot, "generate", generate)

	cli_env.run("snap")

	_frames, lines = generate.call_args.args
	assert lines[2] == "Dark threshold config: 60.0"
	assert lines[3] == "Certainty config: 3.5"


def test_models_dir_is_under_tmp(cli_env):
	"""Guard: the fixture must never let a subcommand touch the real /etc."""
	import paths_factory
	assert paths_factory.user_model_path("alice").startswith(str(cli_env.tmp))
	assert paths_factory.config_file_path().startswith(str(cli_env.tmp))
	assert not os.path.exists(cli_env.models)
