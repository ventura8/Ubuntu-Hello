# Ubuntu-Hello/Ubuntu Hello Architecture

This document provides a deep dive into the architecture, component interaction, and design patterns of the Ubuntu-Hello/Ubuntu Hello facial recognition authentication system.

---

## 1. High-Level System Architecture

Ubuntu Hello functions as a PAM (Pluggable Authentication Module) provider. When a Linux service requests authentication (e.g., `sudo`, lock screen login, or GDM), the PAM stack invokes the Ubuntu Hello C++ module. This module then spawns a Python subprocess to handle camera streaming, face detection, and verification, communicating progress back to a floating GTK status window.

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant PAM as PAM Service (sudo/gdm)
    participant C++ as pam_ubuntu_hello.so (C++)
    participant Python as compare.py (Python)
    participant GTK as ubuntu-hello-gtk (Overlay UI)
    participant Cam as Video Capture Device

    User->>PAM: Invokes Auth Command (e.g., sudo)
    PAM->>C++: pam_sm_authenticate()
    activate C++
    Note over C++: Check lid state, SSH,<br/>and config
    C++->>Python: Spawn compare.py subprocess (posix_spawnp)
    activate Python
    Python->>GTK: Spawn ubuntu-hello-gtk --start-auth-ui
    activate GTK
    GTK->>User: Display "Starting up..." overlay
    Python->>Cam: Open camera (OpenCV / FFmpeg / pyv4l2)
    activate Cam
    Cam-->>Python: Grab video frames
    deactivate Cam
    Note over Python: Face Detection &<br/>ResNet Recognition
    Python->>GTK: Write IPC status via stdin (e.g., "M=Identifying...")
    GTK->>User: Display "Identifying you..." & frame count
    
    rect rgb(240, 240, 240)
        Note over Python: If Match Succeeded & Rubberstamps Enabled
        Python->>Python: Run Rubberstamps (e.g., nod liveness check)
        Python->>GTK: Write rubberstamp prompt (e.g., "M=Nod to confirm")
        GTK->>User: Show rubberstamp confirmation request
    end

    Python-->>C++: Exit status (0 = success, other = error)
    deactivate Python
    C++->>GTK: Terminate GTK overlay process (SIGTERM)
    deactivate GTK
    
    alt Match Succeeded (Exit 0)
        C++-->>PAM: Return PAM_SUCCESS
        PAM-->>User: Grant authentication
    else Match Failed or Timed Out
        Note over C++: If native/input workaround,<br/>handle password fallback
        C++-->>PAM: Return PAM_AUTH_ERR
        PAM-->>User: Fallback to Password Prompt
    end
    deactivate C++
