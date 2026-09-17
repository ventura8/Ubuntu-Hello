import time

from i18n import _

# Import the root rubberstamp class
from rubberstamps import RubberStamp


class nod(RubberStamp):
	def declare_config(self):
		"""Set the default values for the optional arguments"""
		self.options["min_distance"] = 6
		self.options["min_directions"] = 2

	# Reset the displacement baseline when the face has been out of view this long:
	# the detector finds a face in only a fraction of the frames on an IR camera, so
	# an old nose position says nothing about where the user is now.
	FACE_GAP_RESET = 0.7

	def run(self):
		"""Track a users nose to see if they nod yes or shake no.

		Displacement is measured from a moving anchor rather than between consecutive
		frames. A frame-to-frame delta depended on how many frames happened to contain
		a detectable face -- roughly a quarter of them on an IR camera -- so the same
		nod registered or not depending on timing, and a slower nod never cleared the
		bar at all. Only the axis that moved furthest in a frame is counted, so a nod
		with a little sideways drift is not read as a head shake, which aborts.
		"""
		self.set_ui_text(_("Nod to confirm"), self.UI_TEXT)
		self.set_ui_text(_("Shake your head to abort"), self.UI_SUBTEXT)

		mindist = self.options["min_distance"]
		needed = self.options["min_directions"]
		# Point each axis measures its displacement from; re-anchored at every
		# direction change so the next leg of the nod is measured from the extreme.
		anchor = {"x": None, "y": None}
		# Contains booleans recording successful nods and their directions
		recorded_nods = {"x": [], "y": []}
		last_seen = None

		starttime = time.time()

		# Keep running the loop while we have not hit timeout yet
		while time.time() < starttime + self.options["timeout"]:
			# Read a frame from the camera
			ret, frame = self.video_capture.read_frame()

			# Apply CLAHE to get a better picture
			frame = self.clahe.apply(frame)

			# Detect all faces in the frame
			face_locations = self.face_detector(frame, 1)

			# Only continue if exactly 1 face is visible in the frame
			if len(face_locations) != 1:
				continue

			now = time.time()
			if last_seen is not None and now - last_seen > self.FACE_GAP_RESET:
				anchor = {"x": None, "y": None}
			last_seen = now

			# Get the position of the eyes and tip of the nose
			face_landmarks = self.pose_predictor(frame, face_locations[0])

			# Distance between the eyes, used to express movement as a fraction of
			# face size so it does not depend on how close the user sits. abs():
			# which eye comes first depends on how the camera mirrors the image, and
			# a negative value here turned the threshold into pure noise.
			eyedist = abs(face_landmarks.part(0).x - face_landmarks.part(2).x) or 1

			moves = {}
			for axis in ["x", "y"]:
				nosepoint = getattr(face_landmarks.part(4), axis)
				if anchor[axis] is None:
					anchor[axis] = nosepoint
				moves[axis] = (nosepoint, (nosepoint - anchor[axis]) * 100 / eyedist)

			# Nodding also moves the nose sideways a little; only the dominant axis
			# counts, otherwise a nod could be recorded as a shake and abort.
			axis = max(moves, key=lambda a: abs(moves[a][1]))
			nosepoint, movement = moves[axis]

			# If the movement is over the minimal distance threshold
			if abs(movement) > mindist:
				direction = movement < 0
				# Only record a change of direction: holding the head down is one leg
				if not recorded_nods[axis] or recorded_nods[axis][-1] != direction:
					recorded_nods[axis].append(direction)
				anchor[axis] = nosepoint

				# Check if we have nodded enough on this axis
				if len(recorded_nods[axis]) >= needed:
					# If nodded yes, show confirmation in ui
					if axis == "y":
						self.set_ui_text(_("Confirmed authentication"), self.UI_TEXT)
					# If shaken no, show abort message
					else:
						self.set_ui_text(_("Aborted authentication"), self.UI_TEXT)

					# Remove subtext
					self.set_ui_text("", self.UI_SUBTEXT)

					# Return true for nodding yes and false for shaking no
					time.sleep(0.8)
					return axis == "y"

		# We've fallen out of the loop, so timeout has been hit
		return not self.options["failsafe"]
