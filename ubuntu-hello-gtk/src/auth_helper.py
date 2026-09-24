import ctypes
import ctypes.util
import os

# PAM service file the setup wizard authenticates against.
PAM_SERVICE_PATH = "/etc/pam.d/ubuntu-hello-verify"
PAM_SERVICE_NAME = b"ubuntu-hello-verify"

# PAM_PROMPT_ECHO_OFF / PAM_PROMPT_ECHO_ON
_PROMPT_STYLES = (1, 2)
_PAM_CONV_ERR = 1


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


def _ensure_verify_service():
    """Write the wizard's own PAM service file if it is not there yet."""
    if os.path.exists(PAM_SERVICE_PATH):
        return
    try:
        with open(PAM_SERVICE_PATH, "w") as f:
            f.write("# PAM configuration for Ubuntu Hello setup wizard password verification\n")
            f.write("auth    required    pam_unix.so nullok_secure\n")
            f.write("account required    pam_unix.so\n")
        os.chmod(PAM_SERVICE_PATH, 0o644)
    except Exception:
        pass


def _load_libraries():
    """Return (libpam, libc), or (None, None) if either is unavailable."""
    pam_lib_path = ctypes.util.find_library("pam")
    if not pam_lib_path:
        return None, None
    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        return None, None
    return ctypes.CDLL(pam_lib_path), ctypes.CDLL(libc_path)


def _configure_signatures(libpam, libc):
    """Pin argtypes/restypes so pointers are not truncated on 64-bit platforms."""
    libpam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv), ctypes.POINTER(ctypes.c_void_p)]
    libpam.pam_start.restype = ctypes.c_int

    libpam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libpam.pam_authenticate.restype = ctypes.c_int

    libpam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libpam.pam_end.restype = ctypes.c_int

    libc.malloc.argtypes = [ctypes.c_size_t]
    libc.malloc.restype = ctypes.c_void_p


def _fill_response(libc, responses, index, password_bytes):
    """Hand PAM a malloc'd copy of the password. Returns False on malloc failure."""
    pw_len = len(password_bytes)
    resp_str = libc.malloc(pw_len + 1)
    if not resp_str:
        return False
    ctypes.memmove(resp_str, password_bytes, pw_len)
    ctypes.memset(resp_str + pw_len, 0, 1)  # null terminator
    responses[index].resp = ctypes.cast(resp_str, ctypes.c_char_p)
    responses[index].resp_retcode = 0
    return True


def _make_conv_callback(libc, password_bytes):
    """Build the PAM conversation callback for this password."""
    def pam_conv_callback(num_msg, msg_p, resp_p, appdata):
        # PAM free()s this array, so it has to come from libc malloc.
        resp_mem = libc.malloc(ctypes.sizeof(PamResponse) * num_msg)
        if not resp_mem:
            return _PAM_CONV_ERR

        responses = ctypes.cast(resp_mem, ctypes.POINTER(PamResponse * num_msg)).contents

        for i in range(num_msg):
            if msg_p[i].contents.msg_style not in _PROMPT_STYLES:
                responses[i].resp = None
                responses[i].resp_retcode = 0
                continue
            if not _fill_response(libc, responses, i, password_bytes):
                return _PAM_CONV_ERR

        resp_p[0] = ctypes.cast(resp_mem, ctypes.POINTER(PamResponse))
        return 0

    return pam_conv_callback


def verify_user_password(username, password):
    """
    Verifies the user's password using PAM (Pluggable Authentication Modules)
    directly through libpam.so via ctypes.
    """
    _ensure_verify_service()

    libpam, libc = _load_libraries()
    if libpam is None:
        return False
    _configure_signatures(libpam, libc)

    conv = PamConv(CONV_FUNC(_make_conv_callback(libc, password.encode('utf-8'))), None)
    pamh = ctypes.c_void_p()

    res = libpam.pam_start(PAM_SERVICE_NAME, username.encode('utf-8'), ctypes.byref(conv), ctypes.byref(pamh))
    if res != 0:
        return False

    try:
        return libpam.pam_authenticate(pamh, 0) == 0
    finally:
        libpam.pam_end(pamh, res)