```

---

## 2. Core Components

### 2.1 Pluggable Authentication Module (PAM) (`ubuntu-hello/src/pam/`)
* **Role**: The main entry point loaded by Linux security services. Written in C++ for security and low runtime footprint.
* **Key Files**:
  - `main.cc`: Implements standard PAM hooks (`pam_sm_authenticate`, `pam_sm_open_session`, etc.). Parses config, detects laptop lid status via ACPI button interface (`/proc/acpi/button/lid/*/state`), and checks for SSH environments.
  - `enter_device.cc` / `hh`: Simulates a keyboard "Enter" keypress via `/dev/uinput` to dismiss active system password prompts when facial authentication completes.
  - `optional_task.hh`: Helper class managing background threads using `std::future` to listen for user passwords concurrently.
* **Process Lifecycle**: Spawns Python using `posix_spawnp` rather than `system` or raw `fork/exec` for performance and safety.

### 2.2 Face Matching Engine (`ubuntu-hello/src/compare.py`)
* **Role**: Orchestrates camera feeds and face detection.
* **Logic Flow**:
  1. Spawns `ubuntu-hello-gtk --start-auth-ui` as a subprocess to create the overlay.
  2. Spawns a background thread to load heavy recognition models (`dlib`).
  3. Initializes the camera recorder plugin and applies contrast optimization (`cv2.createCLAHE`).
  4. Runs a capture-and-compare loop:
     - Discards fully black/dark frames based on configurable brightness thresholds.
     - Feeds valid frames to the `dlib` face detector.
     - Evaluates facial features using a 5-point landmark shape predictor and a ResNet face recognition model.
     - Computes the Euclidean distance (L2 norm) between current face descriptors and saved models:
       $$\text{distance} = \|\mathbf{v}_{\text{known}} - \mathbf{v}_{\text{current}}\|_{2}$$
     - If the lowest distance is below `certainty / 10`, matches are accepted.
  5. Executes post-auth checks (Rubberstamps) if enabled.
  6. Exits with corresponding status codes (`0` on success, or error status codes).

### 2.3 GTK Graphical Interface (`ubuntu-hello-gtk/src/`)
* **Role**: Displays status notifications and onboarding/administration windows.
* **Key Sub-modules**:
  - `authsticky.py` (`StickyWindow`): A frameless, semi-transparent top-aligned overlay mimicking Windows Hello. Communicates with the matching engine by parsing `sys.stdin` commands line-by-line.
  - `window.py` / `tab_models.py` / `tab_video.py`: The administrative UI for managing users, adding/removing face profiles, and tweaking camera parameters.
  - `onboarding.py`: Wizard helping first-time users identify their camera and construct their first facial profile.

### 2.4 Administration CLI (`ubuntu-hello/src/cli.py` & `cli/`)
* **Role**: Configures the facial engine, handles profile collection, and executes diagnostic tests.
* **Commands**:
  - `add.py`: Guides users in creating a new profile. Captures up to 60 frames and extracts the first frame containing exactly one face to write to models.
  - `list.py`, `remove.py`, `clear.py`: Manage profile models (`models/<username>.dat`).
  - `test.py`: Debugging CLI tool that launches a local window showing the camera stream with highlighted landmarks and matching thresholds.

### 2.5 Camera Recorders Abstraction (`ubuntu-hello/src/recorders/`)
* **Role**: Abstracts different camera APIs to deal with varying Linux kernel webcam drivers.
* **Plugins**:
  - `video_capture.py`: Selects the appropriate reader according to the `recording_plugin` config parameter.
  - `v4l2.py` / `pyv4l2_reader.py`: Low-level wrapper around V4L2 (Video4Linux) ioctl calls.
  - `ffmpeg_reader.py`: Uses a non-blocking subprocess pipeline reading raw frames from FFmpeg.
  - `cv2.VideoCapture`: standard OpenCV backend.

### 2.6 Rubberstamps Hooks System (`ubuntu-hello/src/rubberstamps/`)
* **Role**: Provides post-verification liveness checks to prevent static photo spoofs.
* **Architecture**:
  - Inherits from `RubberStamp` base class. Uses `SourceFileLoader` to load plugins dynamically.
  - `nod.py`: Nose-tracking algorithm checking if the user actively nods up and down (to verify authentication) or shakes their head (to abort).
  - `hotkey.py`: Prompts the user to hit a specific hotkey (like `Enter` or `Esc`) to verify intent.

---

## 3. Data Flow & Inter-Process Communication (IPC)

### 3.1 C++ to Python Subprocess Launch
The PAM module starts Python as a subprocess:
```cpp
const char *const args[] = {PYTHON_EXECUTABLE_PATH, COMPARE_PROCESS_PATH, username, nullptr};
posix_spawnp(&child_pid, PYTHON_EXECUTABLE_PATH, nullptr, nullptr, const_cast<char *const *>(args), nullptr);
```
C++ evaluates the return code using standard wait macros:
* `EXIT_SUCCESS` ($0$): Face authenticated.
* `11` (`TIMEOUT_REACHED`): Comparison took longer than the video timeout.
* `13` (`TOO_DARK`): Captured frames were darker than configured limits.
* `10` (`NO_FACE_MODEL`): No face models recorded for the target user.

### 3.2 Python to GTK Overlay Pipe
`compare.py` starts `ubuntu-hello-gtk` and keeps a handle to standard input:
```python
gtk_proc = subprocess.Popen(["ubuntu-hello-gtk", "--start-auth-ui"], stdin=subprocess.PIPE)
```
Status updates are written as short formatted line structures:
- `M=<text>`: Updates main window status text.
- `S=<text>`: Updates subtext status.
`authsticky.py` runs a 10ms loop reading line-by-line using `gobject.timeout_add`:
```python
comm = sys.stdin.readline()[:-1]
if comm.startswith("M="):
    self.message = comm[2:].strip()
```

---

## 4. Models and Profiles Format

Face models are saved in `/etc/ubuntu-hello/models/<username>.dat` as standard JSON databases:
```json
[
  {
    "id": 0,
    "label": "Initial model",
    "time": 1785939200,
    "data": [
      [-0.110293, 0.089201, 0.043920, ...]
    ]
  }
]
```
* `id`: Incremental integer identifying the scan.
* `label`: String to identify the scan condition (e.g. "Glasses", "Morning").
* `time`: Unix timestamp.
* `data`: Array containing a 128-dimensional floating point vector generated by dlib's ResNet face recognition model.
