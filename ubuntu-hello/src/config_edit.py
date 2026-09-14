"""Edit one option in an INI config file while preserving comments and layout.

configparser.write() drops every comment, and `ubuntu-hello set` matches a bare
key name (ambiguous: [notifications] enabled vs [rubberstamps] enabled), so
the Settings UI uses this section-aware, in-place editor instead.
"""

import os
import re
import tempfile

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _key_re(key):
	return re.compile(r"^\s*%s\s*=" % re.escape(key))


def set_option(path, section, key, value):
	"""Set ``[section] key = value`` in *path*, keeping comments and order.

	The key is replaced in place when present in the section, appended to the
	section otherwise, and the section itself is appended when missing.
	Returns True when the file was written.
	"""
	value = str(value)
	with open(path, "r", encoding="utf-8") as fh:
		lines = fh.read().splitlines(keepends=True)

	out = []
	in_section = False
	section_found = False
	replaced = False
	# True while dropping the indented continuation lines of the key we replaced:
	# an INI value may span lines, and leaving them behind would append a second
	# value to the new one (e.g. two stamp_rules).
	skipping_continuation = False
	key_re = _key_re(key)
	def append_to_section():
		# Insert before the blank lines that separate this section from the next.
		insert_at = len(out)
		while insert_at > 0 and not out[insert_at - 1].strip():
			insert_at -= 1
		out.insert(insert_at, "%s = %s\n" % (key, value))

	for i, line in enumerate(lines):
		if skipping_continuation:
			if line.strip() and line[:1] in (" ", "\t"):
				continue
			skipping_continuation = False
		match = _SECTION_RE.match(line)
		if match:
			if in_section and not replaced:
				append_to_section()
				replaced = True
			in_section = match.group(1).strip() == section
			section_found = section_found or in_section
			out.append(line)
			continue
		if in_section and not replaced and key_re.match(line):
			out.append("%s = %s\n" % (key, value))
			replaced = True
			skipping_continuation = True
			continue
		out.append(line)

	if not replaced:
		if not section_found:
			if out and not out[-1].endswith("\n"):
				out[-1] += "\n"
			out.append("\n[%s]\n" % section)
			out.append("%s = %s\n" % (key, value))
		else:
			append_to_section()

	directory = os.path.dirname(path) or "."
	fd, tmp = tempfile.mkstemp(prefix=".config.", dir=directory)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as fh:
			fh.writelines(out)
		try:
			os.chmod(tmp, os.stat(path).st_mode & 0o777)
		except OSError:
			pass
		os.replace(tmp, path)
	except Exception:
		try:
			os.unlink(tmp)
		except OSError:
			pass
		raise
	return True


def get_bool(path, section, key, default):
	"""Read a boolean option (true/yes/on/1) without configparser strictness."""
	try:
		with open(path, "r", encoding="utf-8") as fh:
			lines = fh.read().splitlines()
	except OSError:
		return default
	in_section = False
	key_re = _key_re(key)
	for line in lines:
		match = _SECTION_RE.match(line)
		if match:
			in_section = match.group(1).strip() == section
			continue
		if in_section and key_re.match(line):
			raw = line.split("=", 1)[1].split("#", 1)[0].split(";", 1)[0].strip().lower()
			return raw in ("1", "true", "yes", "on")
	return default
