# 🧠 Ubuntu Hello - AI Agent Skills & Runbook

This runbook documents the specific procedures, commands, and workflows required to execute tasks in the **Ubuntu Hello** repository. Use these "skills" to perform common developer actions.

---

## Skill 1: Compilation & Installation

Ubuntu Hello uses the **Meson** build system. Use these steps to build and install locally:

```bash
# 1. Clear any existing build artifacts
rm -rf builddir/

# 2. Setup the build directory with system parameters
meson setup builddir -Dprefix=/usr -Dsysconfdir=/etc -Dlibdir=lib \
  -Dinstall_pam_config=true -Dwith_polkit=true -Dfetch_dlib_data=true \
  -Dinih:with_INIReader=true

# 3. Compile the C++ PAM module and scripts
meson compile -C builddir

# 4. Install files globally (requires root privileges)
sudo meson install -C builddir
```

To completely uninstall Ubuntu Hello, run the uninstaller script:
```bash
sudo bash uninstall.sh
```

---

## Skill 2: Face Profile Management (CLI)

Use the built-in CLI command wrapper `ubuntu-hello` to perform administrative profile actions:

* **Enroll a Face Profile**:
  ```bash
  sudo ubuntu-hello add
  ```
  *(Follow the interactive CLI prompts to save your face profile descriptors)*
* **List Registered Profiles**:
  ```bash
  sudo ubuntu-hello list
  ```
* **Remove a Specific Profile**:
  ```bash
  sudo ubuntu-hello remove <model_id>
  ```
* **Clear All Face Profiles**:
  ```bash
  sudo ubuntu-hello clear
  ```

---

## Skill 3: Dry-Running & Diagnostics

### 3.1 Bypassing PAM for Testing
You can dry-run the face recognition engine without invoking the PAM stack or needing privilege elevation hooks:
```bash
sudo python3 /lib/security/ubuntu-hello/compare.py <target_username>
```
*(Replace `/lib/security/ubuntu-hello/compare.py` with `/usr/lib/security/ubuntu-hello/compare.py` depending on the system lib path)*

### 3.2 Visual Camera Verification Tool
Run the test subcommand to open a visual diagnostics window:
```bash
sudo ubuntu-hello test
```
This utility loads the camera reader, runs the face detection algorithm, draws facial bounding boxes and landmarks, and logs comparison confidence levels in real time.

---

## Skill 4: Troubleshooting & Log Collection

### 4.1 System Auth Logs
To monitor authentication lifecycle and execution output of the PAM module:
```bash
tail -f /var/log/auth.log | grep pam_ubuntu_hello
# Or on systemd-journal configurations:
journalctl -f | grep pam_ubuntu_hello
```

### 4.2 Verbose Debug Engine Logs
To print comprehensive timings, model details, and status notifications, edit `/etc/ubuntu-hello/config.ini`:
```ini
[debug]
end_report = true        # Log execution latency and model profiles
verbose_stamps = true    # Log execution of rubberstamp plugins
gtk_stdout = true        # Pipe GTK interface stdout to parent process terminal
```

> [!WARNING]
> **PAM Lockout Recovery**: If a C++ code modification breaks the compiled PAM module, you might be locked out of graphical logins and `sudo`. 
> To recover:
> 1. Switch to a Virtual Console (e.g. `Ctrl` + `Alt` + `F3`).
> 2. Log in using a standard password.
> 3. Edit `/etc/pam.d/common-auth` and comment out the line referencing `pam_ubuntu_hello.so`.

---

## Skill 5: Modifying the GTK GUI & Glade Layouts

The Graphical User Interface and the Onboarding Wizard use PyGObject (GTK 3) and Glade layouts:
* **UI Layout**: Edit `/ubuntu-hello-gtk/src/main.glade` using the Glade UI designer tool.
* **Main Configuration Window**: Controlled in `/ubuntu-hello-gtk/src/window.py` (which includes `/ubuntu-hello-gtk/src/tab_models.py` and `/ubuntu-hello-gtk/src/tab_video.py`).
* **Onboarding Wizard**: Controlled in `/ubuntu-hello-gtk/src/onboarding.py`.

### Theme Support
Ensure GTK overlays and settings windows honor system-wide preferences:
```python
# Verify dark/light theme detection and apply appropriate CSS providers
settings = Gtk.Settings.get_default()
theme_name = settings.get_property("gtk-theme-name")
```

---

## Skill 6: Keyring Configuration & Auto-Unlock

Automatic login keyring unlocking works via credential simulation:
* Keyring configurations are handled via GTK wizard slides and the `ubuntu-hello keyring` subcommand.
* Ensure credentials saved during face enrollment are written to the appropriate PAM keyring configuration helper directories (e.g. `/etc/pam.d/` overrides).

---

## Skill 7: Creating Custom Rubberstamp Hooks

Rubberstamps act as post-verification liveness checks. To write a custom rubberstamp:
1. Create a script `/ubuntu-hello/src/rubberstamps/yourstamp.py`.
2. Inherit from `RubberStamp` and implement `declare_config` and `run`:

```python
from rubberstamps import RubberStamp

class yourstamp(RubberStamp):
    def declare_config(self):
        # Add default options
        self.options["min_confidence"] = 0.85

    def run(self):
        # Update the UI prompt text
        self.set_ui_text("Look straight and smile", self.UI_TEXT)
        
        # Access video frame loop
        # ret, frame = self.video_capture.read_frame()
        
        # Return True if check passes, False to abort auth
        return True
```

3. Enable the rubberstamp in `/etc/ubuntu-hello/config.ini`:
```ini
[rubberstamps]
enabled = true
stamp_rules =
    yourstamp 10s failsafe min_confidence=0.90
```

---

## Skill 8: Generating Coverage & Linting Badges

You can update and generate local coverage and linting SVG badges by executing the badge generator script:

```bash
python3 generate_badges.py
```

This utility will:
1. Run pytest with coverage to collect fresh statistics.
2. Verify python syntax correctness across the codebase.
3. Automatically update `docs/badges/coverage.svg` and `docs/badges/linting.svg`.
