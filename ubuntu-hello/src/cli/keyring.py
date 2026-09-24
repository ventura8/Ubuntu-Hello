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


_USAGE = "keyring [enable|disable|restore [--all]]"


def _print_usage_and_exit():
	# The command name and its subcommands are literals the user has to type,
	# so only the label is translatable. Machine translation localised the
	# whole line in 91 of 98 catalogs, which told people to run commands that
	# do not exist ("clavèr activar", "klíčenka povolit").
	print(_("Usage:") + " " + _USAGE)
	sys.exit(1)


def _read_new_password(user, wallet, backend_label):
	"""Read the password to seal, from a pipe or an interactive prompt."""
	if not sys.stdin.isatty():
		piped = sys.stdin.readline().strip('\n')
		if not piped:
			# EOF or a bare newline. Sealing "" looks like success here but
			# unseal_password() treats an empty result as failure, so the user
			# would end up with a seal that never unlocks anything.
			print(_("Password cannot be empty"))
			sys.exit(1)
		return piped

	passwd1 = getpass.getpass(_("Enter password for user {} to unlock {} ({}): ").format(user, wallet, backend_label))
	if not passwd1:
		print(_("Password cannot be empty"))
		sys.exit(1)

	if passwd1 != getpass.getpass(_("Confirm password: ")):
		print(_("Passwords do not match"))
		sys.exit(1)
	return passwd1


def _tpm_usable():
	"""True when a TPM device and tpm2-tools are both present.

	Installs tpm2-tools when the hardware is there but the tools are not.
	"""
	tpm_dev_exists = os.path.exists("/dev/tpmrm0") or os.path.exists("/dev/tpm0")
	if not tpm_dev_exists:
		return False

	def tools_present():
		# _seal_with_tpm calls createprimary + create; keyring_restore's unseal
		# path calls load + unseal. A partial install must fall back to software
		# rather than failing halfway through sealing.
		return all(shutil.which(cmd) is not None for cmd in (
			"tpm2_createprimary", "tpm2_create", "tpm2_load", "tpm2_unseal"))

	if tools_present():
		return True

	print(_("TPM hardware detected. Auto-installing tpm2-tools..."))
	try:
		subprocess.run(["apt-get", "install", "-y", "-qq", "tpm2-tools"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
	except Exception:
		return False
	return tools_present()


def _seal_with_tpm(user, passwd, key_file, pub_file, priv_file, backend_label):
	print(_("TPM hardware active. Sealing password in TPM..."))
	try:
		os.makedirs(TPM_KEYS_DIR, exist_ok=True)
		os.chmod(TPM_KEYS_DIR, 0o700)

		primary_ctx = os.path.join(TPM_KEYS_DIR, f"primary_{os.getpid()}.ctx")
		subprocess.run(["tpm2_createprimary", "-C", "o", "-c", primary_ctx], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

		p = subprocess.Popen(["tpm2_create", "-C", primary_ctx, "-i", "-", "-u", pub_file, "-r", priv_file],
							 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
		_stdout, stderr = p.communicate(input=passwd.encode())

		if os.path.exists(primary_ctx):
			try:
				os.unlink(primary_ctx)
			except Exception:
				pass

		if p.returncode != 0:
			raise RuntimeError(stderr.decode())

		os.chmod(pub_file, 0o600)
		os.chmod(priv_file, 0o600)

		# Only now that the TPM seal is on disk is it safe to drop the software
		# key: a failure above must leave the previous credential usable.
		if os.path.exists(key_file):
			os.unlink(key_file)

		print(_("Keyring/KWallet unlocking enabled successfully for user {} using TPM (wallet: {}).").format(user, backend_label))
	except Exception as e:
		print(_("Failed to seal password to TPM: {}").format(e))
		sys.exit(1)


def _seal_with_software(user, passwd, key_file, pub_file, priv_file, backend_label):
	"""AES-256-GCM with a root-only master key. Always writes a fresh UH1 blob."""
	print(_("No TPM active. Using software-based credential caching..."))
	try:
		ciphertext = encrypt_password(passwd)

		os.makedirs(KEYRING_KEYS_DIR, exist_ok=True)
		os.chmod(KEYRING_KEYS_DIR, 0o700)

		# Always overwrite (migrates legacy XOR → UH1)
		with open(key_file, "w") as f:
			f.write(ciphertext + "\n")

		os.chmod(key_file, 0o600)

		# Only now that the software key is on disk is it safe to drop any TPM
		# seal: a failure above must leave the previous credential usable.
		for path in (pub_file, priv_file):
			if os.path.exists(path):
				os.unlink(path)

		print(_("Keyring/KWallet unlocking enabled successfully for user {} (Software Caching, wallet: {}).").format(user, backend_label))
	except Exception as e:
		print(_("Failed to enable keyring unlocking: {}").format(e))
		sys.exit(1)


def _keyring_enable(user, wallet, backend_label, key_file, pub_file, priv_file):
	passwd = _read_new_password(user, wallet, backend_label)
	if _tpm_usable():
		_seal_with_tpm(user, passwd, key_file, pub_file, priv_file, backend_label)
	else:
		_seal_with_software(user, passwd, key_file, pub_file, priv_file, backend_label)


def _keyring_disable(user, paths):
	deleted = False
	for path in paths:
		if not os.path.exists(path):
			continue
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


def _restore_every_user():
	ok, fail = restore_all_users()
	if ok == 0 and fail == 0:
		print(_("No sealed login passwords found; nothing to restore."))
		return
	print(_("Restored login wallet password for {} user(s).").format(ok))
	if fail:
		print(_("Could not restore login wallet password for {} user(s).").format(fail))
		print(_("If prompts persist, set the login keyring or KWallet password in Seahorse / System Settings."))
		sys.exit(1)


def _keyring_restore(user, arguments):
	"""Re-assert the sealed login password as the OS wallet password.

	Does not delete seals (uninstall / apt prerm delete afterwards).
	"""
	rest = arguments[1:]
	# run_keyring() may be called with explicit arguments (not via the CLI
	# import path), in which case the builtins are never set — read them
	# defensively instead of assuming the CLI populated them.
	cli_args = getattr(builtins, "ubuntu_hello_args", None)
	want_all = bool(getattr(cli_args, "all", False)) or rest == ["--all"]

	if rest and rest != ["--all"]:
		_print_usage_and_exit()

	if want_all:
		_restore_every_user()
	elif restore_user(user):
		print(_("Restored login wallet password for user {}.").format(user))
	else:
		print(_("Could not restore login wallet password for user {}.").format(user))
		sys.exit(1)


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
		_print_usage_and_exit()

	action = arguments[0].lower()
	key_file = os.path.join(KEYRING_KEYS_DIR, user)
	pub_file = os.path.join(TPM_KEYS_DIR, f"{user}.pub")
	priv_file = os.path.join(TPM_KEYS_DIR, f"{user}.priv")
	pending_file = os.path.join(PENDING_DIR, user)

	if action == "enable":
		_keyring_enable(user, wallet_unlock_phrase(), wallet_backend_label(),
						key_file, pub_file, priv_file)
	elif action == "disable":
		_keyring_disable(user, (key_file, pub_file, priv_file, pending_file))
	elif action == "restore":
		_keyring_restore(user, arguments)
	else:
		# Same reasoning as the usage line above: the three words in quotes are
		# what the user types, so they stay out of the translatable part.
		print(_("Invalid action. Use one of:") + " 'enable', 'disable', 'restore'.")
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
