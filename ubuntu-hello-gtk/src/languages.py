# Language codes for Settings → Language combo.
# Babel/CLDR names in the active UI language, plus each language's own name.

from __future__ import annotations

import os
import re
from typing import Optional

# English reference names (Whisper / Whisper-Pro-ASR + English).
LANGUAGE_NAMES = {
	"en": "English",
	"af": "Afrikaans",
	"am": "Amharic",
	"ar": "Arabic",
	"as": "Assamese",
	"az": "Azerbaijani",
	"ba": "Bashkir",
	"be": "Belarusian",
	"bg": "Bulgarian",
	"bn": "Bengali",
	"bo": "Tibetan",
	"br": "Breton",
	"bs": "Bosnian",
	"ca": "Catalan",
	"cs": "Czech",
	"cy": "Welsh",
	"da": "Danish",
	"de": "German",
	"el": "Greek",
	"es": "Spanish",
	"et": "Estonian",
	"eu": "Basque",
	"fa": "Persian",
	"fi": "Finnish",
	"fo": "Faroese",
	"fr": "French",
	"gl": "Galician",
	"gu": "Gujarati",
	"ha": "Hausa",
	"haw": "Hawaiian",
	"he": "Hebrew",
	"hi": "Hindi",
	"hr": "Croatian",
	"ht": "Haitian Creole",
	"hu": "Hungarian",
	"hy": "Armenian",
	"id": "Indonesian",
	"is": "Icelandic",
	"it": "Italian",
	"ja": "Japanese",
	"jw": "Javanese",
	"ka": "Georgian",
	"kk": "Kazakh",
	"km": "Khmer",
	"kn": "Kannada",
	"ko": "Korean",
	"la": "Latin",
	"lb": "Luxembourgish",
	"ln": "Lingala",
	"lo": "Lao",
	"lt": "Lithuanian",
	"lv": "Latvian",
	"mg": "Malagasy",
	"mi": "Maori",
	"mk": "Macedonian",
	"ml": "Malayalam",
	"mn": "Mongolian",
	"mr": "Marathi",
	"ms": "Malay",
	"mt": "Maltese",
	"my": "Myanmar",
	"ne": "Nepali",
	"nl": "Dutch",
	"nn": "Nynorsk",
	"no": "Norwegian",
	"oc": "Occitan",
	"pa": "Punjabi",
	"pl": "Polish",
	"ps": "Pashto",
	"pt": "Portuguese",
	"ro": "Romanian",
	"ru": "Russian",
	"sa": "Sanskrit",
	"sd": "Sindhi",
	"si": "Sinhala",
	"sk": "Slovak",
	"sl": "Slovenian",
	"sn": "Shona",
	"so": "Somali",
	"sq": "Albanian",
	"sr": "Serbian",
	"su": "Sundanese",
	"sv": "Swedish",
	"sw": "Swahili",
	"ta": "Tamil",
	"te": "Telugu",
	"tg": "Tajik",
	"th": "Thai",
	"tk": "Turkmen",
	"tl": "Tagalog",
	"tr": "Turkish",
	"tt": "Tatar",
	"uk": "Ukrainian",
	"ur": "Urdu",
	"uz": "Uzbek",
	"vi": "Vietnamese",
	"yi": "Yiddish",
	"yo": "Yoruba",
	"zh": "Chinese",
}

# Whisper / ISO quirks → Babel / CLDR language ids.
_BABEL_ALIASES = {
	"jw": "jv",
	"tl": "fil",
	# Language-only Chinese (avoid “Simplified, China” territory clutter in the combo).
	"zh": "zh",
	"no": "nb",
	"he": "he",
	"nb": "nb",
	"nn": "nn",
	"iw": "he",
}

COMBO_CODES = ("en",) + tuple(code for code in LANGUAGE_NAMES if code != "en")
WHISPER_CODES = COMBO_CODES


def is_known_language(code: str) -> bool:
	return bool(code) and code in LANGUAGE_NAMES


def _normalize_ui_lang(code: Optional[str]) -> str:
	if not code or code == "auto":
		for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
			raw = os.environ.get(key) or ""
			if raw:
				token = re.split(r"[:\.@_]", raw.strip())[0].lower()
				if token:
					return _BABEL_ALIASES.get(token, token)
		return "en"
	code = code.strip().lower()
	return _BABEL_ALIASES.get(code, code)


def _title_first(name: str) -> str:
	"""Capitalize the first character for combo UX (CLDR often returns lowercase)."""
	if not name:
		return name
	return name[:1].upper() + name[1:] if len(name) > 1 else name.upper()


def _babel_display_name(code: str, in_lang: str) -> Optional[str]:
	"""Language *code*'s name as shown in locale *in_lang*, or None."""
	babel_code = _BABEL_ALIASES.get(code, code)
	try:
		from babel import Locale

		ui = Locale.parse(in_lang)
		name = ui.languages.get(babel_code) or ui.languages.get(code)
		if not name:
			target = Locale.parse(babel_code)
			name = target.get_display_name(in_lang)
		if name:
			return _title_first(name)
	except Exception:
		pass
	return None


def _native_display_name(code: str) -> Optional[str]:
	"""Autonym: the language's name in that language (e.g. German → Deutsch)."""
	babel_code = _BABEL_ALIASES.get(code, code)
	try:
		from babel import Locale

		target = Locale.parse(babel_code)
		name = target.get_display_name(babel_code)
		if name:
			return _title_first(name)
	except Exception:
		pass
	return None


def language_combo_label(code: str, ui_lang: Optional[str] = None) -> str:
	"""Return combo text: UI-language name, plus native name when different.

	Example with Romanian UI: ``Germană (Deutsch)``. When the localized and
	native names match (or native is unavailable), returns only the localized
	name. Falls back to the English catalog name.
	"""
	english = LANGUAGE_NAMES.get(code, code)
	display = _normalize_ui_lang(ui_lang)
	localized = _babel_display_name(code, display) or english
	native = _native_display_name(code)
	if native and native.casefold() != localized.casefold():
		return f"{localized} ({native})"
	return localized
