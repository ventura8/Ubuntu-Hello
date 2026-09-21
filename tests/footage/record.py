#!/usr/bin/env python3
"""Record the three reference clips for the footage tier from the real camera.

Run as root (the camera device is root-owned under the PAM setup) with the app
installed, so the same capture code the authenticator uses does the recording:

    sudo UH_SRC=/usr/lib/x86_64-linux-gnu/ubuntu-hello python3 tests/footage/record.py [out_dir]

Three clips of six seconds each, with a desktop notification and a countdown
before each so the person has time to get into position:

    still   look at the camera and hold still           (enrollment source)
    nod     look at the camera and nod up and down       (same face, liveness)
    away    turn your back so no face is visible          (clean negative)

The clips are a face. Keep them out of the repository; deidentify.py extracts the
per-frame signals the tests need, after which the pixel files can be deleted.
"""
from __future__ import annotations

import configparser
import importlib
import os
import pwd
import subprocess
import sys
import time

import cv2
import numpy as np

SRC = os.environ.get("UH_SRC", "/usr/lib/ubuntu-hello")

CONFIG = "/etc/ubuntu-hello/config.ini"
SECONDS = 6
LEAD = 8
MAX_HEIGHT = 320
CLIPS = (
	("still", "Look at the camera and stay STILL"),
	("nod", "Look at the camera and NOD up and down, again and again"),
	("away", "Turn your BACK to the camera so it sees only the back of your head, no face at all"),
)


def _desktop_user():
	user = os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_UID")
	if user and user.isdigit():
		user = pwd.getpwuid(int(user)).pw_name
	return user


def tell(title, message, milliseconds):
	print(">>> %s: %s" % (title, message), flush=True)
	user = _desktop_user()
	if not user:
		return
	uid = pwd.getpwnam(user).pw_uid
	subprocess.Popen(["sudo", "-u", user, "env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%d/bus" % uid,
	                  "notify-send", "-u", "critical", "-t", str(milliseconds), title, message],
	                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _open_camera():
	"""The app's own capture class, from the installed tree, so frames match what it sees."""
	sys.path.insert(0, SRC)
	video_capture = importlib.import_module("recorders.video_capture")
	config = configparser.ConfigParser()
	config.read(CONFIG)
	return video_capture.VideoCapture(config)


def main(out_dir):
	os.makedirs(out_dir, exist_ok=True)
	capture = _open_camera()
	for name, instruction in CLIPS:
		tell("Next clip", "%s. Get ready, %d seconds." % (instruction, LEAD), LEAD * 1000)
		for remaining in range(LEAD, 0, -2):
			print("    ... %d" % remaining, flush=True)
			time.sleep(2)
		tell("GO", "%s NOW, for %d seconds" % (instruction, SECONDS), SECONDS * 1000)
		time.sleep(0.5)
		frames, stamps = [], []
		start = time.time()
		while time.time() - start < SECONDS:
			frame, _gs = capture.read_frame()
			height = frame.shape[0]
			if height > MAX_HEIGHT:
				scale = MAX_HEIGHT / height
				frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
			frames.append(frame)
			stamps.append(time.time() - start)
		path = os.path.join(out_dir, name + ".npz")
		np.savez_compressed(path, frames=np.stack(frames), t=np.array(stamps))
		print("    saved %s: %d frames" % (path, len(frames)), flush=True)
		tell("Stop", "Clip '%s' recorded. Relax." % name, 3000)
		time.sleep(3)
	capture.release()
	tell("Done", "All three clips recorded.", 4000)
	user = _desktop_user()
	if user:
		for name, _ in CLIPS:
			os.chown(os.path.join(out_dir, name + ".npz"), pwd.getpwnam(user).pw_uid, pwd.getpwnam(user).pw_gid)


if __name__ == "__main__":
	main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "fixtures", "footage"))
