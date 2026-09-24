"""Guided multi-sample capture used by `ubuntu-hello add` (enroll_capture.py).

A face model used to be one descriptor from the first frame with a face, so a
small head or exposure change at login landed just past the certainty
threshold (near-miss 3.37-3.6 vs 3.5 on real hardware). The capture now walks
the user through small head movements and stores several descriptors.
"""
import numpy as np

import enroll_capture as ec


class FakeClock:
	def __init__(self):
		self.t = 0.0

	def __call__(self):
		return self.t

	def advance(self, dt):
		self.t += dt


def _run(frames, clock, detect=None, encode=None, **kw):
	"""frames: list of (frame_id, gs) items consumed in order; the clock advances 0.1 s per read."""
	it = iter(frames)
	lines = []

	def read_frame():
		clock.advance(0.1)
		try:
			return next(it)
		except StopIteration:
			return ("blank", None)

	detect = detect or (lambda gs: [gs] if gs is not None else [])
	encode = encode or (lambda frame, loc: [float(frame)] * 3)
	samples = ec.capture_guided_samples(read_frame, detect, encode, lines.append, [0.0, 0.0, 0.0], clock=clock, **kw)
	return samples, lines


def test_guide_steps_cover_center_left_right_up_down_in_order():
	keys = [k for k, _ in ec.GUIDE_STEPS]
	assert keys[0] == "center"
	assert keys[-1] == "center"
	assert keys[1:5] == ["left", "right", "up", "down"]
	assert all(prompt for _, prompt in ec.GUIDE_STEPS)
	assert ec.MAX_SAMPLES == 1 + len(ec.GUIDE_STEPS) * ec.SAMPLES_PER_STEP


def test_protocol_lines_are_machine_readable():
	assert ec.guide_line("left") == "@guide left"
	assert ec.progress_line(3, 13) == "@progress 3/13"


def test_is_duplicate_uses_euclidean_distance():
	assert ec.is_duplicate([0.0, 0.0], [], np) is False
	assert ec.is_duplicate([0.0, 0.01], [[0.0, 0.0]], np) is True
	assert ec.is_duplicate([0.0, 0.5], [[0.0, 0.0]], np) is False


def test_frame_darkness_uses_first_of_eight_bins_and_handles_empty():
	class FakeCV2:
		@staticmethod
		def calcHist(images, channels, mask, hist_size, ranges):
			return np.array([[25.0], [75.0], [0], [0], [0], [0], [0], [0]])

	assert ec.frame_darkness(None, np, FakeCV2) == 25.0

	class EmptyCV2:
		@staticmethod
		def calcHist(*a):
			return np.zeros((8, 1))

	assert ec.frame_darkness(None, np, EmptyCV2) is None


def test_collects_samples_per_step_and_reports_progress():
	clock = FakeClock()
	# Plenty of distinct frames: each frame id becomes a distinct descriptor.
	frames = [(i, "gs%d" % i) for i in range(1, 200)]
	samples, lines = _run(frames, clock, settle_seconds=0.2, step_seconds=1.0)
	assert len(samples) == ec.MAX_SAMPLES
	assert samples[0] == [0.0, 0.0, 0.0]
	guides = [l for l in lines if l.startswith("@guide ")]
	assert guides == ["@guide " + k for k, _ in ec.GUIDE_STEPS]
	progress = [l for l in lines if l.startswith("@progress ")]
	assert progress[0] == "@progress 1/%d" % ec.MAX_SAMPLES
	assert progress[-1] == "@progress %d/%d" % (ec.MAX_SAMPLES, ec.MAX_SAMPLES)
	# every prompt is emitted as plain text right after its @guide line
	for k, prompt in ec.GUIDE_STEPS:
		assert prompt in lines


def test_settle_phase_discards_frames_so_capture_never_sees_stale_ones():
	clock = FakeClock()
	frames = [(i, "gs%d" % i) for i in range(1, 200)]
	seen = []

	def detect(gs):
		seen.append(gs)
		return [gs]

	_run(frames, clock, detect=detect, settle_seconds=0.5, step_seconds=0.3, steps=ec.GUIDE_STEPS[:1])
	# 5 frames (0.5 s at 0.1 s each) were read during settle and never detected on
	assert seen
	assert seen[0] == "gs6"


def test_step_without_usable_frame_is_skipped_not_fatal():
	clock = FakeClock()
	frames = [("dark", None)] * 500
	samples, lines = _run(frames, clock, settle_seconds=0.1, step_seconds=0.3)
	assert samples == [[0.0, 0.0, 0.0]]
	assert lines.count("@progress 1/%d" % ec.MAX_SAMPLES) == 1


def test_frames_with_two_faces_are_ignored():
	clock = FakeClock()
	frames = [(i, "gs%d" % i) for i in range(1, 100)]
	samples, _ = _run(frames, clock, detect=lambda gs: ["a", "b"], settle_seconds=0.1, step_seconds=0.3)
	assert len(samples) == 1


def test_near_identical_descriptors_are_not_stored_twice():
	clock = FakeClock()
	frames = [(1, "gs")] * 300  # every frame encodes to the same vector
	samples, _ = _run(frames, clock, settle_seconds=0.1, step_seconds=0.3)
	assert samples == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]


def test_max_samples_caps_the_model_size():
	clock = FakeClock()
	frames = [(i, "gs%d" % i) for i in range(1, 300)]
	samples, lines = _run(frames, clock, settle_seconds=0.1, step_seconds=1.0, max_samples=4)
	assert len(samples) == 4
	assert lines[0] == "@progress 1/4"
	assert sum(1 for l in lines if l.startswith("@guide ")) <= 3


def test_flatten_models_maps_every_descriptor_back_to_its_model():
	"""Regression: with several descriptors per model, argmin over the flat list is not a model index
	(compare.py crashed with IndexError on the end report / success notification label)."""
	models = [
		{"id": 0, "label": "Setup lighting 1", "data": [[0.1] * 3, [0.2] * 3, [0.3] * 3]},
		{"id": 2, "label": "Glasses", "data": [[0.4] * 3]},
		{"id": 3, "label": "empty", "data": []},
	]
	encodings, owners = ec.flatten_models(models)
	assert encodings == [[0.1] * 3, [0.2] * 3, [0.3] * 3, [0.4] * 3]
	assert owners == [0, 0, 0, 1]
	assert models[owners[3]]["label"] == "Glasses"
	assert ec.flatten_models([]) == ([], [])


def test_compare_and_test_cli_use_model_owner_mapping():
	from pathlib import Path
	root = Path(__file__).resolve().parents[1] / "ubuntu-hello" / "src"
	for rel in ("compare.py", "cli/test.py"):
		text = (root / rel).read_text(encoding="utf-8")
		assert "enroll_capture.flatten_models(models)" in text, rel
		assert "models[match_index]" not in text, rel
