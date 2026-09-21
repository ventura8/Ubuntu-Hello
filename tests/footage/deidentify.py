#!/usr/bin/env python3
"""Turn a recorded clip into signals the tests can replay without any pixels.

A clip recorded by record.py is a face. This keeps, per frame, only what the
recognition loop and the nod check consume: whether the frame was too dark to
use, whether one face was found and where, the five landmark points, and the
128-number descriptor. None of that can be turned back into an image, and the
pixel file can then be deleted.

Usage:  UH_REAL_DLIB=1 python3 tests/footage/deidentify.py <clip.npz> [more.npz ...]
Writes  <clip>.signals.npz next to each input.
"""
from __future__ import annotations

import os
import sys

import numpy as np

DLIB_DATA_CANDIDATES = ("/usr/share/dlib-data", "/usr/share/ubuntu-hello/dlib-data", "/usr/lib/ubuntu-hello/dlib-data")
DARK_THRESHOLD = 60.0     # [video] dark_threshold in the shipped config


def dlib_data_dir():
	for candidate in DLIB_DATA_CANDIDATES:
		if os.path.isfile(os.path.join(candidate, "shape_predictor_5_face_landmarks.dat")):
			return candidate
	sys.exit("dlib model files not found in " + ", ".join(DLIB_DATA_CANDIDATES))


def deidentify(path):
	import cv2
	import dlib
	data = dlib_data_dir()
	detector = dlib.get_frontal_face_detector()
	predictor = dlib.shape_predictor(os.path.join(data, "shape_predictor_5_face_landmarks.dat"))
	encoder = dlib.face_recognition_model_v1(os.path.join(data, "dlib_face_recognition_resnet_model_v1.dat"))
	clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

	z = np.load(path)
	frames, t = z["frames"], z["t"]
	n = len(frames)
	dark = np.zeros(n, dtype=bool)
	faces = np.zeros(n, dtype=np.int16)
	rect = np.zeros((n, 4), dtype=np.int32)
	landmarks = np.zeros((n, 5, 2), dtype=np.int32)
	descriptor = np.zeros((n, 128), dtype=np.float32)
	for i, frame in enumerate(frames):
		gs = clahe.apply(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
		hist = cv2.calcHist([gs], [0], None, [8], [0, 256])
		if hist[0] / hist.sum() * 100 > DARK_THRESHOLD:
			dark[i] = True
			continue
		found = detector(gs, 1)
		faces[i] = len(found)
		if len(found) != 1:
			continue
		face = found[0]
		rect[i] = (face.left(), face.top(), face.right(), face.bottom())
		shape = predictor(frame, face)
		landmarks[i] = [(shape.part(k).x, shape.part(k).y) for k in range(5)]
		descriptor[i] = np.array(encoder.compute_face_descriptor(frame, shape, 1), dtype=np.float32)
	out = path[:-4] + ".signals.npz" if path.endswith(".npz") else path + ".signals.npz"
	np.savez_compressed(out, t=t, height=int(frames.shape[1]), width=int(frames.shape[2]),
	                    dark=dark, faces=faces, rect=rect, landmarks=landmarks, descriptor=descriptor)
	print("%s: %d frames, %d dark, %d with one face -> %s (%.0f KB)" % (
		os.path.basename(path), n, int(dark.sum()), int((faces == 1).sum()), os.path.basename(out), os.path.getsize(out) / 1024))


if __name__ == "__main__":
	if len(sys.argv) < 2:
		sys.exit(__doc__)
	for clip in sys.argv[1:]:
		deidentify(clip)
