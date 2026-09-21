#!/usr/bin/env python3
"""Apply scripts/i18n_fill_data/<domain>/<lang>.json onto .po files (from .pot structure)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scripts" / "i18n_fill_data"


def project_version() -> str:
	"""Read the repo-root VERSION file (single source of truth)."""
	return (ROOT / "VERSION").read_text(encoding="utf-8").strip().splitlines()[0].strip()


DOMAINS = {
	"ubuntu-hello": {
		"podir": ROOT / "ubuntu-hello" / "po",
		"pot": ROOT / "ubuntu-hello" / "po" / "ubuntu-hello.pot",
	},
	"ubuntu-hello-gtk": {
		"podir": ROOT / "ubuntu-hello-gtk" / "po",
		"pot": ROOT / "ubuntu-hello-gtk" / "po" / "ubuntu-hello-gtk.pot",
	},
}


# gettext plural rules per language (GNU gettext's tables, CLDR where gettext is
# silent). Written into every catalogue header: without Plural-Forms, Python's
# gettext falls back to the Germanic n != 1 with two forms and silently ignores a
# third or fourth msgstr. The number of forms here is what the fill data must supply.
_ONE = "nplurals=1; plural=0;"
_TWO = "nplurals=2; plural=(n != 1);"
_TWO_ZERO_SINGULAR = "nplurals=2; plural=(n > 1);"
_EAST_SLAVIC = (
	"nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
	"n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
)
PLURAL_FORMS = {
	**{lang: _ONE for lang in ("ba", "bo", "id", "ja", "jw", "km", "ko", "lo", "ms", "my", "su", "th", "tt", "vi", "yo", "zh")},
	**{lang: _TWO for lang in (
		"af", "az", "bg", "ca", "da", "de", "el", "es", "et", "eu", "fi", "fo", "gl", "ha", "haw", "he",
		"hu", "it", "ka", "kk", "la", "lb", "ml", "mn", "ne", "nl", "nn", "no", "ps", "pt", "sd", "sn",
		"so", "sq", "sv", "sw", "ta", "te", "tg", "tk", "tr", "ur", "uz", "yi",
	)},
	**{lang: _TWO_ZERO_SINGULAR for lang in (
		"am", "as", "bn", "br", "fa", "fr", "gu", "hi", "ht", "hy", "kn", "ln", "mg", "mi", "mr", "oc",
		"pa", "si", "tl",
	)},
	**{lang: _EAST_SLAVIC for lang in ("be", "bs", "hr", "ru", "sr", "uk")},
	"ar": (
		"nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
		"n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);"
	),
	"cs": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
	"cy": "nplurals=4; plural=(n==1) ? 0 : (n==2) ? 1 : (n != 8 && n != 11) ? 2 : 3;",
	"is": "nplurals=2; plural=(n%10!=1 || n%100==11);",
	"lt": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && (n%100<10 || n%100>=20) ? 1 : 2);",
	"lv": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n != 0 ? 1 : 2);",
	"mk": "nplurals=2; plural=(n==1 || n%10==1 ? 0 : 1);",
	"mt": (
		"nplurals=4; plural=(n==1 ? 0 : n==0 || ( n%100>1 && n%100<11) ? 1 : "
		"(n%100>10 && n%100<20 ) ? 2 : 3);"
	),
	"pl": "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
	"ro": "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);",
	"sa": "nplurals=3; plural=(n==1 ? 0 : n==2 ? 1 : 2);",
	"sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
	"sl": "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);",
}


def nplurals(lang: str) -> int:
	"""How many msgstr forms a plural entry needs in this language's catalogue."""
	rule = PLURAL_FORMS[lang]
	return int(rule.split("nplurals=", 1)[1].split(";", 1)[0])


