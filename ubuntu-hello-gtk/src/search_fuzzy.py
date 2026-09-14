# Dependency-free fuzzy match for Settings search (stdlib only).
from __future__ import annotations

import difflib
import unicodedata

# Ratio threshold for SequenceMatcher on short Settings labels.
_SEQUENCE_RATIO = 0.55


def fold(text: str) -> str:
	"""Casefold and strip diacritics so "notificatii" finds "Notificații" and "Deutsch" finds "Dèutsch"."""
	decomposed = unicodedata.normalize("NFKD", text or "")
	return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def is_subsequence(query: str, haystack: str) -> bool:
	"""True if all query chars appear in order in haystack (both casefolded)."""
	if not query:
		return True
	qi = 0
	for ch in haystack:
		if ch == query[qi]:
			qi += 1
			if qi >= len(query):
				return True
	return False


def fuzzy_score(query: str, haystack: str) -> float:
	"""Return match score in [0, 1]. Empty query → 1.0 (show all)."""
	q = fold((query or "").strip())
	h = fold(haystack)
	if not q:
		return 1.0
	if not h:
		return 0.0
	if q in h:
		return 1.0
	if is_subsequence(q, h):
		# Prefer longer relative coverage for subsequence hits.
		return max(0.75, min(0.99, len(q) / max(len(h), 1)))
	return difflib.SequenceMatcher(None, q, h).ratio()


def fuzzy_match(query: str, haystack: str, threshold: float = _SEQUENCE_RATIO) -> bool:
	"""True if query fuzzy-matches haystack at or above threshold."""
	q = (query or "").strip()
	if not q:
		return True
	return fuzzy_score(q, haystack) >= threshold
