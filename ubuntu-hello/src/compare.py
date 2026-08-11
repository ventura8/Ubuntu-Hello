# Compare incoming video with known faces
# Running in a local python instance to get around PATH issues

# Import time so we can start timing asap
import time

# Start timing
timings = {
	"st": time.time()
}

# Import required modules
import sys
import os
import signal
# Set secure umask for all created files/directories (0o077 ensures only owner has access)
os.umask(0o077)

import json
import configparser
import dlib
import cv2
from datetime import timezone, datetime
import atexit
import subprocess
import snapshot
import numpy as np
import _thread as thread
import paths_factory
from recorders.video_capture import VideoCapture
from i18n import _

# Tracked so cleanup can release the camera even on signal abort
video_capture = None

_cleaned_up = False


def install_parent_death_signal(sig=signal.SIGTERM):
	"""Ask the kernel to signal us if the PAM parent exits (Esc/cancel).

	Prevents an orphaned compare from holding the camera on the lock shield
	when gdm-session-worker dies without a clean PAM return.
	"""
	try:
		import ctypes

		libc = ctypes.CDLL("libc.so.6", use_errno=True)
		# PR_SET_PDEATHSIG == 1 on Linux
		return libc.prctl(1, sig) == 0
	except Exception:
		return False

def _recognition_timeout_kind(now, loop_start, scan_start, timeout, acquisition_timeout):
    """Decide whether the compare loop should stop for time.

    Recognition time (*timeout*) starts only after the first usable
    (non-black, non-dark) frame so IR warm-up does not consume the whole
    window. *acquisition_timeout* is a hard cap from loop start when no
    usable frame ever arrives.

    Returns ``None``, ``\"recognition\"``, or ``\"acquisition\"``.
    """
    if scan_start is None:
        if now - loop_start >= acquisition_timeout:
            return "acquisition"
        return None
    if now - scan_start >= timeout:
        return "recognition"
    return None


def cleanup():
	"""Release the camera and terminate the auth UI if still running"""
	global video_capture, gtk_proc, _cleaned_up

	if _cleaned_up:
		return
	_cleaned_up = True

	# Release the camera if we opened one (safe if missing/already released)
	if video_capture is not None:
		try:
			video_capture.release()
		except Exception:
			pass
		video_capture = None
		# Brief settle so the next PAM attempt can reopen the V4L device.
		try:
			time.sleep(0.35)
		except Exception:
			pass

	# Exit the auth ui process if there is one
	if "gtk_proc" in globals() and gtk_proc is not None:
		try:
			gtk_proc.terminate()
			try:
				gtk_proc.wait(timeout=1)
			except subprocess.TimeoutExpired:
				gtk_proc.kill()
				gtk_proc.wait(timeout=1)
		except Exception:
			pass


def exit(code=None):
	"""Exit while releasing the camera and closing ubuntu-hello-gtk properly"""
	cleanup()

	# Exit compare
	if code is not None:
		sys.exit(code)


def _signal_exit(signum, frame):
	"""Handle SIGTERM/SIGINT: same cleanup as exit, then abort with code 12"""
	cleanup()
	os._exit(12)


def init_detector(lock):
	"""Start face detector, encoder and predictor in a new thread"""
	global face_detector, pose_predictor, face_encoder

	# Test if at lest 1 of the data files is there and abort if it's not
	if not os.path.isfile(paths_factory.shape_predictor_5_face_landmarks_path()):
		print(_("Data files have not been downloaded, please run the following commands:"))
		print("\n\tcd " + paths_factory.dlib_data_dir_path())
		print("\tsudo ./install.sh\n")
		lock.release()
		exit(1)

	# Use the CNN detector if enabled
	if use_cnn:
		face_detector = dlib.cnn_face_detection_model_v1(paths_factory.mmod_human_face_detector_path())
	else:
		face_detector = dlib.get_frontal_face_detector()

	# Start the others regardless
	pose_predictor = dlib.shape_predictor(paths_factory.shape_predictor_5_face_landmarks_path())
	face_encoder = dlib.face_recognition_model_v1(paths_factory.dlib_face_recognition_resnet_model_v1_path())

	# Note the time it took to initialize detectors
	timings["ll"] = time.time() - timings["ll"]
	lock.release()


