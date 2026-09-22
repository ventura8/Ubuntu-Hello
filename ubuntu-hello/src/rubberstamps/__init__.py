import sys
import os
import re

from i18n import _

from importlib.machinery import SourceFileLoader


class RubberStamp:
	"""Ubuntu Hello rubber stamp"""

	UI_TEXT = "ui_text"
	UI_SUBTEXT = "ui_subtext"

	# Set by execute(); the challenge is mirrored here because the auth overlay
	# has no display under PAM's minimal environment and is never seen.
	notifier = None

	def set_ui_text(self, text, type=None):
		"""Convert an ui string to input ubuntu-hello-gtk understands"""
		typedec = "M"

		if type == self.UI_SUBTEXT:
			typedec = "S"

		if type == self.UI_SUBTEXT:
			self._ui_subtext = text
		else:
			self._ui_text = text
		self._notify_ui()

		return self.send_ui_raw(typedec + "=" + text)

	def _notify_ui(self):
		"""Mirror the current challenge onto the desktop notification card."""
		notifier = getattr(self, "notifier", None)
		prompt = getattr(self, "_ui_text", "")
		if notifier is None or not prompt:
			return
		try:
			notifier.liveness(prompt, getattr(self, "_ui_subtext", ""))
		except Exception:
			pass   # a card is never allowed to affect the auth result

	def send_ui_raw(self, command):
		"""Write raw command to ubuntu-hello-gtk stdin"""
		if self.config.getboolean("debug", "verbose_stamps", fallback=False):
			print("Sending command to ubuntu-hello-gtk: " + command)

		# Add a newline because the ui reads per line
		command += " \n"

		# If we're connected to the ui
		if self.gtk_proc:
			try:
				# Send the command as bytes
				self.gtk_proc.stdin.write(bytearray(command.encode("utf-8")))
				self.gtk_proc.stdin.flush()

				# Write a padding line to force the command through any buffers
				self.gtk_proc.stdin.write(bytearray("P=_PADDING \n".encode("utf-8")))
				self.gtk_proc.stdin.flush()
			except (OSError, ValueError):
				# The overlay is optional and, under PAM, cannot open a window at
				# all: compare.py is started with an explicit minimal environment
				# (PATH only), so GTK has no display and the process exits, leaving
				# a broken pipe here. The prompt still reaches the user on the
				# notification card; a dead overlay must never fail authentication.
				self.gtk_proc = None


def _installed_stamps(dir_path):
	"""Stamp module names available in the rubberstamps folder."""
	stamps = []
	for filename in os.listdir(dir_path):
		# Skip directories and meta files.
		if not os.path.isfile(dir_path + "/" + filename):
			continue
		if filename in ["__init__.py", ".gitignore"]:
			continue
		stamps.append(filename.split(".")[0])
	return stamps


def _apply_rule_options(instance, raw_options, stamp_type):
	"""Apply the rule's trailing ``key=value`` arguments to *instance*.

	Only keys the stamp declared are accepted, and the declared value's type
	decides how the string is converted.
	"""
	for option in raw_options.split():
		key, value = option.split("=")

		if key not in instance.options:
			print("Unknown config option for rubberstamp " + stamp_type + ": " + key)
			continue

		if isinstance(instance.options[key], int):
			value = int(value)
		elif isinstance(instance.options[key], float):
			value = float(value)

		instance.options[key] = value


def _run_stamp(instance, stamp_type, rule_failsafe, verbose):
	"""Run one stamp and act on its verdict.

	Exits 15 when the stamp denies. A stamp that *crashes* also denies when the
	rule is failsafe: returning here would skip the result check below and fall
	through to sys.exit(0), authenticating the user on a broken liveness check.
	"""
	try:
		result = instance.run()
	except Exception:
		print(_("Internal error in rubberstamp:"))

		import traceback
		traceback.print_exc()
		if rule_failsafe:
			sys.exit(15)
		return

	if verbose: print("Stamp \"" + stamp_type + "\" returned: " + str(result))

	if result is False:
		if verbose: print("Authentication aborted by rubber stamp")
		sys.exit(15)


def execute(config, gtk_proc, opencv, notifier=None):
	verbose = config.getboolean("debug", "verbose_stamps", fallback=False)
	dir_path = os.path.dirname(os.path.realpath(__file__))
	installed_stamps = _installed_stamps(dir_path)

	if verbose: print("Installed rubberstamps: " + ", ".join(installed_stamps))

	# Get the rules defined in the config
	raw_rules = config.get("rubberstamps", "stamp_rules")
	rules = raw_rules.split("\n")

	# Go through the rules one by one
	for rule in rules:
		rule = rule.strip()

		if len(rule) <= 1:
			continue

		# Parse the rule with regex
		regex_result = re.search(r"^(\w+)\s+([\w.]+)\s+([a-z]+)(?![a-z])(.*)$", rule, re.IGNORECASE)

		# Error out if the regex did not match (invalid line)
		if not regex_result:
			print(_("Error parsing rubberstamp rule: {}").format(rule))
			continue

		stamp_type = regex_result.group(1)
		# failsafe (the default) aborts when the check does not pass; faildeadly
		# lets authentication through. Same vocabulary as the config file.
		rule_failsafe = regex_result.group(3).lower() != "faildeadly"

		# Error out if the stamp name in the rule is not a file
		if stamp_type not in installed_stamps:
			print(_("Stamp not installed: {}").format(stamp_type))
			continue

		# Load the module from file
		module = SourceFileLoader(stamp_type, dir_path + "/" + stamp_type + ".py").load_module()

		# Try to get the class with the same name
		try:
			constructor = getattr(module, stamp_type)
		except AttributeError:
			print(_("Stamp error: Class {} not found").format(stamp_type))
			continue

		# Init the class and set common values
		instance = constructor()
		instance.verbose = verbose
		instance.config = config
		instance.gtk_proc = gtk_proc
		instance.notifier = notifier
		instance.opencv = opencv

		# Set some opensv shorthands
		instance.video_capture = opencv["video_capture"]
		instance.face_detector = opencv["face_detector"]
		instance.pose_predictor = opencv["pose_predictor"]
		instance.clahe = opencv["clahe"]

		# Parse and set the 2 required options for all rubberstamps
		instance.options = {
			"timeout": float(re.sub("[a-zA-Z]", "", regex_result.group(2))),
			"failsafe": regex_result.group(3) != "faildeadly"
		}

		# Try to get the class do declare its other config variables
		try:
			instance.declare_config()
		except Exception:
			print(_("Internal error in rubberstamp configuration declaration:"))

			import traceback
			traceback.print_exc()
			continue

		_apply_rule_options(instance, regex_result.group(4), stamp_type)

		if verbose:
			print("Stamp \"" + stamp_type + "\" options parsed:")
			print(instance.options)
			print("Executing stamp")

		_run_stamp(instance, stamp_type, rule_failsafe, verbose)

	# This is outside the for loop, so we've run all the rules
	if verbose: print("All rubberstamps processed, authentication successful")

	# Exit with no errors
	sys.exit(0)
