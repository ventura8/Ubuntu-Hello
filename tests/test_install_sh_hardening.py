"""Source install/uninstall hardening: restore order, hashes, env pins."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DLIB_MODEL_SHA256 = (
    "abb1f61041e434465855ce81c2bd546e830d28bcbed8d27ffbe5bb408b11553a",
    "db9e9e40f092c118d5eb3e643935b216838170793559515541c56a2b50d9fc84",
    "6e787bbebf5c9efdb793f6cd1f023230c4413306605f24f299f12869f95aa472",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_install_sh_uses_package_configure_hashes_and_pinned_dlib():
    text = _read("install.sh")
    assert "scripts/package-configure.sh" in text
    assert "uh_download_models" in text
    assert "uh_ensure_dlib" in text
    assert "uh_set_permissions" in text
    assert "-Dfetch_dlib_data=false" in text
    assert "pip3 install dlib" not in text
    assert "UH_OFFICIAL_REPO_URL" in text
    assert "Ignoring untrusted UH_REPO_URL" in text


def test_package_configure_and_download_models_share_sha256():
    configure = _read("scripts/package-configure.sh")
    downloader = _read("ubuntu-hello/src/download_models.py")
    for digest in DLIB_MODEL_SHA256:
        assert digest in configure
        assert digest in downloader
    assert "dlib==19.24.9" in configure


def test_install_sh_does_not_honor_arbitrary_repo_url():
    text = _read("install.sh")
    assert 'REPO_URL="${UH_REPO_URL:-' not in text
    assert "UH_OFFICIAL_REPO_URL=" in text
