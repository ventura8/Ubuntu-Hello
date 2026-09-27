# Set the disable flag

# Import required modules
import sys
import builtins
import config_edit
import paths_factory

from i18n import _

# Get the absolute filepath
config_path = paths_factory.config_file_path()

# Check if enough arguments have been passed
if not builtins.ubuntu_hello_args.arguments:
	print(_("Please add a 0 (enable) or a 1 (disable) as an argument"))
	sys.exit(1)

# Get the cli argument
argument = builtins.ubuntu_hello_args.arguments[0]

# Translate the argument to the right string
if argument == "1" or argument.lower() == "true":
	out_value = "true"
elif argument == "0" or argument.lower() == "false":
	out_value = "false"
else:
	# Of it's not a 0 or a 1, it's invalid
	print(_("Please only use 0 (enable) or 1 (disable) as an argument"))
	sys.exit(1)

# A missing key means enabled, as in the PAM module (core.disabled defaults to false)
current_value = "true" if config_edit.get_bool(config_path, "core", "disabled", False) else "false"

# Don't do anything when the state is already the requested one
if out_value == current_value:
	print(_("The disable option has already been set to ") + out_value)
	sys.exit(1)

# Section-aware and atomic (temp file + rename): any spelling of the key is
# updated, a missing key is added under [core], comments are kept, and a failed
# write leaves the old config in place rather than an empty file.
config_edit.set_option(config_path, "core", "disabled", out_value)

# Print what we just did
if out_value == "true":
	print(_("Ubuntu Hello has been disabled"))
else:
	print(_("Ubuntu Hello has been enabled"))
