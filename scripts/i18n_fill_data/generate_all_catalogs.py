#!/usr/bin/env python3
"""Generate complete filled .po catalogs for all Whisper langs (both domains).

Loads English msgid arrays + per-language ordered msgstr packs from
scripts/i18n_fill_data/packs/<domain>/<lang>.json, writes keyed JSON, then
applies via scripts/i18n-fill-translations.py.

Also loads any catalog_data/*.py modules that export GTK and/or CORE dicts
mapping lang -> list[str] (ordered msgstr).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
LINGUAS = (REPO / "po" / "whisper-languages.txt").read_text().split()


def load_module(path: Path):
	spec = importlib.util.spec_from_file_location(path.stem, path)
	mod = importlib.util.module_from_spec(spec)
	assert spec.loader is not None
	spec.loader.exec_module(mod)
	return mod


def collect_tables(domain: str) -> dict[str, list[str]]:
	keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
	tables: dict[str, list[str]] = {}
	# packs/
	pack_dir = ROOT / "packs" / domain
	if pack_dir.is_dir():
		for p in pack_dir.glob("*.json"):
			if p.name.startswith("_") or p.name.startswith("__"):
				continue
			vals = json.loads(p.read_text(encoding="utf-8"))
			if len(vals) != len(keys):
				raise SystemExit(f"BAD pack {p}: {len(vals)} != {len(keys)}")
			tables[p.stem] = vals
	# keyed JSON already in domain/
	for p in (ROOT / domain).glob("*.json"):
		if p.name.startswith("_"):
			continue
		if p.stem in tables:
			continue
		data = json.loads(p.read_text(encoding="utf-8"))
		tables[p.stem] = [data[k] for k in keys]
	# catalog_data modules
	for path in sorted((ROOT / "catalog_data").glob("*.py")):
		if path.name.startswith("_"):
			continue
		mod = load_module(path)
		attr = "GTK" if domain.endswith("gtk") else "CORE"
		blob = getattr(mod, attr, None)
		if not blob:
			continue
		for lang, vals in blob.items():
			if len(vals) != len(keys):
				raise SystemExit(f"BAD {path.name} {attr}[{lang}]: {len(vals)} != {len(keys)}")
			tables[lang] = vals
	return tables


def write_keyed(domain: str, tables: dict[str, list[str]]) -> None:
	keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
	missing = [l for l in LINGUAS if l not in tables]
	if missing:
		raise SystemExit(f"{domain}: missing langs ({len(missing)}): {missing[:12]}")
	for lang in LINGUAS:
		vals = tables[lang]
		empty = sum(1 for v in vals if not v)
		if empty:
			raise SystemExit(f"{domain}/{lang}: {empty} empty msgstr")
		data = {k: v for k, v in zip(keys, vals)}
		(ROOT / domain / f"{lang}.json").write_text(
			json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
		)
		# also refresh pack
		pack = ROOT / "packs" / domain / f"{lang}.json"
		pack.parent.mkdir(parents=True, exist_ok=True)
		pack.write_text(json.dumps(vals, ensure_ascii=False) + "\n", encoding="utf-8")
	print(f"{domain}: wrote {len(LINGUAS)} catalogs")


def main() -> int:
	for domain in ("ubuntu-hello-gtk", "ubuntu-hello"):
		tables = collect_tables(domain)
		print(f"{domain}: have {len(tables)} lang tables before require")
		write_keyed(domain, tables)
	rc = subprocess.call([sys.executable, str(REPO / "scripts" / "i18n-fill-translations.py")])
	return rc


if __name__ == "__main__":
	raise SystemExit(main())
