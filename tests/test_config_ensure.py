"""Tests for config_ensure.py and dpkg postinst config.ini restore."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from config_ensure import config_needs_restore, ensure_system_config

ROOT = Path(__file__).resolve().parents[1]
SRC_TEMPLATE = ROOT / "ubuntu-hello" / "src" / "config.ini"


def _write(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as err:
        raise AssertionError(f"failed to write fixture {path}") from err


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise AssertionError(f"failed to read {path}") from err


def _read_repo(relative: str) -> str:
    path = ROOT / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise AssertionError(f"failed to read repository file {path}") from err


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError as err:
        raise AssertionError(f"failed to stat {path}") from err


def test_needs_restore_missing_file(tmp_path):
    assert config_needs_restore(str(tmp_path / "config.ini")) is True


def test_needs_restore_empty_file(tmp_path):
    dest = tmp_path / "config.ini"
    _write(dest, "")
    assert config_needs_restore(str(dest)) is True


def test_needs_restore_comment_only(tmp_path):
    dest = tmp_path / "config.ini"
    _write(dest, "# just a comment\n")
    assert config_needs_restore(str(dest)) is True


def test_needs_restore_has_video_section(tmp_path):
    dest = tmp_path / "config.ini"
    _write(dest, "[video]\ndevice_path = none\n")
    assert config_needs_restore(str(dest)) is False


def test_needs_restore_malformed_file(tmp_path):
    dest = tmp_path / "config.ini"
    _write(dest, "[core]\ndisabled = true\n[core]\nduplicate = 1\n")
    assert config_needs_restore(str(dest)) is False


def test_ensure_does_not_overwrite_existing(tmp_path):
    dest = tmp_path / "etc" / "config.ini"
    ensure_system_config(dest_path=str(dest), template_path=str(SRC_TEMPLATE))
    assert _is_file(dest)
    text = _read(dest)
    assert "[video]" in text
    assert "device_path" in text
    try:
        mode = dest.stat().st_mode & 0o777
    except OSError as err:
        raise AssertionError(f"failed to stat {dest}") from err
    assert mode == 0o644


def test_ensure_does_not_overwrite_existing(tmp_path):
    dest = tmp_path / "config.ini"
    existing = "[core]\ndisabled = true\n\n[video]\ndevice_path = /dev/video0\n"
    _write(dest, existing)
    ensure_system_config(dest_path=str(dest), template_path=str(SRC_TEMPLATE))
    assert _read(dest) == existing


def test_ensure_does_not_overwrite_malformed(tmp_path):
    dest = tmp_path / "config.ini"
    existing = "[core]\ndisabled = true\n[core]\nduplicate = 1\n"
    _write(dest, existing)
    ensure_system_config(dest_path=str(dest), template_path=str(SRC_TEMPLATE))
    assert _read(dest) == existing


def test_ensure_restores_empty_file(tmp_path):
    dest = tmp_path / "config.ini"
    _write(dest, "")
    ensure_system_config(dest_path=str(dest), template_path=str(SRC_TEMPLATE))
    assert "[video]" in _read(dest)


def test_ensure_missing_template_raises(tmp_path):
    dest = tmp_path / "config.ini"
    with pytest.raises(FileNotFoundError, match="config template not found"):
        ensure_system_config(
            dest_path=str(dest),
            template_path=str(tmp_path / "no-such-template.ini"),
        )
    assert not _is_file(dest)


def test_ensure_copy_oserror_propagates(tmp_path):
    dest = tmp_path / "config.ini"
    with patch("config_ensure.os.open", side_effect=OSError("denied")):
        with pytest.raises(OSError, match="denied"):
            ensure_system_config(dest_path=str(dest), template_path=str(SRC_TEMPLATE))
    assert not _is_file(dest)


def test_ensure_finds_packaged_or_source_template(tmp_path):
    dest = tmp_path / "config.ini"
    ensure_system_config(dest_path=str(dest))
    assert _is_file(dest)
    assert "[video]" in _read(dest)


def test_postinst_restores_missing_config_ini():
    postinst = _read_repo("debian/ubuntu-hello.postinst")
    configure = _read_repo("scripts/package-configure.sh")
    assert "package-configure.sh" in postinst
    assert "uh_package_configure" in postinst
    assert 'config_ini="$(uh_pkg_path "/etc/ubuntu-hello/config.ini")"' in configure
    assert 'config_template="$(uh_pkg_path "/usr/share/ubuntu-hello/config.ini")"' in configure
    assert '[ ! -f "$config_ini" ]' in configure
    assert 'mkdir -p "$(dirname "$config_ini")"' in configure
    assert 'cp "$config_template" "$config_ini"' in configure
    assert 'chmod 644 "$config_ini"' in configure
    assert ">>> Restored default config.ini" in configure
    assert "Failed to restore config.ini" in configure


def test_meson_installs_share_template():
    text = _read_repo("ubuntu-hello/src/meson.build")
    assert "install_data(" in text
    assert "'config.ini'" in text
    assert "install_dir: datadir" in text
    assert "'config_ensure.py'" in text
