# Ubuntu-Hello/Ubuntu Hello Developer Guide

This document acts as a comprehensive reference guide for developers and AI agents aiming to build, extend, debug, and troubleshoot the Ubuntu-Hello/Ubuntu Hello codebase.

---

## 1. Directory Structure

```
├── .clang-tidy                # C++ linter settings
├── meson.build                # Root build system definition
├── meson.options              # Build options (paths, dependencies)
├── ubuntu-hello/                     # Core backend module
│   ├── src/
│   │   ├── bin/               # Script template for /usr/bin/ubuntu-hello wrapper
│   │   ├── cli/               # Subcommands (add, test, config, remove, etc.)
│   │   ├── pam/               # C++ PAM module sources (main.cc, enter_device.cc)
│   │   ├── recorders/         # Camera capturing plugins (ffmpeg, pyv4l2, cv2)
│   │   ├── rubberstamps/      # Post-auth verification hooks (nod, hotkey)
│   │   ├── cli.py             # CLI Router entrypoint
│   │   ├── compare.py         # Face verification main engine
│   │   ├── config.ini         # Default configuration template
│   │   └── paths_factory.py   # Utility calculating locations of configuration/models
│   └── meson.build
└── ubuntu-hello-gtk/                 # Graphical user interface module
    ├── src/
    │   ├── authsticky.py      # Floating status overlay display
    │   ├── window.py          # Admin/control panel window
    │   ├── onboarding.py      # Setup wizard
    │   └── polkit/            # Polkit rules for elevated access
    └── meson.build
```

---

## 2. Dev Environment and Build Lifecycle

### 2.1 Dependencies
Ensure all compilation and runtime dependencies are installed. On Debian/Ubuntu:
```bash
sudo apt-get update && sudo apt-get install -y \
  python3 python3-pip python3-setuptools python3-wheel \
  cmake make build-essential \
  libpam0g-dev libinih-dev libevdev-dev python3-opencv \
  python3-dev libopencv-dev libgirepository1.0-dev
```

### 2.2 Compilation and Installation
Ubuntu Hello uses the Meson build system paired with Ninja:
```bash
# Configure the build directory
meson setup build

# Compile all modules (PAM C++ and scripts configuration)
meson compile -C build

# Install files globally (requires root privileges)
sudo meson install -C build
```

#### Notable Build Options
Configure options during setup using `-Doption=value`:
* `python_path`: Absolute path to Python binary to build against (defaults to path detected by Meson).
* `config_dir`: System path where configs are stored (default: `/etc/ubuntu-hello`).
* `user_models_dir`: Location of compiled facial descriptors (default: `/etc/ubuntu-hello/models`).

---

## 3. Extending the Codebase

### 3.1 Adding a New CLI Command
CLI subcommands are located in `ubuntu-hello/src/cli/`.
To add a new command (e.g. `example`):
1. Create a python file in `ubuntu-hello/src/cli/example.py`.
2. Add your command logic. The command arguments are exposed via `builtins.ubuntu_hello_args`.
3. Open `ubuntu-hello/src/cli.py` and modify the argument parser:
   ```python
   parser.add_argument(
       "command",
       choices=["add", "clear", "config", "disable", "list", "remove", "set", "snapshot", "test", "version", "example"]
   )
   ```
4. Map the execution in `ubuntu-hello/src/cli.py` at the bottom:
   ```python
   elif args.command == "example":
       import cli.example
   ```

### 3.2 Adding a New Rubberstamp (Liveness / Verification Check)
Rubberstamps act as plugins running after a valid face comparison.
To write a new stamp class:
1. Create a script in `ubuntu-hello/src/rubberstamps/yourstamp.py`.
2. Define a class named `yourstamp` that inherits from `RubberStamp`:
   ```python
   from rubberstamps import RubberStamp
   import time

   class yourstamp(RubberStamp):
       def declare_config(self):
           # Set default configurations (accessible under self.options)
           self.options["custom_parameter"] = 10.0

       def run(self):
           # Display text on the floating GTK window
           self.set_ui_text("Running Custom Check...", self.UI_TEXT)
           
           # Access camera frame loop:
           # ret, frame = self.video_capture.read_frame()
           # processed = self.clahe.apply(frame)
           
           # Return True if check passes, False to abort authentication
           return True
   ```
3. Update your local config rules `/etc/ubuntu-hello/config.ini`:
   ```ini
   [rubberstamps]
   enabled = true
   stamp_rules =
       yourstamp  5s  failsafe  custom_parameter=12.0
   ```

---

## 4. Debugging & Troubleshooting

### 4.1 Bypassing PAM
If you need to test the recognition logic without invoking PAM (e.g., sudo), run `compare.py` directly:
```bash
sudo python3 /lib/security/ubuntu-hello/compare.py <target_username>
```
*Replace `/lib/security/ubuntu-hello/compare.py` with your installed script location if different.*

### 4.2 Logging
* **Auth Logs**: Check `/var/log/auth.log` or syslog for runtime traces from the PAM module `pam_ubuntu_hello`.
  ```bash
  tail -f /var/log/auth.log | grep pam_ubuntu_hello
  ```
* **Engine Debug Logs**: Turn on detailed reports in `/etc/ubuntu-hello/config.ini`:
  ```ini
  [debug]
  end_report = true        # Prints latency statistics and model indices
  verbose_stamps = true    # Logs detailed rubberstamp execution
  gtk_stdout = true        # Pipes standard output of GUI to console
  ```

### 4.3 Execution Error Codes Reference

| Exit Code | Constant / Cause | Description |
|---|---|---|
| `0` | `EXIT_SUCCESS` | Authentication succeeded, face verified. |
| `10` | `NO_FACE_MODEL` | No face models recorded or matching file `/etc/ubuntu-hello/models/<user>.dat` is missing/empty. |
| `11` | `TIMEOUT_REACHED` | Facial recognition loop hit maximum timeout config without finding a match. |
| `12` | `INVALID_ARGUMENTS` | Executed `compare.py` or CLI with missing or invalid arguments. |
| `13` | `TOO_DARK` | All captured video frames exceeded the config `dark_threshold` value. |
| `14` | `INVALID_DEVICE` | Could not connect to or read frames from the device path. |
| `15` | `RUBBERSTAMP_FAIL` | Face was authenticated but a liveness rubberstamp check returned False. |
