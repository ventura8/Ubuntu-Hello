#!/usr/bin/env python3
"""Drive a PAM authentication with a scripted conversation, and count the prompts.

pam_probe.py <service> <user> <password>
Prints one JSON line: {"result": <pam return code>, "prompts": <n>, "texts": [...]}

Runs the real PAM stack for *service* exactly as a login program would, with the
conversation answering every password prompt with *password*. The greeter rule
in pam_ubuntu_hello is about prompts: with `workaround = off` the module must not
ask for a password itself, or GDM's user selection bounces back to the list. The
prompt count is how that is observed without a display manager.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import sys

PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON, PAM_ERROR_MSG, PAM_TEXT_INFO = 1, 2, 3, 4
PAM_SUCCESS = 0

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libpam = ctypes.CDLL(ctypes.util.find_library("pam"))
libc.calloc.restype = ctypes.c_void_p
libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
libc.strdup.restype = ctypes.c_void_p
libc.strdup.argtypes = [ctypes.c_char_p]


class PamMessage(ctypes.Structure):
	_fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
	_fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(PamMessage)),
                        ctypes.POINTER(ctypes.POINTER(PamResponse)), ctypes.c_void_p)


class PamConv(ctypes.Structure):
	_fields_ = [("conv", CONV), ("appdata_ptr", ctypes.c_void_p)]


def main(service, user, password):
	seen = {"prompts": 0, "texts": []}

	def conversation(num_msg, msg, resp, appdata):
		responses = libc.calloc(num_msg, ctypes.sizeof(PamResponse))
		array = ctypes.cast(responses, ctypes.POINTER(PamResponse))
		for i in range(num_msg):
			message = msg[i].contents
			text = (message.msg or b"").decode(errors="replace")
			seen["texts"].append("%d:%s" % (message.msg_style, text.strip()))
			if message.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
				seen["prompts"] += 1
				array[i].resp = libc.strdup(password.encode())
			array[i].resp_retcode = 0
		resp[0] = array
		return PAM_SUCCESS

	conv = PamConv(CONV(conversation), None)
	handle = ctypes.c_void_p()
	libpam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv), ctypes.POINTER(ctypes.c_void_p)]
	rc = libpam.pam_start(service.encode(), user.encode(), ctypes.byref(conv), ctypes.byref(handle))
	if rc != PAM_SUCCESS:
		print(json.dumps({"result": rc, "prompts": 0, "texts": ["pam_start failed"]}))
		return
	libpam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
	result = libpam.pam_authenticate(handle, 0)
	libpam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
	libpam.pam_end(handle, result)
	print(json.dumps({"result": result, "prompts": seen["prompts"], "texts": seen["texts"]}))


if __name__ == "__main__":
	if len(sys.argv) != 4:
		sys.exit(__doc__)
	main(*sys.argv[1:])
