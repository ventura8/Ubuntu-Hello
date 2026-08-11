#!/usr/bin/env python3
"""Merge pack_*.py GTK/CORE dicts → JSON → .po via i18n-fill-translations.py."""
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]

def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def main():
    gtk = {}
    core = {}
    for path in sorted(ROOT.glob("pack_*.py")):
        mod = load_module(path)
        gtk.update(getattr(mod, "GTK", {}))
        core.update(getattr(mod, "CORE", {}))
    # also load existing JSON as packs
    for domain, tables in (("ubuntu-hello-gtk", gtk), ("ubuntu-hello", core)):
        keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
        # hydrate from JSON files if present and not in tables
        for j in (ROOT / domain).glob("*.json"):
            if j.name.startswith("_"):
                continue
            lang = j.stem
            if lang in tables:
                continue
            data = json.loads(j.read_text(encoding="utf-8"))
            tables[lang] = [data[k] for k in keys]
        for lang, vals in tables.items():
            if len(vals) != len(keys):
                print(f"BAD LEN {domain}/{lang}: {len(vals)}!={len(keys)}", file=sys.stderr)
                continue
            data = {k: v for k, v in zip(keys, vals)}
            (ROOT / domain / f"{lang}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(f"{domain}: {len(tables)} language tables")
    rc = subprocess.call([sys.executable, str(REPO / "scripts" / "i18n-fill-translations.py")])
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
