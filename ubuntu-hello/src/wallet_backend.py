"""Detect the session wallet backend (GNOME Keyring vs KWallet).

Face auth sets PAM_AUTHTOK for downstream modules such as pam_gnome_keyring
and pam_kwallet5. This helper only labels which consumer is typical for the
current desktop — it does not change the sealed credential format.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from i18n import _


def detect_desktop(environ: Optional[dict] = None) -> str:
	"""Return kde, gnome, xfce, cinnamon, mate, budgie, lxqt, or unknown."""
	env = environ if environ is not None else os.environ
	raw = (env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or "").lower()
	tokens = [t for t in re.split(r"[:\s;,]+", raw) if t]
	joined = " ".join(tokens)

	def has(*names: str) -> bool:
		return any(n in tokens or n in joined for n in names)

	if has("kde", "plasma"):
		return "kde"
	if has("xfce", "xubuntu"):
		return "xfce"
	if has("cinnamon"):
		return "cinnamon"
	if has("mate"):
		return "mate"
	if has("budgie"):
		return "budgie"
	if has("lxqt", "lubuntu"):
		return "lxqt"
	if has("gnome", "ubuntu", "unity", "pop"):
		return "gnome"
	return "unknown"


def detect_wallet_backend(environ: Optional[dict] = None) -> str:
	"""Return ``kwallet``, ``gnome-keyring``, or ``none``.

	Plasma sessions use KWallet (pam_kwallet5). Most Ubuntu-family DEs use
	GNOME Keyring when the PAM consumer is present. ``none`` means no clear
	desktop wallet is inferred (auto-unlock still depends on PAM stack).
	"""
	desktop = detect_desktop(environ)
	if desktop == "kde":
		return "kwallet"
	if desktop in ("gnome", "cinnamon", "mate", "budgie", "xfce", "lxqt"):
		return "gnome-keyring"
	return "none"


def wallet_backend_label(environ: Optional[dict] = None) -> str:
	"""Short user-facing label for the inferred wallet backend."""
	backend = detect_wallet_backend(environ)
	if backend == "kwallet":
		return _("KWallet")
	if backend == "gnome-keyring":
		return _("GNOME Keyring")
	return _("None")


def wallet_unlock_phrase(environ: Optional[dict] = None) -> str:
	"""Phrase for UI copy: login keyring, KWallet, or both."""
	backend = detect_wallet_backend(environ)
	if backend == "kwallet":
		return _("KWallet")
	if backend == "gnome-keyring":
		return _("login keyring")
	return _("login keyring or KWallet")
