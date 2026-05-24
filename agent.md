# 🤖 Ubuntu Hello - Generic AI Agent Workspace Guidelines

This document provides system-level guidelines, architectural principles, coding standards, and safety precautions for any AI coding agent or Large Language Model (LLM) interacting with the **Ubuntu Hello** codebase.

---

## 1. Project DNA & Context

* **What is Ubuntu Hello?**
  Ubuntu Hello is a Windows Hello™-style facial authentication system for Linux. It integrates with PAM (Pluggable Authentication Module) to authorize users for `sudo`, screen unlocks, login managers (e.g., GDM), `su`, and graphical authentication requests (via Polkit).
* **Historical Context**: The project was rebranded from *Howdy*. Ensure all references, variables, packages, and system files use the prefix/name `ubuntu-hello` or `ubuntu_hello`. Do **not** use the old name.
* **Target OS**: Primarily designed and optimized for **Ubuntu 26.04 LTS (Plucky / Resolute)**, but compatible with other modern Debian-based and Arch-based Linux distributions.
* **Core Goal**: Secure, reliable, and smooth facial authentication.

---

## 2. High-Level Component Architecture

AI agents must understand the relationships and communication channels between the project's core modules:

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant PAM as PAM Service (sudo/gdm)
    participant C++ as pam_ubuntu_hello.so (C++)
    participant Python as compare.py (Python Engine)
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
    GTK->>User: Display "Identifying you..."
    
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
        C++-->>PAM: Return PAM_AUTH_ERR
        PAM-->>User: Fallback to Password Prompt
    end
    deactivate C++
```

### IPC Protocol (Python Engine to GTK UI)
The python matching engine (`compare.py`) writes commands to the GTK overlay's standard input:
* `M=<text>`: Updates main prompt text (e.g. `M=Identifying...`).
* `S=<text>`: Updates subtext.

---

## 3. Directory Layout Reference

Ensure files are modified or added in their appropriate structural directories:

| Path | Purpose / Description |
|---|---|
| `/ubuntu-hello/src/pam/` | C++ PAM module source code (`main.cc`, `enter_device.cc`). |
| `/ubuntu-hello/src/cli/` | Command line subcommands (`add`, `clear`, `config`, `disable`, `list`, `remove`, `test`, `snapshot`). |
| `/ubuntu-hello/src/recorders/` | Camera capturing plugins (wrapper, ffmpeg, pyv4l2, cv2). |
| `/ubuntu-hello/src/rubberstamps/` | Post-auth liveness check plugins (e.g., nose-tracking nod check). |
| `/ubuntu-hello-gtk/src/` | Administrative settings panel (`window.py`), setup wizard (`onboarding.py`), and overlay window (`authsticky.py`). |
| `/debian/` or `/ubuntu-hello/debian/` | Debian packaging control, installation, and post-installation scripts. |

---

## 4. AI Coding Standards & Rules

To maintain high software quality, AI agents must adhere to the following coding rules:

### 4.1 C++ Implementation Guidelines
* Use **C++17** features for robust and clean code.
* Ensure all files conform to the project linter settings in `.clang-tidy`.
* Run background processes via safe subprocess spawning APIs (`posix_spawnp`) rather than raw `fork()`/`exec()` or vulnerable `system()` shell-escapes.
* Always clean up file descriptors, allocated resources, and threads (`std::future` or `std::thread`).

### 4.2 Python Implementation Guidelines
* Target **Python 3.10+**.
* Adhere to PEP 8 spacing and structure conventions.
* Implement structured error handling; wrap OS level syscalls, subprocess executions, and file I/O operations in `try-except` blocks.
* Keep imports organized and avoid circular dependencies (e.g., import cli components dynamically in the router `cli.py`).

### 4.3 Security & Integrity Rules
> [!CAUTION]
> The C++ PAM module and the comparison engine run with superuser (root) privileges. Security is paramount.

* **No Shell Arbitrary Code Execution**: Avoid executing shell strings. Use array/list-based subprocess invocations to prevent shell injection vectors.
* **Privilege Separation**: Keep system passwords, credentials, and facial models read-only and restricted to `root` or owners under `/etc/ubuntu-hello/`.
* **Resource Leak Prevention**: Ensure camera handles (`cv2.VideoCapture`), subprocesses, and shared memory pipes are explicitly closed/terminated in `finally` blocks.

### 4.4 Documentation & Comments
* Maintain documentation integrity. Keep existing comments and docstrings intact unless directly refactoring the referenced logic.
* Document any new class methods, rubberstamp plugins, or configuration options you add.

---

## 5. Standard Exit Code Mapping

When modifying the verification loop or PAM helper logic, match these standardized exit codes:

| Code | Value | Description |
|---|---|---|
| `0` | `EXIT_SUCCESS` | Authentication succeeded, face verified. |
| `10` | `NO_FACE_MODEL` | Facial models missing or empty. |
| `11` | `TIMEOUT_REACHED` | Recognition loop timed out without a match. |
| `12` | `INVALID_ARGUMENTS` | Invalid command line arguments passed. |
| `13` | `TOO_DARK` | Camera frames are below the dark brightness threshold. |
| `14` | `INVALID_DEVICE` | Could not capture frames from target webcam. |
| `15` | `RUBBERSTAMP_FAIL` | Face verified but liveness/rubberstamp check failed. |