def unescape(s: str) -> str:
	out = []
	i = 0
	while i < len(s):
		if s[i] == "\\" and i + 1 < len(s):
			n = s[i + 1]
			mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
			if n in mapping:
				out.append(mapping[n])
				i += 2
				continue
			if n == "x" and i + 3 < len(s):
				out.append(chr(int(s[i + 2 : i + 4], 16)))
				i += 4
				continue
			out.append(n)
			i += 2
			continue
		out.append(s[i])
		i += 1
	return "".join(out)


def escape(s: str) -> str:
	return (
		s.replace("\\", "\\\\")
		.replace('"', '\\"')
		.replace("\n", "\\n")
		.replace("\t", "\\t")
		.replace("\r", "\\r")
	)


def parse_pot(path: Path):
	text = path.read_text(encoding="utf-8")
	# A plural entry carries msgid_plural and msgstr[0..n]; a singular one a bare
	# msgstr. Both are matched, so an ngettext() string is a key like any other
	# (it used to fall through this pattern and reach no fill pack at all).
	pattern = re.compile(
		r'(?:^msgctxt\s+"(?P<ctxt>(?:[^"\\]|\\.)*)"\s*\n)?'
		r'^msgid\s+"(?P<id0>(?:[^"\\]|\\.)*)"\s*\n'
		r'(?P<idmore>(?:^"(?:[^"\\]|\\.)*"\s*\n)*)'
		r'(?:^msgid_plural\s+"(?P<plural0>(?:[^"\\]|\\.)*)"\s*\n'
		r'(?P<pluralmore>(?:^"(?:[^"\\]|\\.)*"\s*\n)*))?'
		r'^msgstr(?:\[\d+\])?\s+"(?:[^"\\]|\\.)*"\s*\n'
		r'(?:^(?:msgstr\[\d+\]\s+)?"(?:[^"\\]|\\.)*"\s*\n)*',
		re.M,
	)
	entries = []
	for m in pattern.finditer(text):
		parts = [m.group("id0")] + re.findall(r'^"(.*)"\s*$', m.group("idmore") or "", re.M)
		msgid = unescape("".join(parts))
		if msgid == "":
			continue
		msgctxt = unescape(m.group("ctxt")) if m.group("ctxt") is not None else None
		msgid_plural = None
		if m.group("plural0") is not None:
			plural_parts = [m.group("plural0")] + re.findall(r'^"(.*)"\s*$', m.group("pluralmore") or "", re.MULTILINE)
			msgid_plural = unescape("".join(plural_parts))
		# Preserve flags / references from the block before msgid
		# Look backwards for #: and #, lines
		start = m.start()
		block_start = text.rfind("\n\n", 0, start)
		prefix = text[block_start + 2 if block_start >= 0 else 0 : start]
		entries.append({
			"prefix": prefix.strip("\n") + ("\n" if prefix.strip() else ""),
			"msgctxt": msgctxt,
			"msgid": msgid,
			"msgid_plural": msgid_plural,
		})
	return entries


def plural_forms_ok(value, lang: str) -> bool:
	"""A plural translation is a list of exactly nplurals(lang) non-empty strings."""
	return (
		isinstance(value, list)
		and len(value) == nplurals(lang)
		and all(isinstance(form, str) and form for form in value)
	)


def translation_missing(entry, trans: dict, lang: str) -> bool:
	value = trans.get(key_for(entry["msgctxt"], entry["msgid"]), trans.get(entry["msgid"], ""))
	if entry.get("msgid_plural") is not None:
		return not plural_forms_ok(value, lang)
	return not (isinstance(value, str) and value)


def format_string_field(tag: str, value: str) -> str:
	esc = escape(value)
	if tag == "msgctxt":
		return f'{tag} "{esc}"\n'
	# msgid / msgstr
	if "\\n" in esc or len(esc) > 72:
		lines = [f'{tag} ""']
		# break into chunks of ~70
		i = 0
		while i < len(esc):
			# try not to split escape sequences awkwardly
			end = min(i + 70, len(esc))
			if end < len(esc):
				# avoid ending on a dangling backslash
				while end > i and esc[end - 1] == "\\":
					end -= 1
				if end == i:
					end = min(i + 70, len(esc))
			lines.append(f'"{esc[i:end]}"')
			i = end
		return "\n".join(lines) + "\n"
	return f'{tag} "{esc}"\n'


