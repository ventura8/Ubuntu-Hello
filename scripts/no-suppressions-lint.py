#!/usr/bin/env python3
"""Fail if production code or tests contain linter/test suppressions.

Scans Ubuntu Hello sources for NOLINT, shellcheck disable, noqa, type: ignore,
pytest.mark.skip/xfail, and similar silence-the-tool patterns. Docs may mention
these as forbidden; this lint only covers executable production + test code.

Usage:
  python3 scripts/no-suppressions-lint.py
  python3 scripts/no-suppressions-lint.py /path/to/repo
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# (rule_id, compiled pattern, human hint)
# Patterns match intentional suppressions, not prose in markdown/skills.
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "shellcheck-disable",
        re.compile(r"(?i)#\s*shellcheck\s+disable\b"),
        "# shellcheck disable is forbidden — fix the script",
    ),
    (
        "shellcheck-exclude",
        re.compile(r"(?i)\bshellcheck\b[^\n]*\s(-e|--exclude)\b"),
        "shellcheck -e/--exclude is forbidden — fix the script",
    ),
    (
        "clang-tidy-nolint",
        re.compile(r"\bNOLINT(?:NEXTLINE|BEGIN|END)?\b"),
        "NOLINT is forbidden — fix the C++ finding",
    ),
    (
        "python-noqa",
        re.compile(r"(?i)#\s*noqa\b"),
        "# noqa is forbidden — fix the Python finding",
    ),
    (
        "python-type-ignore",
        re.compile(r"(?i)#\s*type:\s*ignore\b"),
        "# type: ignore is forbidden — fix types or imports",
    ),
    (
        "python-pyright-ignore",
        re.compile(r"(?i)#\s*pyright:\s*ignore\b"),
        "# pyright: ignore is forbidden",
    ),
    (
        "python-mypy-ignore",
        re.compile(r"(?i)#\s*mypy:\s*ignore\b"),
        "# mypy: ignore is forbidden",
    ),
    (
        "python-pylint-disable",
        re.compile(r"(?i)#\s*pylint:\s*disable\b"),
        "# pylint: disable is forbidden",
    ),
    (
        "python-ruff-ignore",
        re.compile(r"(?i)#\s*ruff:\s*(?:noqa|ignore)\b"),
        "# ruff: noqa/ignore is forbidden",
    ),
    (
        "python-flake8-ignore",
        re.compile(r"(?i)#\s*flake8:\s*noqa\b"),
        "# flake8: noqa is forbidden",
    ),
    (
        "pytest-mark-skip",
        re.compile(r"@pytest\.mark\.skip(?:if)?\b"),
        "@pytest.mark.skip(if) is forbidden — fix the test or use env pytest.skip()",
    ),
    (
        "pytest-mark-xfail",
        re.compile(r"@pytest\.mark\.xfail\b"),
        "@pytest.mark.xfail is forbidden — fix the failure",
    ),
]

CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".sh",
        ".cc",
        ".cpp",
        ".cxx",
        ".c",
        ".h",
        ".hh",
        ".hpp",
    }
)

# Top-level trees that hold production + test + packaging scripts.
SCAN_ROOTS = (
    "ubuntu-hello",
    "ubuntu-hello-gtk",
    "tests",
    "scripts",
    "packaging",
    "debian",
    "docker",
)

# Repo-root entrypoints (no suffix or .sh).
ROOT_FILES = (
    "install.sh",
    "uninstall.sh",
)

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "artifacts",
        "logs",
        "po",
        "i18n_fill_data",
        ".debhelper",
        # Snapcraft leftovers under packaging/snap/ (and generic parts/).
        "parts",
        "stage",
        "prime",
        "overlay",
        ".craft",
    }
)

# dh staged package trees under debian/ (not source).
DEBIAN_SKIP_DIRS = frozenset(
    {
        "tmp",
        "ubuntu-hello",
        "ubuntu-hello-gtk",
        "files",
    }
)


def skip_path(path: Path) -> bool:
    """True if path should not be scanned (build trees, caches, this linter)."""
    parts = path.parts
    if "i18n_fill_data" in parts:
        return True
    for part in parts:
        if part in SKIP_DIR_NAMES:
            return True
        if part.startswith("build") or part.startswith("obj-"):
            return True
    # The linter documents forbidden tokens in RULES; do not flag itself.
    if path.name == "no-suppressions-lint.py":
        return True
    # Unit tests embed sample suppression strings under tmp_path and in dicts.
    if path.name == "test_no_suppressions_lint.py":
        return True
    return False


def is_scanned_file(path: Path, repo: Path) -> bool:
    if skip_path(path):
        return False
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except (ValueError, OSError):
        return False
    if rel.as_posix() in ROOT_FILES:
        return True
    if not rel.parts:
        return False
    if rel.parts[0] not in SCAN_ROOTS:
        return False
    # Only debian/ packaging *scripts* at the debian/ root (not dh build trees).
    if rel.parts[0] == "debian":
        if len(rel.parts) != 2:
            return False
        if rel.parts[1] in DEBIAN_SKIP_DIRS:
            return False
        name = rel.parts[1]
        if name.endswith((".postinst", ".prerm", ".postrm", ".preinst", ".sh")):
            return True
        return False
    suffix = path.suffix.lower()
    if suffix in CODE_SUFFIXES:
        return True
    # Packaging / docker hooks often have no extension.
    if rel.parts[0] in {"packaging", "docker", "scripts"} and suffix == "":
        name = path.name
        if name in {"configure", "snap-entrypoint.sh"} or name.endswith("-entrypoint.sh"):
            return True
        try:
            if path.is_file() and path.stat().st_mode & 0o111:
                return True
        except OSError:
            return False
    return False


def _walk_onerror(err: OSError) -> None:
    print(f"no-suppressions-lint: warning: cannot walk path: {err}", file=sys.stderr)


def iter_scan_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for root_name in SCAN_ROOTS:
        root = repo / root_name
        if not root.is_dir():
            continue
        try:
            walker = os.walk(root, onerror=_walk_onerror)
        except OSError as err:
            print(
                f"no-suppressions-lint: warning: cannot start walk of {root}: {err}",
                file=sys.stderr,
            )
            continue
        for dirpath, dirnames, filenames in walker:
            # Prune snapcraft/build leftovers so packaging/snap/parts never stalls CI.
            dirnames[:] = [
                d
                for d in dirnames
                if d not in SKIP_DIR_NAMES
                and not d.startswith("build")
                and not d.startswith("obj-")
            ]
            # dh trees only under debian/ — do not prune same names elsewhere.
            if root_name == "debian":
                dirnames[:] = [d for d in dirnames if d not in DEBIAN_SKIP_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    if path.is_file() and is_scanned_file(path, repo):
                        files.append(path)
                except OSError as err:
                    print(
                        f"no-suppressions-lint: warning: skip {path}: {err}",
                        file=sys.stderr,
                    )
    for name in ROOT_FILES:
        path = repo / name
        try:
            if path.is_file() and is_scanned_file(path, repo):
                files.append(path)
        except OSError as err:
            print(
                f"no-suppressions-lint: warning: skip {path}: {err}",
                file=sys.stderr,
            )
    return sorted(files)


def lint_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        return [f"{path}: cannot read: {err}"]
    except UnicodeDecodeError:
        # Binary / non-UTF8 — skip quietly (not source we lint).
        return []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule_id, pattern, hint in RULES:
            if pattern.search(line):
                errors.append(f"{path}:{lineno}: [{rule_id}] {hint}")
    return errors


def lint_tree(repo: Path) -> list[str]:
    errors: list[str] = []
    for path in iter_scan_files(repo):
        errors.extend(lint_file(path))
    return errors


def main(argv: list[str]) -> int:
    repo = Path(argv[1] if len(argv) > 1 else ".").resolve()
    if not (repo / "AGENTS.md").is_file():
        print(f"error: not an Ubuntu Hello repo root: {repo}", file=sys.stderr)
        return 2
    errors = lint_tree(repo)
    if errors:
        print("no-suppressions-lint: FORBIDDEN suppressions found:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        print(
            f"no-suppressions-lint: {len(errors)} finding(s). "
            "Fix the code; do not silence linters/tests.",
            file=sys.stderr,
        )
        return 1
    print("no-suppressions-lint: OK (no NOLINT / shellcheck disable / noqa / type: ignore / xfail)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
