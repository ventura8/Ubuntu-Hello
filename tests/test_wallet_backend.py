"""Tests for wallet_backend.py (GNOME Keyring vs KWallet labels)."""
import wallet_backend as wb


class TestDetectWalletBackend:
	def test_kde_kwallet(self):
		assert wb.detect_wallet_backend({"XDG_CURRENT_DESKTOP": "KDE"}) == "kwallet"
		assert wb.wallet_backend_label({"XDG_CURRENT_DESKTOP": "Plasma"}) == "KWallet"
		assert wb.wallet_unlock_phrase({"XDG_CURRENT_DESKTOP": "KDE"}) == "KWallet"

	def test_gnome_keyring(self):
		assert wb.detect_wallet_backend({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}) == "gnome-keyring"
		assert wb.wallet_backend_label({"XDG_CURRENT_DESKTOP": "GNOME"}) == "GNOME Keyring"
		assert wb.wallet_unlock_phrase({"XDG_CURRENT_DESKTOP": "GNOME"}) == "login keyring"

	def test_xfce_cinnamon_mate_budgie_lxqt(self):
		for env in (
			{"XDG_CURRENT_DESKTOP": "XFCE"},
			{"XDG_CURRENT_DESKTOP": "X-Cinnamon"},
			{"XDG_CURRENT_DESKTOP": "MATE"},
			{"XDG_CURRENT_DESKTOP": "Budgie:GNOME"},
			{"XDG_CURRENT_DESKTOP": "LXQt"},
		):
			assert wb.detect_wallet_backend(env) == "gnome-keyring"

	def test_unknown_none(self):
		assert wb.detect_wallet_backend({}) == "none"
		assert wb.wallet_backend_label({}) == "None"
		assert wb.wallet_unlock_phrase({}) == "login keyring or KWallet"
