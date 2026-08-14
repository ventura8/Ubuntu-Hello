#!/usr/bin/env python3
"""Write domain/lang.json from an ordered list of msgstr (same order as _keys.json)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent

def main():
    domain, lang = sys.argv[1], sys.argv[2]
    keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
    # remaining args unused; read msgstr list as JSON from stdin
    values = json.load(sys.stdin)
    if len(values) != len(keys):
        raise SystemExit(f"{lang}: expected {len(keys)} strings, got {len(values)}")
    data = {k: v for k, v in zip(keys, values)}
    out = ROOT / domain / f"{lang}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(data)})")

if __name__ == "__main__":
    main()
