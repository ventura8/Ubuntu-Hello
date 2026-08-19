"""Tests for scripts/no-suppressions-lint.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "no_suppressions_lint", ROOT / "scripts" / "no-suppressions-lint.py"
)
assert _SPEC is not None and _SPEC.loader is not None
nsl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nsl)


def test_skip_build_and_self():
    assert nsl.skip_path(Path("build-ci-lint/foo.py"))
    assert nsl.skip_path(Path(".cache/x.py"))
    assert nsl.skip_path(Path("scripts/i18n_fill_data/ubuntu-hello/de.json"))
    assert nsl.skip_path(Path("scripts/no-suppressions-lint.py"))
    assert nsl.skip_path(Path("tests/test_no_suppressions_lint.py"))
    assert nsl.skip_path(Path("packaging/snap/parts/ubuntu-hello/install/x.py"))
    assert nsl.skip_path(Path("packaging/snap/stage/bin/x.sh"))
    assert not nsl.skip_path(Path("ubuntu-hello/src/cli.py"))


def test_lint_file_flags_common_suppressions(tmp_path: Path):
    samples = {
        "a.py": "x = 1  # noqa: E501\n",
        "b.py": "from x import y  # type: ignore\n",
        "c.cc": "// NOLINTNEXTLINE(readability-magic-numbers)\nint x = 1;\n",
        "d.sh": "# shellcheck disable=SC2086\necho $x\n",
        "e.py": "@pytest.mark.xfail\ndef test_x():\n    pass\n",
        "f.py": "@pytest.mark.skip(reason='no')\ndef test_y():\n    pass\n",
    }
    found_rules: set[str] = set()
    for name, body in samples.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        errors = nsl.lint_file(path)
        assert errors, name
        for err in errors:
            # "...: [rule-id] hint"
            rule = err.split("[", 1)[1].split("]", 1)[0]
            found_rules.add(rule)
    assert "python-noqa" in found_rules
    assert "python-type-ignore" in found_rules
    assert "clang-tidy-nolint" in found_rules
    assert "shellcheck-disable" in found_rules
    assert "pytest-mark-xfail" in found_rules
    assert "pytest-mark-skip" in found_rules


def test_lint_file_allows_env_pytest_skip(tmp_path: Path):
    path = tmp_path / "gate.py"
    path.write_text(
        'import pytest\npytest.skip("tool missing")\n',
        encoding="utf-8",
    )
    assert nsl.lint_file(path) == []


def test_repo_has_no_suppressions():
    """Production + tests must stay clean (same gate as CI lint stage)."""
    errors = nsl.lint_tree(ROOT)
    assert errors == [], "\n".join(errors)
