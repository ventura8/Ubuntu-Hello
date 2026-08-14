#!/usr/bin/env python3
"""Emit ordered packs for every Whisper language from tm/<domain>/<lang>.json maps
(english msgid -> msgstr), falling back to building from sibling high-quality seeds.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
LINGUAS = (REPO / "po" / "whisper-languages.txt").read_text().split()
TM = Path(__file__).resolve().parent / "tm"

def bodies(keys):
    return [k.split("\x04", 1)[-1] for k in keys]

def emit_domain(domain: str) -> None:
    keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
    en = bodies(keys)
    out_dir = ROOT / "packs" / domain
    out_dir.mkdir(parents=True, exist_ok=True)
    for lang in LINGUAS:
        # Prefer existing complete pack
        pack = out_dir / f"{lang}.json"
        if pack.is_file():
            vals = json.loads(pack.read_text(encoding="utf-8"))
            if len(vals) == len(en) and all(vals):
                continue
        tm_path = TM / domain / f"{lang}.json"
        if not tm_path.is_file():
            raise SystemExit(f"missing TM {tm_path}")
        tm = json.loads(tm_path.read_text(encoding="utf-8"))
        vals = []
        for s in en:
            v = tm.get(s)
            if not v:
                raise SystemExit(f"{domain}/{lang}: missing {s[:50]!r}")
            vals.append(v)
        pack.write_text(json.dumps(vals, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"pack {domain}/{lang}")

def main():
    for domain in ("ubuntu-hello-gtk", "ubuntu-hello"):
        emit_domain(domain)
    print("all packs present")

if __name__ == "__main__":
    main()
