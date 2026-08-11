"""Unit tests for Settings fuzzy search helper (stdlib only)."""
from search_fuzzy import fuzzy_match, fuzzy_score, is_subsequence


def test_subsequence_basic():
	assert is_subsequence("kyr", "keyring")
	assert is_subsequence("abt", "about")
	assert not is_subsequence("xyz", "about")


def test_exact_and_substring_score_high():
	assert fuzzy_score("keyring", "Keyring") == 1.0
	assert fuzzy_score("key", "Keyring Auto-Unlock") == 1.0


def test_typo_still_matches():
	# SequenceMatcher should accept near matches on short labels
	assert fuzzy_match("langage", "Language")
	assert fuzzy_match("modls", "Models")


def test_empty_query_matches_all():
	assert fuzzy_match("", "anything")
	assert fuzzy_score("", "anything") == 1.0


def test_empty_haystack_and_subsequence_edges():
	assert fuzzy_score("x", "") == 0.0
	assert is_subsequence("", "abc")
	assert not is_subsequence("ab", "a")
	assert fuzzy_match("   ", "Models")  # whitespace-only query → show all
	# Subsequence scoring path
	assert 0.75 <= fuzzy_score("kyr", "keyring") <= 0.99
