"""Uninstall / prerm restore the wallet password before deleting seals."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_ETC_REMOVE = re.compile(
    r'^\s*rm\s+-rf\s+(?:"\$\(uh_pkg_path\s+)?/etc/ubuntu-hello(?!-)', re.MULTILINE
)
_PRERM_SOURCES = re.compile(
    r"remove\|purge\)[\s\S]*?\. /usr/share/ubuntu-hello/package-prerm\.sh[\s\S]*?uh_package_prerm",
    re.MULTILINE,
)


def _read_repo_text(relative: str) -> str:
    path = ROOT / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise AssertionError(f"failed to read repository file {path}") from err


def _before_etc_remove(text: str) -> str:
    match = _ETC_REMOVE.search(text)
    assert match is not None, "expected removal of /etc/ubuntu-hello"
    return text[: match.start()]


def test_before_etc_remove_rejects_hello_gtk_prefix():
    text = "rm -rf /etc/ubuntu-hello-gtk\nrm -rf /etc/ubuntu-hello\n"
    head = _before_etc_remove(text)
    assert head == "rm -rf /etc/ubuntu-hello-gtk\n"
    assert "rm -rf /etc/ubuntu-hello\n" not in head


def test_uninstall_sh_restores_before_deleting_config():
    text = _read_repo_text("uninstall.sh")
    head = _before_etc_remove(text)
    assert "ubuntu-hello keyring restore --all" in head
    assert "step \"Restoring login keyring / KWallet password\"" in head


def test_debian_prerm_restores_before_deleting_config():
    prerm = _read_repo_text("debian/ubuntu-hello.prerm")
    shared = _read_repo_text("scripts/package-prerm.sh")
    assert _PRERM_SOURCES.search(prerm), (
        "prerm must source /usr/share/ubuntu-hello/package-prerm.sh and call "
        "uh_package_prerm in the remove|purge branch"
    )
    head = _before_etc_remove(shared)
    assert "ubuntu-hello keyring restore --all" in head
    assert "timeout 120" in head
    assert "|| true" in head
