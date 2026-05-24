#!/usr/bin/env python3
import os
import subprocess
import pwd

def main():
    # If DESTDIR is set, we are packaging/staging, so do not launch the app
    if os.environ.get("DESTDIR"):
        return

    # Find the real user who ran sudo
    real_user = os.environ.get("SUDO_USER")
    if not real_user or real_user == "root":
        try:
            output = subprocess.check_output(["loginctl", "list-sessions"], text=True)
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[2] not in ("USER", "root"):
                    real_user = parts[2]
                    break
        except Exception:
            pass

    if not real_user or real_user == "root":
        for d in os.listdir("/home"):
            if d != "lost+found" and os.path.isdir(os.path.join("/home", d)):
                real_user = d
                break

    if not real_user:
        return

    env_display = os.environ.get("DISPLAY", ":0")
    env_xauth = os.environ.get("XAUTHORITY", f"/home/{real_user}/.Xauthority")

    # Run the GUI in the background as the active GUI user
    cmd = [
        "sudo", "-u", real_user,
        "env",
        f"DISPLAY={env_display}",
        f"XAUTHORITY={env_xauth}",
        "ubuntu-hello-gtk"
    ]

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Launched Ubuntu Hello GUI post-install for user {real_user} on display {env_display}")
    except Exception as e:
        print("Failed to launch GUI post-install:", e)

if __name__ == "__main__":
    main()
