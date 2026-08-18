# Restore /etc/ubuntu-hello/config.ini from the packaged template when missing.
#
# dpkg does not unpack a deleted conffile on reinstall. apt remove runs prerm
# which deletes /etc/ubuntu-hello, so the next apt install leaves no config.ini
# and the setup wizard crashes with NoSectionError: video.
#
# Do not import paths_factory here: GTK puts its own paths_factory on sys.path
# first, and GTK data_dir is /usr/share/ubuntu-hello-gtk (no config.ini).

import configparser
import os
import tempfile
from pathlib import Path

_CONFIG_MODE = 0o644
_DEFAULT_ETC = "/etc/ubuntu-hello/config.ini"
_DEFAULT_SHARE = "/usr/share/ubuntu-hello/config.ini"


def _live_config_path() -> str:
    try:
        import paths
        return str(Path(paths.config_dir) / "config.ini")
    except (ImportError, AttributeError):
        return _DEFAULT_ETC


def packaged_template_path() -> str:
    """Return the first packaged/source default config.ini that exists."""
    candidates = []
    try:
        import paths
        candidates.append(str(Path(paths.data_dir) / "config.ini"))
    except (ImportError, AttributeError):
        pass
    candidates.append(_DEFAULT_SHARE)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "config.ini"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return _DEFAULT_SHARE


def _try_parse(path: str) -> configparser.ConfigParser | None:
    """Parse INI; None means the file exists but could not be read/parsed."""
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except (configparser.Error, OSError):
        return None
    return parser


def config_needs_restore(path: str) -> bool:
    """True when the live config is missing or parsed with no INI sections.

    Malformed or unreadable files are preserved (not restored).
    """
    if not os.path.isfile(path):
        return True
    parser = _try_parse(path)
    if parser is None:
        return False
    return not parser.sections()


def _install_template(template: str, dest: str) -> None:
    """Write template to dest without clobbering a file created concurrently."""
    parent = os.path.dirname(dest) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        with open(template, "rb") as src:
            data = src.read()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(dest, flags, _CONFIG_MODE)
        except FileExistsError:
            if not config_needs_restore(dest):
                return
        else:
            try:
                os.write(fd, data)
                os.fchmod(fd, _CONFIG_MODE)
            finally:
                os.close(fd)
            return
        fd, tmp = tempfile.mkstemp(prefix=".config.ini.", dir=parent)
        tmp_path = tmp
        try:
            os.write(fd, data)
            os.fchmod(fd, _CONFIG_MODE)
            os.close(fd)
            fd = -1
            os.replace(tmp_path, dest)
            tmp_path = None
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp_path is not None and os.path.lexists(tmp_path):
                os.unlink(tmp_path)
    except OSError as err:
        raise OSError(
            f"failed to restore config from {template!r} to {dest!r}: {err}"
        ) from err


def ensure_system_config(
    dest_path: str | None = None,
    template_path: str | None = None,
) -> str:
    """Copy the packaged default config.ini into place when the live file is gone.

    Never overwrites a config that already has sections (user settings) or a
    file that exists but cannot be parsed. Raises FileNotFoundError if restore
    is required and the template is missing. Raises OSError if mkdir/copy/chmod
    fails.
    """
    dest = dest_path if dest_path is not None else _live_config_path()
    template = template_path if template_path is not None else packaged_template_path()
    if not config_needs_restore(dest):
        return dest
    if not os.path.isfile(template):
        raise FileNotFoundError(
            f"config template not found at {template!r}; cannot restore {dest!r}"
        )
    _install_template(template, dest)
    return dest
