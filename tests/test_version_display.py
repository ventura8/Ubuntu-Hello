"""Tests for GTK user-facing version display (VERSION SSOT, no old git tags)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GTK_SRC = ROOT / "ubuntu-hello-gtk" / "src"
sys.path.insert(0, str(GTK_SRC))

import version_display as vd  # noqa: E402

REPO_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip().splitlines()[0].strip()


def test_display_prefers_version_file_over_legacy_paths_paren(monkeypatch, tmp_path):
    (tmp_path / "VERSION").write_text(f"{REPO_VERSION}\n", encoding="utf-8")
    monkeypatch.setattr(
        vd, "_paths_version", lambda: f"v{REPO_VERSION}-dev (v1.0.4-1-g22bcf89-dirty)"
    )
    monkeypatch.setattr(vd, "_git_exact_release_tag", lambda *_a, **_k: False)
    assert vd.get_display_version(str(tmp_path)) == f"v{REPO_VERSION}-dev"


def test_display_release_when_exact_tag(monkeypatch, tmp_path):
    (tmp_path / "VERSION").write_text(f"{REPO_VERSION}\n", encoding="utf-8")
    monkeypatch.setattr(vd, "_paths_version", lambda: f"v{REPO_VERSION}")
    monkeypatch.setattr(vd, "_git_exact_release_tag", lambda *_a, **_k: True)
    assert vd.get_display_version(str(tmp_path)) == f"v{REPO_VERSION}"


def test_display_from_paths_only_strips_old_tag(monkeypatch, tmp_path):
    # No VERSION file in tmp_path; paths still carries legacy describe paren.
    monkeypatch.setattr(
        vd, "_paths_version", lambda: f"v{REPO_VERSION}-dev (v1.0.4-1-gdeadbee)"
    )
    monkeypatch.setattr(vd, "_git_exact_release_tag", lambda *_a, **_k: None)
    assert vd.get_display_version(str(tmp_path)) == f"v{REPO_VERSION}-dev"


def test_semver_from_any():
    assert vd._semver_from_any(f"v{REPO_VERSION}-dev (v1.0.4-1-gabc)") == REPO_VERSION
    assert vd._semver_from_any("1.0.4") == "1.0.4"
    assert vd._semver_from_any("not-a-version") is None


def test_read_version_file_oserror(monkeypatch, tmp_path):
    version_path = tmp_path / "VERSION"
    version_path.write_text(f"{REPO_VERSION}\n", encoding="utf-8")

    real_open = open

    def boom(path, *args, **kwargs):
        if str(path) == str(version_path):
            raise OSError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", boom)
    assert vd._read_version_file(str(tmp_path)) is None


def test_paths_version_reads_module(monkeypatch):
    class FakePaths:
        version = f"v{REPO_VERSION}"

    monkeypatch.setitem(sys.modules, "paths", FakePaths())
    assert vd._paths_version() == f"v{REPO_VERSION}"


def test_paths_version_ignores_placeholder(monkeypatch):
    class FakePaths:
        version = "@VERSION@"

    monkeypatch.setitem(sys.modules, "paths", FakePaths())
    assert vd._paths_version() is None


def test_paths_version_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "paths", None)
    # Force import failure path
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "paths":
            raise ImportError("no paths")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert vd._paths_version() is None


def test_git_exact_release_tag_true(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        class Result:
            def __init__(self, code, out):
                self.returncode = code
                self.stdout = out

        if "rev-parse" in cmd:
            return Result(0, "true\n")
        return Result(0, f"v{REPO_VERSION}\n")

    monkeypatch.setattr(vd.subprocess, "run", fake_run)
    assert vd._git_exact_release_tag(str(tmp_path), REPO_VERSION) is True


def test_git_exact_release_tag_false_and_unavailable(monkeypatch, tmp_path):
    def not_a_repo(cmd, **_kwargs):
        class Result:
            returncode = 1
            stdout = ""

        return Result()

    monkeypatch.setattr(vd.subprocess, "run", not_a_repo)
    assert vd._git_exact_release_tag(str(tmp_path), REPO_VERSION) is None

    def inside_but_no_tag(cmd, **_kwargs):
        class Result:
            def __init__(self, code, out=""):
                self.returncode = code
                self.stdout = out

        if "rev-parse" in cmd:
            return Result(0, "true\n")
        return Result(1, "")

    monkeypatch.setattr(vd.subprocess, "run", inside_but_no_tag)
    assert vd._git_exact_release_tag(str(tmp_path), REPO_VERSION) is False

    def boom(*_a, **_k):
        raise OSError("git missing")

    monkeypatch.setattr(vd.subprocess, "run", boom)
    assert vd._git_exact_release_tag(str(tmp_path), REPO_VERSION) is None
