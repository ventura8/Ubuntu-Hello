#!/usr/bin/env python3
import os
import sys
import shutil

def add_missing_confirmations(target_path):
    """The `[video] confirmations` migration, shared with the package path.

    Lives in config_ensure.py so package-configure.sh (deb, rpm, arch) runs the
    very same code; this script only reaches it from the source tree.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config_ensure

    return config_ensure.add_missing_confirmations(target_path)


def confirmations_for(certainty):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config_ensure

    return config_ensure.confirmations_for(certainty)


_POLKIT_OVERRIDE = (
    "[Service]\n"
    "PrivateDevices=no\n"
    "DeviceAllow=char-video4linux rw\n"
    "DeviceAllow=/dev/uinput rw\n"
    # TPM-sealed keyring unlock: polkit >= 126 sandboxes the helper
    # with DevicePolicy=strict + ProtectSystem=strict, which blocks
    # /dev/tpmrm0 and the transient .ctx files under tpm-keys/.
    "DeviceAllow=char-tpm rw\n"
    "DeviceAllow=/dev/tpm0 rw\n"
    "ReadWritePaths=/etc/ubuntu-hello/tpm-keys\n"
    # ProtectHome=yes also hides /run/user (the session bus): the
    # desktop notification could never reach the user from a
    # polkit prompt. tmpfs keeps /home and /root EMPTY inside the
    # unit and only /run/user is bound back in (the notifier drops
    # to the target uid before touching its bus socket).
    # /run/ubuntu-hello: root-only notification state.
    "ProtectHome=tmpfs\n"
    "BindReadOnlyPaths=-/run/user\n"
    "ReadWritePaths=-/run/ubuntu-hello\n"
)


def _drop_polkit_1_from_ignore_services(target_path):
    """Older configs ignored the polkit-1 PAM service; face auth needs it."""
    try:
        import configparser
        config = configparser.ConfigParser()
        config.read(target_path)
        if not config.has_option('core', 'ignore_services'):
            return
        value = config.get('core', 'ignore_services')
        services = [s.strip() for s in value.split(",") if s.strip()]
        if "polkit-1" not in services:
            return
        services.remove("polkit-1")
        config.set('core', 'ignore_services', ", ".join(services))
        with open(target_path, 'w') as f:
            config.write(f)
        print("Migrated existing config: removed 'polkit-1' from ignore_services")
    except Exception as e:
        print(f"Warning: Failed to migrate existing config: {e}")


def install_or_migrate_config(src_config, conf_dir):
    """Install the default config.ini, or migrate the one already there."""
    os.makedirs(conf_dir, exist_ok=True)
    target_path = os.path.join(conf_dir, 'config.ini')

    if os.path.exists(target_path):
        print(f"{target_path} already exists, not overwriting.")
        add_missing_confirmations(target_path)
        _drop_polkit_1_from_ignore_services(target_path)
        return

    shutil.copy(src_config, target_path)
    try:
        # 0644, matching docs/SECURITY.md, package-configure.sh and the
        # tests/vm/ mode check. The execute bit this used to set was never
        # meaningful for an .ini and diverged from every other install path.
        os.chmod(target_path, 0o644)
    except OSError:
        pass
    print(f"Installed default config.ini to {target_path}")


def create_tpm_keys_dir(conf_dir):
    """The polkit drop-in grants ReadWritePaths=/etc/ubuntu-hello/tpm-keys.

    systemd refuses to start the unit when that path is missing, so create it
    here for standalone Meson installs (install.sh / package-configure.sh do
    the same in their permission step). Root-only: transient TPM contexts.
    """
    tpm_keys_dir = os.path.join(conf_dir, 'tpm-keys')
    try:
        os.makedirs(tpm_keys_dir, mode=0o700, exist_ok=True)
        os.chmod(tpm_keys_dir, 0o700)
    except OSError as e:
        print(f"Warning: Failed to create {tpm_keys_dir}: {e}")


def write_polkit_override(destdir):
    """Let the sandboxed polkit agent helper reach the camera, uinput and TPM."""
    override_dir = '/etc/systemd/system/polkit-agent-helper@.service.d'
    if destdir:
        override_dir = os.path.join(destdir, override_dir.lstrip(os.sep))
    os.makedirs(override_dir, exist_ok=True)

    override_file = os.path.join(override_dir, 'override.conf')
    try:
        with open(override_file, 'w') as f:
            f.write(_POLKIT_OVERRIDE)
        os.chmod(override_file, 0o644)
        print(f"Configured Polkit systemd helper override at {override_file}")
    except OSError as e:
        print(f"Warning: Failed to write Polkit systemd helper override: {e}")


def reload_systemd():
    try:
        import subprocess
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "polkit-agent-helper.socket"], check=True)
        print("Polkit systemd socket enabled and systemd daemon reloaded successfully")
    except Exception as e:
        print(f"Warning: Failed to enable polkit-agent-helper.socket or reload daemon: {e}")


def main():
    if len(sys.argv) < 3:
        print("Usage: install_config.py <src_config_ini> <conf_dir>")
        sys.exit(1)

    if os.environ.get('MESON_INSTALL_DRY_RUN'):
        return

    src_config = sys.argv[1]
    conf_dir = sys.argv[2]

    destdir = os.environ.get('DESTDIR', '')
    if destdir:
        conf_dir = os.path.join(destdir, conf_dir.lstrip(os.sep))

    install_or_migrate_config(src_config, conf_dir)
    create_tpm_keys_dir(conf_dir)
    write_polkit_override(destdir)

    if not destdir:
        reload_systemd()


if __name__ == '__main__':
    main()
