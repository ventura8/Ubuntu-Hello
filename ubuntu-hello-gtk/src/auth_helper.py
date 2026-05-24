import ctypes
import ctypes.util
import os

def verify_user_password(username, password):
    """
    Verifies the user's password using PAM (Pluggable Authentication Modules)
    directly through libpam.so via ctypes.
    """
    # 1. Ensure PAM verification service file exists in /etc/pam.d/
    pam_service_path = "/etc/pam.d/ubuntu-hello-verify"
    if not os.path.exists(pam_service_path):
        try:
            with open(pam_service_path, "w") as f:
                f.write("# PAM configuration for Ubuntu Hello setup wizard password verification\n")
                f.write("auth    required    pam_unix.so nullok_secure\n")
                f.write("account required    pam_unix.so\n")
            os.chmod(pam_service_path, 0o644)
        except Exception:
            pass

    # 2. Find and load PAM library
    pam_lib_path = ctypes.util.find_library("pam")
    if not pam_lib_path:
        return False
    libpam = ctypes.CDLL(pam_lib_path)

    # 3. Load standard C library to allocate memory that PAM will free
    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        return False
    libc = ctypes.CDLL(libc_path)

    # 4. Define PAM structures
    class PamMessage(ctypes.Structure):
        _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]

    class PamResponse(ctypes.Structure):
        _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]

    # Callback type: int conv(int, const struct pam_message **, struct pam_response **, void *)
    CONV_FUNC = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.POINTER(PamMessage)),
        ctypes.POINTER(ctypes.POINTER(PamResponse)),
        ctypes.c_void_p
    )

    class PamConv(ctypes.Structure):
        _fields_ = [("conv", CONV_FUNC), ("appdata_ptr", ctypes.c_void_p)]

    # Configure ctypes function signatures to prevent truncation on 64-bit platforms
    libpam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv), ctypes.POINTER(ctypes.c_void_p)]
    libpam.pam_start.restype = ctypes.c_int

    libpam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libpam.pam_authenticate.restype = ctypes.c_int

    libpam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libpam.pam_end.restype = ctypes.c_int

    libc.malloc.argtypes = [ctypes.c_size_t]
    libc.malloc.restype = ctypes.c_void_p

    # Convert strings to bytes
    username_bytes = username.encode('utf-8')
    password_bytes = password.encode('utf-8')

    def pam_conv_callback(num_msg, msg_p, resp_p, appdata):
        # Allocate memory for the responses array via libc malloc as PAM will free() it
        size = ctypes.sizeof(PamResponse) * num_msg
        resp_mem = libc.malloc(size)
        if not resp_mem:
            return 1 # PAM_CONV_ERR

        # Cast raw memory to a ctypes array structure
        responses = ctypes.cast(resp_mem, ctypes.POINTER(PamResponse * num_msg)).contents

        for i in range(num_msg):
            msg = msg_p[i].contents
            # Style 1: PAM_PROMPT_ECHO_OFF, Style 2: PAM_PROMPT_ECHO_ON
            if msg.msg_style in (1, 2):
                pw_len = len(password_bytes)
                resp_str = libc.malloc(pw_len + 1)
                if not resp_str:
                    return 1 # PAM_CONV_ERR
                ctypes.memmove(resp_str, password_bytes, pw_len)
                ctypes.memset(resp_str + pw_len, 0, 1) # null terminator
                responses[i].resp = ctypes.cast(resp_str, ctypes.c_char_p)
                responses[i].resp_retcode = 0
            else:
                responses[i].resp = None
                responses[i].resp_retcode = 0

        resp_p[0] = ctypes.cast(resp_mem, ctypes.POINTER(PamResponse))
        return 0

    conv = PamConv(CONV_FUNC(pam_conv_callback), None)
    pamh = ctypes.c_void_p()

    # Start PAM session with our specific verify service
    res = libpam.pam_start(b"ubuntu-hello-verify", username_bytes, ctypes.byref(conv), ctypes.byref(pamh))
    if res != 0:
        return False

    try:
        auth_res = libpam.pam_authenticate(pamh, 0)
        return auth_res == 0
    finally:
        libpam.pam_end(pamh, res)
