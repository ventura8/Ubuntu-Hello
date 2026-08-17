# Subcommand to enable/disable keyring unlocking
import sys
import os
import builtins
import getpass
import shutil
import subprocess
from i18n import _
from keyring_crypto import encrypt_password
from keyring_restore import restore_all_users, restore_user
from wallet_backend import wallet_backend_label, wallet_unlock_phrase

KEYRING_KEYS_DIR = "/etc/ubuntu-hello/keyring-keys"
TPM_KEYS_DIR = "/etc/ubuntu-hello/tpm-keys"
PENDING_DIR = "/etc/ubuntu-hello/keyring-caching-pending"


def run_keyring(user=None, arguments=None):
	"""Enable/disable keyring unlocking for *user*.

	Uses ``builtins.ubuntu_hello_user`` / ``ubuntu_hello_args`` when args omitted
	(CLI import path). Software enable always writes a fresh ``UH1:`` blob
	(overwriting any legacy XOR content). Downstream PAM consumers of the
	stored password via PAM_AUTHTOK include pam_gnome_keyring and pam_kwallet5.
	"""
	if user is None:
		user = builtins.ubuntu_hello_user
	if arguments is None:
		arguments = builtins.ubuntu_hello_args.arguments

	if not arguments:
		print(_("Usage: keyring [enable|disable|restore [--all]]"))
		sys.exit(1)

	action = arguments[0].lower()
	wallet = wallet_unlock_phrase()
	backend_label = wallet_backend_label()

	key_file = os.path.join(KEYRING_KEYS_DIR, user)
	pub_file = os.path.join(TPM_KEYS_DIR, f"{user}.pub")
	priv_file = os.path.join(TPM_KEYS_DIR, f"{user}.priv")
	pending_file = os.path.join(PENDING_DIR, user)

	if action == "enable":
		if not sys.stdin.isatty():
			passwd1 = sys.stdin.readline().strip('\n')
			passwd2 = passwd1
		else:
			passwd1 = getpass.getpass(_("Enter password for user {} to unlock {} ({}): ").format(user, wallet, backend_label))
			if not passwd1:
				print(_("Password cannot be empty"))
				sys.exit(1)

			passwd2 = getpass.getpass(_("Confirm password: "))
			if passwd1 != passwd2:
				print(_("Passwords do not match"))
				sys.exit(1)

		# Detect TPM
		tpm_dev_exists = os.path.exists("/dev/tpmrm0") or os.path.exists("/dev/tpm0")
		tpm_tools_exist = shutil.which("tpm2_createprimary") is not None and shutil.which("tpm2_unseal") is not None

		# Auto-install if needed
		if tpm_dev_exists and not tpm_tools_exist:
			print(_("TPM hardware detected. Auto-installing tpm2-tools..."))
			try:
				subprocess.run(["apt-get", "install", "-y", "-qq", "tpm2-tools"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
				tpm_tools_exist = shutil.which("tpm2_createprimary") is not None and shutil.which("tpm2_unseal") is not None
			except Exception:
				pass

		if tpm_dev_exists and tpm_tools_exist:
			# Use TPM
			print(_("TPM hardware active. Sealing password in TPM..."))
			try:
				if os.path.exists(key_file):
					os.unlink(key_file)

				os.makedirs(TPM_KEYS_DIR, exist_ok=True)
				os.chmod(TPM_KEYS_DIR, 0o700)

				primary_ctx = os.path.join(TPM_KEYS_DIR, f"primary_{os.getpid()}.ctx")
				subprocess.run(["tpm2_createprimary", "-C", "o", "-c", primary_ctx], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

				p = subprocess.Popen(["tpm2_create", "-C", primary_ctx, "-i", "-", "-u", pub_file, "-r", priv_file],
									 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
				stdout, stderr = p.communicate(input=passwd1.encode())

				if os.path.exists(primary_ctx):
					try:
						os.unlink(primary_ctx)
					except Exception:
						pass

				if p.returncode != 0:
					raise Exception(stderr.decode())

				os.chmod(pub_file, 0o600)
				os.chmod(priv_file, 0o600)
				print(_("Keyring/KWallet unlocking enabled successfully for user {} using TPM (wallet: {}).").format(user, backend_label))
			except Exception as e:
				print(_("Failed to seal password to TPM: {}").format(e))
				sys.exit(1)
		else:
			# Software fallback: AES-256-GCM with root-only master key (always UH1)
			print(_("No TPM active. Using software-based credential caching..."))
			try:
				for path in (pub_file, priv_file):
					if os.path.exists(path):
						os.unlink(path)

				ciphertext = encrypt_password(passwd1)

				os.makedirs(KEYRING_KEYS_DIR, exist_ok=True)
				os.chmod(KEYRING_KEYS_DIR, 0o700)

				# Always overwrite (migrates legacy XOR → UH1)
				with open(key_file, "w") as f:
					f.write(ciphertext + "\n")

				os.chmod(key_file, 0o600)
				print(_("Keyring/KWallet unlocking enabled successfully for user {} (Software Caching, wallet: {}).").format(user, backend_label))
			except Exception as e:
				print(_("Failed to enable keyring unlocking: {}").format(e))
				sys.exit(1)

	elif action == "disable":
		deleted = False
		for path in (key_file, pub_file, priv_file, pending_file):
			if os.path.exists(path):
				try:
					os.unlink(path)
					deleted = True
				except Exception as e:
					print(_("Failed to disable keyring unlocking: {}").format(e))
					sys.exit(1)
		if deleted:
			print(_("Keyring/KWallet unlocking disabled for user {}.").format(user))
		else:
			print(_("Keyring/KWallet unlocking was not enabled for user {}.").format(user))

	elif action == "restore":
		# Re-assert the sealed login password as the OS wallet password.
		# Does not delete seals (uninstall / apt prerm delete afterwards).
		rest = arguments[1:]
		if rest == ["--all"]:
			ok, fail = restore_all_users()
			if ok == 0 and fail == 0:
				print(_("No sealed login passwords found; nothing to restore."))
			else:
				print(_("Restored login wallet password for {} user(s).").format(ok))
				if fail:
					print(_("Could not restore login wallet password for {} user(s).").format(fail))
					print(_("If prompts persist, set the login keyring or KWallet password in Seahorse / System Settings."))
					sys.exit(1)
		elif rest:
			print(_("Usage: keyring [enable|disable|restore [--all]]"))
			sys.exit(1)
		else:
			if restore_user(user):
				print(_("Restored login wallet password for user {}.").format(user))
			else:
				print(_("Could not restore login wallet password for user {}.").format(user))
				sys.exit(1)

	else:
		print(_("Invalid action. Use 'enable', 'disable', or 'restore'."))
		sys.exit(1)


def _cli_autostart():
	"""Run when imported from cli.py (``command == keyring``)."""
	if (
		hasattr(builtins, "ubuntu_hello_args")
		and hasattr(builtins, "ubuntu_hello_user")
		and getattr(builtins.ubuntu_hello_args, "command", None) == "keyring"
	):
		run_keyring()


# CLI entry: ``import cli.keyring`` after builtins are set (cli.py sets command).
_cli_autostart()
