#!/usr/bin/env python3
"""Merge ordered-list packs into domain/<lang>.json and apply to .po files."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
LINGUAS = (REPO / "po" / "whisper-languages.txt").read_text().split()

def main() -> int:
    for domain in ("ubuntu-hello-gtk", "ubuntu-hello"):
        keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
        # Load all ordered packs: packs/<domain>/<lang>.json is a list[str]
        pack_dir = ROOT / "packs" / domain
        tables = {}
        if pack_dir.is_dir():
            for p in pack_dir.glob("*.json"):
                vals = json.loads(p.read_text(encoding="utf-8"))
                if len(vals) != len(keys):
                    print(f"BAD {p}: {len(vals)}!={len(keys)}", file=sys.stderr)
                    return 1
                tables[p.stem] = vals
        # Also accept existing keyed JSON
        for p in (ROOT / domain).glob("*.json"):
            if p.name.startswith("_"):
                continue
            if p.stem in tables:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            tables[p.stem] = [data[k] for k in keys]
        missing = [l for l in LINGUAS if l not in tables]
        if missing:
            print(f"{domain}: missing {len(missing)} langs: {missing[:10]}...", file=sys.stderr)
            return 1
        for lang in LINGUAS:
            data = {k: v for k, v in zip(keys, tables[lang])}
            if any(not v for v in data.values()):
                empty = sum(1 for v in data.values() if not v)
                print(f"{domain}/{lang}: {empty} empty msgstr", file=sys.stderr)
                return 1
            (ROOT / domain / f"{lang}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(f"{domain}: wrote {len(LINGUAS)} JSON catalogs")
    rc = subprocess.call([sys.executable, str(REPO / "scripts" / "i18n-fill-translations.py")])
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
