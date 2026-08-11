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
