#!/usr/bin/env python3
"""Expand expert_pack LANGUAGE tables into per-lang JSON, then fill .po files."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]

def load_pack():
    path = ROOT / "expert_pack.py"
    spec = importlib.util.spec_from_file_location("expert_pack", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def main():
    pack = load_pack()
    for domain in ("ubuntu-hello-gtk", "ubuntu-hello"):
        keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
        tables = getattr(pack, domain.replace("-", "_").replace("ubuntu_hello_gtk", "GTK").replace("ubuntu_hello", "CORE") if False else "", {})
    # Use pack.GTK and pack.CORE
    for domain, attr in (("ubuntu-hello-gtk", "GTK"), ("ubuntu-hello", "CORE")):
        keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
        tables = getattr(pack, attr)
        linguas = (REPO / domain.replace("ubuntu-hello-gtk", "ubuntu-hello-gtk").split("/")[0] )
        # domain folder mapping
        podir = REPO / ("ubuntu-hello-gtk" if domain.endswith("gtk") else "ubuntu-hello") / "po"
        want = (podir / "LINGUAS").read_text().split()
        for lang in want:
            if lang not in tables:
                print(f"MISSING {domain}/{lang}", file=sys.stderr)
                continue
            vals = tables[lang]
            if len(vals) != len(keys):
                print(f"LEN {domain}/{lang}: {len(vals)} != {len(keys)}", file=sys.stderr)
                continue
            data = {k: v for k, v in zip(keys, vals)}
            (ROOT / domain / f"{lang}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"json {domain}/{lang}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
