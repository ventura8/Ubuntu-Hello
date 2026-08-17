"""Uninstall / prerm restore the wallet password before deleting seals."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_ETC_REMOVE = re.compile(r"^\s*rm\s+-rf\s+/etc/ubuntu-hello(?!-)", re.MULTILINE)


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
    text = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    head = _before_etc_remove(text)
    assert "ubuntu-hello keyring restore --all" in head
    assert "step \"Restoring login keyring / KWallet password\"" in head


def test_debian_prerm_restores_before_deleting_config():
    text = (ROOT / "debian" / "ubuntu-hello.prerm").read_text(encoding="utf-8")
    head = _before_etc_remove(text)
    assert "ubuntu-hello keyring restore --all" in head
    assert "timeout 120" in head
    assert "|| true" in head
