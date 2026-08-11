# Opens auth ui if requested, otherwise starts normal ui
import os
import sys

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

# System locale + bindtextdomain before any user-visible string / Glade load.
import i18n  # noqa: F401


def _ensure_ubuntu_hello_on_path(here: str | None = None) -> None:
	"""Make core helpers (e.g. wallet_backend) importable from the GTK app.

	GTK sources live in ``…/ubuntu-hello-gtk`` while ``wallet_backend.py`` is
	installed next to the CLI under ``…/ubuntu-hello``. Tests already put both
	on ``sys.path`` via conftest; the installed launcher must do the same.
	"""
	base = os.path.dirname(os.path.abspath(here or __file__))
	candidates = [
		os.path.join(os.path.dirname(base), "ubuntu-hello"),
		"/usr/lib/ubuntu-hello",
		"/usr/local/lib/ubuntu-hello",
	]
	# Debian multiarch / custom libdir: /usr/lib/<triplet>/ubuntu-hello
	lib_root = os.path.dirname(base)
	if os.path.basename(base) == "ubuntu-hello-gtk" and os.path.isdir(lib_root):
		try:
			for name in os.listdir(lib_root):
				candidates.append(os.path.join(lib_root, name, "ubuntu-hello"))
		except OSError:
			pass

	for path in candidates:
		if not path or not os.path.isfile(os.path.join(path, "wallet_backend.py")):
			continue
		if path not in sys.path:
			# Append so local GTK modules (i18n, paths) stay preferred.
			sys.path.append(path)
		return


_ensure_ubuntu_hello_on_path()

# Set the application name and program name so GNOME Shell maps the window to the desktop file.
GLib.set_prgname("ubuntu-hello-gtk")
GLib.set_application_name("Ubuntu Hello")

if "--start-auth-ui" in sys.argv:
	import authsticky
else:
	import window