def make_snapshot(type):
	"""Generate snapshot after detection"""
	scan_anchor = timings.get("fr") or timings.get("loop") or time.time()
	scan_elapsed = max(time.time() - scan_anchor, 0.001)
	snapshot.generate(snapframes, [
		type + _(" LOGIN"),
		_("Date: ") + datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S UTC"),
		_("Scan time: ") + str(round(scan_elapsed, 2)) + "s",
		_("Frames: ") + str(frames) + " (" + str(round(frames / scan_elapsed, 2)) + "FPS)",
		_("Hostname: ") + os.uname().nodename,
		_("Best certainty value: ") + str(round(lowest_certainty * 10, 1))
	])


def send_to_ui(type, message):
	"""Send message to the auth ui"""
	global gtk_proc

	# Only execute of the process started
	if "gtk_proc" in globals():
		# Format message so the ui can parse it
		message = type + "=" + message + " \n"

		# Try to send the message to the auth ui, but it's okay if that fails
		try:
			if gtk_proc.poll() is None: # Make sure the gtk_proc is still running before write into the pipe
				gtk_proc.stdin.write(bytearray(message.encode("utf-8")))
				gtk_proc.stdin.flush()
		except IOError:
			pass


if __name__ == "__main__":
	# Die with the PAM parent if Esc/cancel kills the greeter worker mid-auth.
	install_parent_death_signal()

	# Make sure we were given an username to test against
	if len(sys.argv) < 2:
		exit(12)

	# The username of the user being authenticated
	user = sys.argv[1]

	# Validate username format to prevent path traversal or malicious inputs
	import re
	if not re.match(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*\$?$", user):
		print("Invalid username format")
		exit(12)
	# The model file contents
	models = []
	# Encoded face models
	encodings = []
	# Amount of ignored 100% black frames
	black_tries = 0
	# Amount of ignored dark frames
	dark_tries = 0
	# Total amount of frames captured
	frames = 0
	# Captured frames for snapshot capture
	snapframes = []
	# Tracks the lowest certainty value in the loop
	lowest_certainty = 10
	# Face recognition/detection instances
	face_detector = None
	pose_predictor = None
	face_encoder = None

	# Try to load the face model from the models folder
	try:
		models = json.load(open(paths_factory.user_model_path(user)))

		for model in models:
			encodings += model["data"]
	except FileNotFoundError:
		exit(10)

	# Check if the file contains a model
	if len(models) < 1:
		exit(10)

	# Read config from disk
	config = configparser.ConfigParser()
	config.read(paths_factory.config_file_path())

	# Get all config values needed
	use_cnn = config.getboolean("core", "use_cnn", fallback=False)
	timeout = config.getint("video", "timeout", fallback=8)
	dark_threshold = config.getfloat("video", "dark_threshold", fallback=50.0)
	video_certainty = config.getfloat("video", "certainty", fallback=3.5) / 10
	end_report = config.getboolean("debug", "end_report", fallback=False)
	save_failed = config.getboolean("snapshots", "save_failed", fallback=False)
	save_successful = config.getboolean("snapshots", "save_successful", fallback=False)
	gtk_stdout = config.getboolean("debug", "gtk_stdout", fallback=False)
	rotate = config.getint("video", "rotate", fallback=0)

	# Send the gtk output to the terminal if enabled in the config
	gtk_pipe = sys.stdout if gtk_stdout else subprocess.DEVNULL

	# Start the auth ui, register it to be always be closed on exit
	try:
		gtk_proc = subprocess.Popen(["ubuntu-hello-gtk", "--start-auth-ui"], stdin=subprocess.PIPE, stdout=gtk_pipe, stderr=gtk_pipe)
		atexit.register(exit)
	except FileNotFoundError:
		pass

	# Ensure SIGTERM/SIGINT release the camera and tear down GTK (atexit does not run on SIGTERM)
	signal.signal(signal.SIGTERM, _signal_exit)
	signal.signal(signal.SIGINT, _signal_exit)

	# Write to the stdin to redraw ui
	send_to_ui("M", _("Starting up..."))

	# Save the time needed to start the script
	timings["in"] = time.time() - timings["st"]

	# Import face recognition, takes some time
	timings["ll"] = time.time()

	# Start threading and wait for init to finish
	lock = thread.allocate_lock()
	lock.acquire()
	thread.start_new_thread(init_detector, (lock, ))

	# Start video capture on the IR camera
	timings["ic"] = time.time()

	video_capture = VideoCapture(config)

	# Read exposure from config to use in the main loop
	exposure = config.getint("video", "exposure", fallback=-1)

	# Note the time it took to open the camera
	timings["ic"] = time.time() - timings["ic"]

	# wait for thread to finish
	lock.acquire()
	lock.release()
	del lock

	# Fetch the max frame height
	max_height = config.getfloat("video", "max_height", fallback=320.0)

	# Get the height of the image (which would be the width if screen is portrait oriented)
	height = video_capture.internal.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1
	if rotate == 2:
		height = video_capture.internal.get(cv2.CAP_PROP_FRAME_WIDTH) or 1
	# Calculate the amount the image has to shrink
	scaling_factor = (max_height / height) or 1

	# Fetch config settings out of the loop
	timeout = config.getint("video", "timeout", fallback=8)
	# Extra wall time to wait for the first usable frame (IR warm-up). Negative
	# means derive from timeout so dark/black frames do not burn the scan window.
	acquisition_timeout = config.getfloat("video", "acquisition_timeout", fallback=-1.0)
	if acquisition_timeout < 0:
		acquisition_timeout = max(float(timeout) * 2.0, float(timeout) + 6.0)
	dark_threshold = config.getfloat("video", "dark_threshold", fallback=60)
	end_report = config.getboolean("debug", "end_report", fallback=False)

	# Initiate histogram equalization
	clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

	# Let the ui know that we're ready
	send_to_ui("M", _("Identifying you..."))

	# Start the read loop. Recognition timeout starts on the first usable frame.
	frames = 0
	valid_frames = 0
	timings["loop"] = time.time()
	timings["fr"] = None
	dark_running_total = 0

	while True:
		# Increment the frame count every loop
		frames += 1

		# Form a string to let the user know we're real busy
		ui_subtext = "Scanned " + str(valid_frames - dark_tries) + " frames"
		if (dark_tries > 1):
			ui_subtext += " (skipped " + str(dark_tries) + " dark frames)"
		# Show it in the ui as subtext
		send_to_ui("S", ui_subtext)

		# Stop if we've exceeded the time limit (recognition clock starts only
		# after the first non-black/non-dark frame so warm-up does not consume it)
		timeout_kind = _recognition_timeout_kind(
			time.time(),
			timings["loop"],
			timings["fr"],
			float(timeout),
			float(acquisition_timeout),
		)
		if timeout_kind is not None:
			# Create a timeout snapshot if enabled
			if save_failed:
				make_snapshot(_("FAILED"))

			if timeout_kind == "acquisition" or dark_tries == valid_frames:
				print(_("All frames were too dark, please check dark_threshold in config"))
				print(_("Average darkness: {avg}, Threshold: {threshold}").format(avg=str(dark_running_total / max(1, valid_frames)), threshold=str(dark_threshold)))
				exit(13)
			else:
				exit(11)

		# Grab a single frame of video
		frame, gsframe = video_capture.read_frame()
		gsframe = clahe.apply(gsframe)

		# If snapshots have been turned on
		if save_failed or save_successful:
			# Start capturing frames for the snapshot
			if len(snapframes) < 3:
				snapframes.append(frame)

		# Create a histogram of the image with 8 values
		hist = cv2.calcHist([gsframe], [0], None, [8], [0, 256])
		# All values combined for percentage calculation
		hist_total = np.sum(hist)

		# Calculate frame darkness
		darkness = (hist[0] / hist_total * 100)

		# If the image is fully black due to a bad camera read,
		# skip to the next frame
		if (hist_total == 0) or (darkness == 100):
			black_tries += 1
			continue

		dark_running_total += darkness
		valid_frames += 1

		# If the image exceeds darkness threshold due to subject distance,
		# skip to the next frame
		if (darkness > dark_threshold):
			dark_tries += 1
			continue

		# First usable frame: start the recognition timeout window
		if timings["fr"] is None:
			timings["fr"] = time.time()

		# If the height is too high
		if scaling_factor != 1:
			# Apply that factor to the frame
			frame = cv2.resize(frame, None, fx=scaling_factor, fy=scaling_factor, interpolation=cv2.INTER_AREA)
			gsframe = cv2.resize(gsframe, None, fx=scaling_factor, fy=scaling_factor, interpolation=cv2.INTER_AREA)

		# If camera is configured to rotate = 1, check portrait in addition to landscape
		if rotate == 1:
			if frames % 3 == 1:
				frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
				gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_COUNTERCLOCKWISE)
			if frames % 3 == 2:
				frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
				gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_CLOCKWISE)

		# If camera is configured to rotate = 2, check portrait orientation
		elif rotate == 2:
			if frames % 2 == 0:
				frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
				gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_COUNTERCLOCKWISE)
			else:
				frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
				gsframe = cv2.rotate(gsframe, cv2.ROTATE_90_CLOCKWISE)

		# Get all faces from that frame as encodings
		# Upsamples 1 time
		face_locations = face_detector(gsframe, 1)
		# Loop through each face
		for fl in face_locations:
			if use_cnn:
				fl = fl.rect

			# Fetch the faces in the image
			face_landmark = pose_predictor(frame, fl)
			face_encoding = np.array(face_encoder.compute_face_descriptor(frame, face_landmark, 1))

			# Match this found face against a known face
			matches = np.linalg.norm(encodings - face_encoding, axis=1)

			# Get best match
			match_index = np.argmin(matches)
			match = matches[match_index]

			# Update certainty if we have a new low
			if lowest_certainty > match:
				lowest_certainty = match

			# Check if a match that's confident enough
			if 0 < match < video_certainty:
				timings["tt"] = time.time() - timings["st"]
				timings["fl"] = time.time() - (timings["fr"] or timings["loop"])

				# If set to true in the config, print debug text
				if end_report:
					def print_timing(label, k):
						"""Helper function to print a timing from the list"""
						print("  %s: %dms" % (label, round(timings[k] * 1000)))

					# Print a nice timing report
					print(_("Time spent"))
					print_timing(_("Starting up"), "in")
					print(_("  Open cam + load libs: %dms") % (round(max(timings["ll"], timings["ic"]) * 1000, )))
					print_timing(_("  Opening the camera"), "ic")
					print_timing(_("  Importing recognition libs"), "ll")
					print_timing(_("Searching for known face"), "fl")
					print_timing(_("Total time"), "tt")

					print(_("\nResolution"))
					width = video_capture.fw or 1
					print(_("  Native: %dx%d") % (height, width))
					# Save the new size for diagnostics
					scale_height, scale_width = frame.shape[:2]
					print(_("  Used: %dx%d") % (scale_height, scale_width))

					# Show the total number of frames and calculate the FPS by dividing it by the total scan time
					print(_("\nFrames searched: %d (%.2f fps)") % (frames, frames / timings["fl"]))
					print(_("Black frames ignored: %d ") % (black_tries, ))
					print(_("Dark frames ignored: %d ") % (dark_tries, ))
					print(_("Certainty of winning frame: %.3f") % (match * 10, ))

					print(_("Winning model: %d (\"%s\")") % (match_index, models[match_index]["label"]))

				# Make snapshot if enabled
				if save_successful:
					make_snapshot(_("SUCCESSFUL"))

				# Run rubberstamps if enabled
				if config.getboolean("rubberstamps", "enabled", fallback=False):
					import rubberstamps

					send_to_ui("S", "")

					if "gtk_proc" not in vars():
						gtk_proc = None

					rubberstamps.execute(config, gtk_proc, {
						"video_capture": video_capture,
						"face_detector": face_detector,
						"pose_predictor": pose_predictor,
						"clahe": clahe
					})

				# End peacefully
				exit(0)

		if exposure != -1:
			# For a strange reason on some cameras (e.g. Lenoxo X1E) setting manual exposure works only after a couple frames
			# are captured and even after a delay it does not always work. Setting exposure at every frame is reliable though.
			video_capture.internal.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)  # 1 = Manual
			video_capture.internal.set(cv2.CAP_PROP_EXPOSURE, float(exposure))
