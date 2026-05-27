# Subcommand to enable/disable keyring unlocking
import sys
import os
import builtins
import getpass
from i18n import _

# Helper function to encrypt/decrypt
def xor_crypt(data: str, key: str) -> str:
    return ''.join(f"{ord(c) ^ ord(key[i % len(key)]):02x}" for i, c in enumerate(data))

# Get the target user
user = builtins.ubuntu_hello_user

# Check if arguments are supplied
if not builtins.ubuntu_hello_args.arguments:
    print(_("Usage: keyring [enable|disable]"))
    sys.exit(1)

action = builtins.ubuntu_hello_args.arguments[0].lower()

keyring_keys_dir = "/etc/ubuntu-hello/keyring-keys"
tpm_keys_dir = "/etc/ubuntu-hello/tpm-keys"
pending_dir = "/etc/ubuntu-hello/keyring-caching-pending"

key_file = os.path.join(keyring_keys_dir, user)
pub_file = os.path.join(tpm_keys_dir, f"{user}.pub")
priv_file = os.path.join(tpm_keys_dir, f"{user}.priv")
pending_file = os.path.join(pending_dir, user)

import shutil
import subprocess

if action == "enable":
    if not sys.stdin.isatty():
        passwd1 = sys.stdin.readline().strip('\n')
        passwd2 = passwd1
    else:
        passwd1 = getpass.getpass(_("Enter password for user {} to unlock keyring: ").format(user))
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
                
            os.makedirs(tpm_keys_dir, exist_ok=True)
            os.chmod(tpm_keys_dir, 0o700)

            primary_ctx = os.path.join(tpm_keys_dir, f"primary_{os.getpid()}.ctx")
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
            print(_("Keyring unlocking enabled successfully for user {} using TPM.").format(user))
        except Exception as e:
            print(_("Failed to seal password to TPM: {}").format(e))
            sys.exit(1)
    else:
        # Software fallback
        print(_("No TPM active. Using software-based credential caching..."))
        try:
            for path in (pub_file, priv_file):
                if os.path.exists(path):
                    os.unlink(path)
            with open("/etc/machine-id", "r") as f:
                machine_id = f.read().strip()
        except Exception as e:
            print(_("Failed to read /etc/machine-id: {}").format(e))
            sys.exit(1)
            
        if not machine_id:
            print(_("/etc/machine-id is empty"))
            sys.exit(1)
            
        ciphertext = xor_crypt(passwd1, machine_id)
        
        try:
            os.makedirs(keyring_keys_dir, exist_ok=True)
            os.chmod(keyring_keys_dir, 0o700)
            
            with open(key_file, "w") as f:
                f.write(ciphertext + "\n")
                
            os.chmod(key_file, 0o600)
            print(_("Keyring unlocking enabled successfully for user {} (Software Caching).").format(user))
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
        print(_("Keyring unlocking disabled for user {}.").format(user))
    else:
        print(_("Keyring unlocking was not enabled for user {}.").format(user))

else:
    print(_("Invalid action. Use 'enable' or 'disable'."))
    sys.exit(1)
