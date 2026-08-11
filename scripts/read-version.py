#!/usr/bin/env python3
"""Print the Ubuntu Hello version from the repo VERSION file (single source of truth)."""

from __future__ import annotations

import sys
from pathlib import Path


def find_version_file() -> Path:
	starts = [Path.cwd().resolve(), Path(__file__).resolve().parent.parent]
	seen: set[Path] = set()
	for start in starts:
		for candidate in [start, *start.parents]:
			if candidate in seen:
				continue
			seen.add(candidate)
			path = candidate / "VERSION"
			if path.is_file():
				return path
	raise FileNotFoundError("VERSION file not found (searched upward from cwd and repo root)")


def read_version() -> str:
	text = find_version_file().read_text(encoding="utf-8").strip()
	if not text:
		raise ValueError("VERSION file is empty")
	# First line only; ignore comments/blank trailing lines.
	line = text.splitlines()[0].strip()
	if not line or line.startswith("#"):
		raise ValueError("VERSION file missing a version on the first line")
	return line


def main() -> int:
	try:
		print(read_version())
	except (OSError, ValueError) as exc:
		print(f"error: {exc}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
