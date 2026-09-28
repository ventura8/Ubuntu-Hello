"""Regression: installed GTK app must import wallet_backend from core package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "ubuntu-hello-gtk" / "src" / "init.py"


def _load_ensure_fn():
	source = INIT.read_text(encoding="utf-8")
	ns: dict = {"os": os, "sys": sys}
	start = source.index("def _ensure_ubuntu_hello_on_path")
	end = source.index("\n_ensure_ubuntu_hello_on_path()")
	exec(compile(source[start:end], str(INIT), "exec"), ns)
	return ns["_ensure_ubuntu_hello_on_path"]


def test_ensure_ubuntu_hello_on_path_finds_sibling_wallet_backend(tmp_path):
	gtk_dir = tmp_path / "ubuntu-hello-gtk"
	core_dir = tmp_path / "ubuntu-hello"
	gtk_dir.mkdir()
	core_dir.mkdir()
	(core_dir / "wallet_backend.py").write_text("# stub\n", encoding="utf-8")

	ensure = _load_ensure_fn()
	before = list(sys.path)
	try:
		ensure(here=str(gtk_dir / "init.py"))
		assert str(core_dir) in sys.path
	finally:
		sys.path[:] = before


def test_ensure_ubuntu_hello_on_path_scans_lib_root(tmp_path):
	lib_root = tmp_path / "lib"
	gtk_dir = lib_root / "ubuntu-hello-gtk"
	triplet = lib_root / "x86_64-linux-gnu" / "ubuntu-hello"
	gtk_dir.mkdir(parents=True)
	triplet.mkdir(parents=True)
	(triplet / "wallet_backend.py").write_text("# stub\n", encoding="utf-8")

	ensure = _load_ensure_fn()
	before = list(sys.path)
	try:
		ensure(here=str(gtk_dir / "init.py"))
		assert str(triplet) in sys.path
	finally:
		sys.path[:] = before


def test_ensure_ubuntu_hello_on_path_noop_when_missing(tmp_path):
	gtk_dir = tmp_path / "ubuntu-hello-gtk"
	gtk_dir.mkdir()
	ensure = _load_ensure_fn()
	before = list(sys.path)
	try:
		ensure(here=str(gtk_dir / "init.py"))
		assert sys.path == before or all(
			"ubuntu-hello" not in p or not Path(p).exists() for p in sys.path
		)
	finally:
		sys.path[:] = before


def _run_init(monkeypatch, argv, *, imported, blocked, isfile=None):
	"""Execute init.py as the launcher does, with the heavy UI modules stubbed.

	*imported* is replaced by an empty stub module; *blocked* is set to ``None`` in
	``sys.modules`` so importing it would raise -- proving which UI init.py chose.
	The real /usr/lib install candidates are hidden unless *isfile* says otherwise,
	so the result does not depend on whether Ubuntu Hello is installed here.
	"""
	import runpy
	import types
	from unittest.mock import MagicMock

	stub = types.ModuleType(imported)
	monkeypatch.setitem(sys.modules, imported, stub)
	monkeypatch.setitem(sys.modules, blocked, None)
	glib = MagicMock()
	monkeypatch.setattr(sys.modules["gi.repository"], "GLib", glib)
	real_isfile = os.path.isfile
	monkeypatch.setattr(os.path, "isfile", isfile or (
		lambda p: False if str(p).endswith("wallet_backend.py") else real_isfile(p)))
	monkeypatch.setattr(sys, "argv", argv)
	monkeypatch.setattr(sys, "path", list(sys.path))
	ns = runpy.run_path(str(INIT), run_name="init")
	return ns, stub, glib


def test_init_opens_the_settings_window_by_default(monkeypatch):
	ns, stub, glib = _run_init(monkeypatch, ["ubuntu-hello-gtk"], imported="window", blocked="authsticky")
	assert ns["window"] is stub
	assert "authsticky" not in ns
	glib.set_prgname.assert_called_once_with("ubuntu-hello-gtk")
	glib.set_application_name.assert_called_once_with("Ubuntu Hello")
	assert ns["_I18N_DOMAIN"] == sys.modules["i18n"].DOMAIN


def test_init_opens_the_auth_overlay_when_asked(monkeypatch):
	ns, stub, glib = _run_init(monkeypatch, ["ubuntu-hello-gtk", "--start-auth-ui"],
	                           imported="authsticky", blocked="window")
	assert ns["authsticky"] is stub
	assert "window" not in ns
	glib.set_prgname.assert_called_once_with("ubuntu-hello-gtk")


def test_init_falls_back_to_the_system_install_path(monkeypatch):
	"""The packaged launcher finds wallet_backend under /usr/lib/ubuntu-hello."""
	target = "/usr/lib/ubuntu-hello"
	def has_core(p):
		return p == os.path.join(target, "wallet_backend.py")

	_run_init(monkeypatch, ["ubuntu-hello-gtk"], imported="window", blocked="authsticky", isfile=has_core)
	assert sys.path[-1] == target
	# A second start finds it already on the path and does not append it twice
	# (_run_init hands each run a monkeypatched copy of the current sys.path).
	_run_init(monkeypatch, ["ubuntu-hello-gtk"], imported="window", blocked="authsticky", isfile=has_core)
	assert [entry for entry in sys.path if entry == target] == [target]


def test_init_leaves_sys_path_alone_without_any_core_install(monkeypatch):
	before = list(sys.path)
	_run_init(monkeypatch, ["ubuntu-hello-gtk"], imported="window", blocked="authsticky")
	assert sys.path == before


def _loaded_ensure_fn(monkeypatch):
	"""The function as init.py defines it (its real line numbers, for coverage)."""
	ns, _, _ = _run_init(monkeypatch, ["ubuntu-hello-gtk"], imported="window", blocked="authsticky")
	monkeypatch.undo()
	return ns["_ensure_ubuntu_hello_on_path"]


def test_loaded_ensure_scans_multiarch_lib_root(tmp_path, monkeypatch):
	lib_root = tmp_path / "lib"
	gtk_dir = lib_root / "ubuntu-hello-gtk"
	triplet = lib_root / "aarch64-linux-gnu" / "ubuntu-hello"
	gtk_dir.mkdir(parents=True)
	triplet.mkdir(parents=True)
	(triplet / "wallet_backend.py").write_text("# stub\n", encoding="utf-8")

	ensure = _loaded_ensure_fn(monkeypatch)
	monkeypatch.setattr(sys, "path", list(sys.path))
	ensure(here=str(gtk_dir / "init.py"))
	assert sys.path[-1] == str(triplet)


def test_ensure_ubuntu_hello_on_path_survives_unreadable_lib_root(tmp_path, monkeypatch):
	gtk_dir = tmp_path / "ubuntu-hello-gtk"
	gtk_dir.mkdir()
	ensure = _loaded_ensure_fn(monkeypatch)

	def denied(path):
		raise PermissionError(path)

	monkeypatch.setattr(os, "listdir", denied)
	monkeypatch.setattr(sys, "path", list(sys.path))
	before = list(sys.path)
	ensure(here=str(gtk_dir / "init.py"))
	assert sys.path == before
