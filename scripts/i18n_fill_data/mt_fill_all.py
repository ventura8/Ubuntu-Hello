#!/usr/bin/env python3
"""Parallel MT fill for Whisper packs (both domains)."""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
LINGUAS = (REPO / "po" / "whisper-languages.txt").read_text().split()

GOOGLE = {
	"af": "af", "am": "am", "ar": "ar", "as": "as", "az": "az", "ba": None,
	"be": "be", "bg": "bg", "bn": "bn", "bo": None, "br": None, "bs": "bs",
	"ca": "ca", "cs": "cs", "cy": "cy", "da": "da", "de": "de", "el": "el",
	"es": "es", "et": "et", "eu": "eu", "fa": "fa", "fi": "fi", "fo": None,
	"fr": "fr", "gl": "gl", "gu": "gu", "ha": "ha", "haw": "haw", "he": "iw",
	"hi": "hi", "hr": "hr", "ht": "ht", "hu": "hu", "hy": "hy", "id": "id",
	"is": "is", "it": "it", "ja": "ja", "jw": "jw", "ka": "ka", "kk": "kk",
	"km": "km", "kn": "kn", "ko": "ko", "la": "la", "lb": "lb", "ln": "ln",
	"lo": "lo", "lt": "lt", "lv": "lv", "mg": "mg", "mi": "mi", "mk": "mk",
	"ml": "ml", "mn": "mn", "mr": "mr", "ms": "ms", "mt": "mt", "my": "my",
	"ne": "ne", "nl": "nl", "nn": "no", "no": "no", "oc": None, "pa": "pa",
	"pl": "pl", "ps": "ps", "pt": "pt", "ro": "ro", "ru": "ru", "sa": "sa",
	"sd": "sd", "si": "si", "sk": "sk", "sl": "sl", "sn": "sn", "so": "so",
	"sq": "sq", "sr": "sr", "su": "su", "sv": "sv", "sw": "sw", "ta": "ta",
	"te": "te", "tg": "tg", "th": "th", "tk": "tk", "tl": "tl", "tr": "tr",
	"tt": "tt", "uk": "uk", "ur": "ur", "uz": "uz", "vi": "vi", "yi": "yi",
	"yo": "yo", "zh": "zh-CN",
}

SKIP_EXACT = {
	"Ubuntu Hello", "ID", "KWallet", "GNOME Keyring", "None", "FPS", "TPM",
	"OpenCV2", "cv2", "IR",
}


def bodies(keys):
	return [k.split("\x04", 1)[-1] for k in keys]


def protect(s: str):
	urls = {}
	ph = []

	def url_sub(m):
		key = f"⟦U{len(urls):04d}⟧"
		urls[key] = m.group(0)
		return key

	out = re.sub(r"https?://[^\s\"<>]+", url_sub, s)

	def ph_sub(m):
		ph.append(m.group(0))
		return f"⟦P{len(ph)-1}⟧"

	out = re.sub(
		r"\{[a-zA-Z_][a-zA-Z0-9_]*\}|\{\}|%(?:\d+\$)?[sdifax]|%\.\d+[fs]|%dx%d",
		ph_sub,
		out,
	)
	for a, b in (
		("Ubuntu Hello", "⟦UH⟧"),
		("Windows Hello", "⟦WH⟧"),
		("OpenCV2", "⟦OCV2⟧"),
		("KWallet", "⟦KW⟧"),
		("GNOME Keyring", "⟦GK⟧"),
		("PAM_AUTHTOK", "⟦PAM⟧"),
		("pam_gnome_keyring", "⟦PGK⟧"),
		("pam_kwallet5", "⟦PKW⟧"),
		("tpm2-tools", "⟦TPM2⟧"),
		("AES-256-GCM", "⟦AES⟧"),
	):
		out = out.replace(a, b)
	out = re.sub(r"\bcv2\b", "⟦cv2⟧", out)
	return out, ph, urls


def unprotect(s, ph, urls):
	out = s
	for k, u in urls.items():
		out = out.replace(k, u)
	for i, p in enumerate(ph):
		out = out.replace(f"⟦P{i}⟧", p)
	for a, b in (
		("⟦UH⟧", "Ubuntu Hello"),
		("⟦WH⟧", "Windows Hello"),
		("⟦OCV2⟧", "OpenCV2"),
		("⟦cv2⟧", "cv2"),
		("⟦KW⟧", "KWallet"),
		("⟦GK⟧", "GNOME Keyring"),
		("⟦PAM⟧", "PAM_AUTHTOK"),
		("⟦PGK⟧", "pam_gnome_keyring"),
		("⟦PKW⟧", "pam_kwallet5"),
		("⟦TPM2⟧", "tpm2-tools"),
		("⟦AES⟧", "AES-256-GCM"),
	):
		out = out.replace(a, b)
	return out


def translate_list(translator, en):
	meta = []
	protected = []
	for t in en:
		if t.strip() in SKIP_EXACT or not t.strip():
			meta.append(None)
			protected.append(t)
			continue
		prot, ph, urls = protect(t)
		meta.append((ph, urls))
		protected.append(prot)

	translated = list(protected)
	idxs = [i for i, m in enumerate(meta) if m is not None]
	for start in range(0, len(idxs), 40):
		batch_i = idxs[start : start + 40]
		batch = [protected[i] for i in batch_i]
		try:
			out = translator.translate_batch(batch)
			if not isinstance(out, list) or len(out) != len(batch):
				raise RuntimeError("bad batch")
		except Exception:
			out = []
			for s in batch:
				try:
					out.append(translator.translate(s))
				except Exception:
					out.append(s)
				time.sleep(0.02)
		for i, tr in zip(batch_i, out):
			ph, urls = meta[i]
			translated[i] = unprotect(tr or en[i], ph, urls)
		time.sleep(0.05)
	return [v if v else e for v, e in zip(translated, en)]


def worker(args):
	"""Process one (domain, lang, keys, en, target_or_None)."""
	domain, lang, keys, en, target = args
	sys.path[:0] = list(Path("/tmp/uh-i18n-venv/lib").glob("python*/site-packages"))
	pack = ROOT / "packs" / domain / f"{lang}.json"
	if pack.is_file():
		vals = json.loads(pack.read_text(encoding="utf-8"))
		if len(vals) == len(en) and all(vals):
			return domain, lang, "keep", vals
	if target is None:
		vals = list(en)
		status = "en"
	else:
		from deep_translator import GoogleTranslator

		translator = GoogleTranslator(source="en", target=target)
		vals = translate_list(translator, en)
		status = f"mt:{target}"
	pack.parent.mkdir(parents=True, exist_ok=True)
	pack.write_text(json.dumps(vals, ensure_ascii=False) + "\n", encoding="utf-8")
	data = {k: v for k, v in zip(keys, vals)}
	(ROOT / domain / f"{lang}.json").write_text(
		json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
	)
	return domain, lang, status, None


def main():
	jobs = []
	for domain in ("ubuntu-hello-gtk", "ubuntu-hello"):
		keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
		en = bodies(keys)
		for lang in LINGUAS:
			jobs.append((domain, lang, keys, en, GOOGLE.get(lang)))

	workers = 8
	print(f"jobs={len(jobs)} workers={workers}", flush=True)
	done = 0
	with ProcessPoolExecutor(max_workers=workers) as ex:
		futs = [ex.submit(worker, j) for j in jobs]
		for fut in as_completed(futs):
			domain, lang, status, _ = fut.result()
			done += 1
			print(f"[{done}/{len(jobs)}] {domain}/{lang}: {status}", flush=True)
	print("OK packs complete", flush=True)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
