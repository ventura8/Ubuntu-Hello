"""User-facing version strings for Settings / setup wizard.

Always prefer repo-root ``VERSION`` (semver SSOT). Never surface older
``git describe`` tags (e.g. ``v1.0.4-N-g…``) in parentheses.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

_SEMVER_RE = re.compile(r"^v?(\d+\.\d+\.\d+)")


def _read_version_file(start_dir: str) -> Optional[str]:
	"""Walk up from *start_dir* looking for repo-root VERSION."""
	cur = os.path.abspath(start_dir)
	for _ in range(8):
		candidate = os.path.join(cur, "VERSION")
		if os.path.isfile(candidate):
			try:
				with open(candidate, encoding="utf-8") as handle:
					line = handle.read().strip().splitlines()[0].strip()
				match = _SEMVER_RE.match(line)
				if match:
					return match.group(1)
			except OSError:
				return None
		parent = os.path.dirname(cur)
		if parent == cur:
			break
		cur = parent
	return None


def _paths_version() -> Optional[str]:
	try:
		import paths

		raw = getattr(paths, "version", None)
		if raw and isinstance(raw, str) and not raw.startswith("@"):
			return raw.strip()
	except Exception:
		pass
	return None


def _semver_from_any(raw: str) -> Optional[str]:
	match = _SEMVER_RE.match(raw.strip())
	return match.group(1) if match else None


def _git_exact_release_tag(base_dir: str, semver: str) -> Optional[bool]:
	"""Return True/False when inside a git work tree; None if git is unavailable."""
	try:
		inside = subprocess.run(
			["git", "-C", base_dir, "rev-parse", "--is-inside-work-tree"],
			capture_output=True,
			text=True,
			check=False,
		)
		if inside.returncode != 0 or inside.stdout.strip() != "true":
			return None
		tag = subprocess.run(
			["git", "-C", base_dir, "describe", "--tags", "--exact-match"],
			capture_output=True,
			text=True,
			check=False,
		)
		if tag.returncode != 0:
			return False
		got = tag.stdout.strip()
		return got in {f"v{semver}", semver}
	except Exception:
		return None


def get_display_version(start_dir: Optional[str] = None) -> str:
	"""Return a UI version string rooted in VERSION — never old git tags."""
	base_dir = start_dir or os.path.dirname(os.path.abspath(__file__))
	paths_ver = _paths_version()
	semver = _read_version_file(base_dir)
	if not semver and paths_ver:
		semver = _semver_from_any(paths_ver)
	if not semver:
		semver = "unknown"

	paths_dev = bool(
		paths_ver
		and (
			"-dev" in paths_ver
			or "dirty" in paths_ver
			or "(Development)" in paths_ver
		)
	)
	# Prefer baked paths.version -dev without spawning git (keeps tests/CI quiet).
	if paths_dev:
		return f"v{semver}-dev"

	exact = _git_exact_release_tag(base_dir, semver)
	if exact is True:
		return f"v{semver}"
	if exact is False:
		return f"v{semver}-dev"
	return f"v{semver}"