def key_for(msgctxt, msgid: str) -> str:
	return f"{msgctxt}\x04{msgid}" if msgctxt else msgid


def write_po(path: Path, lang: str, domain: str, entries, trans: dict) -> int:
	missing = 0
	parts = [
		f"# {domain} translation for {lang}.\n",
		"# Copyright (C) Ubuntu Hello contributors\n",
		f"# This file is distributed under the same license as the {domain} package.\n",
		"#\n",
		'msgid ""\n',
		'msgstr ""\n',
		f'"Project-Id-Version: {domain} {project_version()}\\n"\n',
		'"Report-Msgid-Bugs-To: https://github.com/ventura8/ubuntu-hello/issues\\n"\n',
		'"POT-Creation-Date: 2026-08-12 03:05+0300\\n"\n',
		f'"PO-Revision-Date: 2026-08-12 03:20+0300\\n"\n',
		'"Last-Translator: Ubuntu Hello contributors\\n"\n',
		'"Language-Team: Ubuntu Hello\\n"\n',
		f'"Language: {lang}\\n"\n',
		'"MIME-Version: 1.0\\n"\n',
		'"Content-Type: text/plain; charset=UTF-8\\n"\n',
		'"Content-Transfer-Encoding: 8bit\\n"\n',
		f'"Plural-Forms: {PLURAL_FORMS[lang]}\\n"\n',
		"\n",
	]
	for e in entries:
		k = key_for(e["msgctxt"], e["msgid"])
		msgstr = trans.get(k, trans.get(e["msgid"], ""))
		if translation_missing(e, trans, lang):
			missing += 1
			msgstr = [""] * nplurals(lang) if e["msgid_plural"] is not None else ""
		if e["prefix"]:
			# keep only reference / translator comments, drop fuzzy
			for line in e["prefix"].splitlines():
				if line.startswith("#,") and "fuzzy" in line:
					continue
				if line.startswith("#:") or line.startswith("#.") or line.startswith("# "):
					parts.append(line + "\n")
		if e["msgctxt"] is not None:
			parts.append(format_string_field("msgctxt", e["msgctxt"]))
		parts.append(format_string_field("msgid", e["msgid"]))
		if e["msgid_plural"] is not None:
			parts.append(format_string_field("msgid_plural", e["msgid_plural"]))
			for index, form in enumerate(msgstr):
				parts.append(format_string_field(f"msgstr[{index}]", form))
		else:
			parts.append(format_string_field("msgstr", msgstr))
		parts.append("\n")
	path.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
	return missing


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--check", action="store_true")
	args = ap.parse_args()
	rc = 0
	for domain, meta in DOMAINS.items():
		entries = parse_pot(meta["pot"])
		linguas = (meta["podir"] / "LINGUAS").read_text(encoding="utf-8").split()
		print(f"{domain}: {len(entries)} msgids, {len(linguas)} langs")
		for lang in linguas:
			jpath = DATA / domain / f"{lang}.json"
			if not jpath.is_file():
				print(f"  MISSING data {jpath}", file=sys.stderr)
				rc = 1
				continue
			trans = json.loads(jpath.read_text(encoding="utf-8"))
			po_path = meta["podir"] / f"{lang}.po"
			if args.check:
				missing = sum(1 for e in entries if translation_missing(e, trans, lang))
			else:
				missing = write_po(po_path, lang, domain, entries, trans)
			if missing:
				print(f"  {lang}: {missing} untranslated", file=sys.stderr)
				rc = 1
			else:
				print(f"  {lang}: ok")
	return rc


if __name__ == "__main__":
	sys.exit(main())
