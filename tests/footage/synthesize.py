#!/usr/bin/env python3
"""Generate synthetic signal clips so the footage tier can run with no real face.

The recorded signals of a real person stay off the repository: a descriptor is a
biometric template. This makes stand-ins with the same *shape* -- the infrared
strobe darkening every other frame, one face per usable frame, a nod as a nose
that travels up and down, a stranger as a descriptor far from the enrolled one --
from random numbers seeded for reproducibility. They exercise compare.py's loop,
the agreeing-frames gate and the nod stamp exactly as the real signals do; only
the calibration test, which is about a real camera, is meaningless on them and
skips.

Usage:  python3 tests/footage/synthesize.py [output_dir]
"""
from __future__ import annotations

import os
import sys

import numpy as np

FPS = 15.5
SECONDS = 6.0
HEIGHT, WIDTH = 320, 569
# Measured on real infrared footage of one person: descriptors have a norm of
# about 1.4, two frames of the same face sit a median 0.094 apart (10th to 90th
# percentile 0.073 to 0.131), and a different face sits about 0.6 away. The
# duplicate filter in enrollment drops anything closer than 0.05, so a spread
# that is too tight would leave it with a single sample.
DESCRIPTOR_NORM = 1.42
SAME_FACE_PAIRWISE = 0.094
OTHER_FACE_DISTANCE = 0.62
# Per-component noise that gives that expected pairwise distance in 128 dimensions.
SAME_FACE_SIGMA = SAME_FACE_PAIRWISE / np.sqrt(2 * 128)


def _descriptor_center(rng):
	v = rng.normal(size=128)
	return v / np.linalg.norm(v) * DESCRIPTOR_NORM


def _clip(rng, seconds=SECONDS, faces=True, nod=False, center=None):
	n = int(round(seconds * FPS))
	t = np.arange(n) / FPS
	dark = (np.arange(n) % 2 == 0)                       # the strobe: every other frame
	face_count = np.where(dark, 0, 1 if faces else 0).astype(np.int16)
	rect = np.zeros((n, 4), dtype=np.int32)
	landmarks = np.zeros((n, 5, 2), dtype=np.int32)
	descriptor = np.zeros((n, 128), dtype=np.float32)
	cx, cy, half = WIDTH // 2, HEIGHT // 2, 70
	eye_gap = 60
	for i in range(n):
		if face_count[i] != 1:
			continue
		# a nod moves the whole face up and down about 30 px over ~1.2 s
		dy = int(round(15 * np.sin(2 * np.pi * t[i] / 1.2))) if nod else 0
		jitter = rng.integers(-2, 3, size=2)
		rect[i] = (cx - half + jitter[0], cy - half + dy + jitter[1], cx + half + jitter[0], cy + half + dy + jitter[1])
		# five-point model: 0 outer left eye, 1 inner left, 2 outer right, 3 inner right, 4 nose tip
		landmarks[i] = [(cx - eye_gap, cy - 20 + dy), (cx - 20, cy - 20 + dy),
		                (cx + eye_gap, cy - 20 + dy), (cx + 20, cy - 20 + dy), (cx, cy + 15 + dy)]
		descriptor[i] = center + rng.normal(scale=SAME_FACE_SIGMA, size=128)
	return dict(t=t, height=HEIGHT, width=WIDTH, dark=dark, faces=face_count, rect=rect,
	            landmarks=landmarks, descriptor=descriptor)


def main(out_dir):
	rng = np.random.default_rng(20260917)
	me = _descriptor_center(rng)
	os.makedirs(out_dir, exist_ok=True)
	for name, clip in (("still", _clip(rng, center=me)),
	                   ("nod", _clip(rng, center=me, nod=True)),
	                   ("away", _clip(rng, faces=False))):
		path = os.path.join(out_dir, name + ".signals.npz")
		np.savez_compressed(path, **clip)
		print("%s: %d frames, %d with a face -> %s (%.0f KB)" % (
			name, len(clip["t"]), int((clip["faces"] == 1).sum()), path, os.path.getsize(path) / 1024))


if __name__ == "__main__":
	main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "fixtures", "footage-synthetic"))
