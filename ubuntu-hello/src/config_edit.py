"""Edit one option in an INI config file while preserving comments and layout.

configparser.write() drops every comment, and `ubuntu-hello set` matches a bare
key name (ambiguous: [notifications] enabled vs [rubberstamps] enabled), so
the Settings UI uses this section-aware, in-place editor instead.
"""

import os
import re
import tempfile

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


_KEY_LINE = "%s = %s\n"

def _key_re(key):
	return re.compile(r"^\s*%s\s*=" % re.escape(key))


def _insert_before_trailing_blanks(out, new_line):
	"""Insert *new_line* before the blank lines separating this section from the next."""
	insert_at = len(out)
	while insert_at > 0 and not out[insert_at - 1].strip():
		insert_at -= 1
	out.insert(insert_at, new_line)


def _is_continuation(line):
	"""True for an indented continuation of the previous key's value."""
	return bool(line.strip()) and line[:1] in (" ", "\t")


def _replace_in_place(lines, section, key, value):
	"""Replace ``key`` inside ``[section]``.

	Returns ``(out, replaced, section_found)``. When the section ends before the
	key is found, the key is appended to it and *replaced* is True.
	"""
	out = []
	in_section = False
	section_found = False
	replaced = False
	# True while dropping the indented continuation lines of the key we replaced:
	# an INI value may span lines, and leaving them behind would append a second
	# value to the new one (e.g. two stamp_rules).
	skipping_continuation = False
	key_re = _key_re(key)

	for line in lines:
		if skipping_continuation:
			if _is_continuation(line):
				continue
			skipping_continuation = False

		match = _SECTION_RE.match(line)
		if match:
			# Leaving our section without having found the key: append it here.
			if in_section and not replaced:
				_insert_before_trailing_blanks(out, _KEY_LINE % (key, value))
				replaced = True
			in_section = match.group(1).strip() == section
			section_found = section_found or in_section
		elif in_section and not replaced and key_re.match(line):
			out.append(_KEY_LINE % (key, value))
			replaced = True
			skipping_continuation = True
			continue

		out.append(line)

	return out, replaced, section_found


def _rewrite_lines(lines, section, key, value):
	"""Return *lines* with ``[section] key`` set to *value*.

	Replaced in place when the key is present in the section, appended to the
	section otherwise, and the section itself appended when missing.
	"""
	out, replaced, section_found = _replace_in_place(lines, section, key, value)
	if replaced:
		return out

	if section_found:
		_insert_before_trailing_blanks(out, _KEY_LINE % (key, value))
		return out

	if out and not out[-1].endswith("\n"):
		out[-1] += "\n"
	out.append("\n[%s]\n" % section)
	out.append(_KEY_LINE % (key, value))
	return out


def _write_atomically(path, lines):
	"""Replace *path* with *lines*, preserving its mode. Never leaves a temp file."""
	directory = os.path.dirname(path) or "."
	fd, tmp = tempfile.mkstemp(prefix=".config.", dir=directory)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as fh:
			fh.writelines(lines)
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


def set_option(path, section, key, value):
	"""Set ``[section] key = value`` in *path*, keeping comments and order.

	Returns True when the file was written.
	"""
	with open(path, "r", encoding="utf-8") as fh:
		lines = fh.read().splitlines(keepends=True)

	_write_atomically(path, _rewrite_lines(lines, section, key, str(value)))
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
