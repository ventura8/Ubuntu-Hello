#!/usr/bin/env python3
"""Expand a translation-memory dict {msgid: {lang: msgstr}} into per-lang ordered lists."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def expand(domain: str, tm: dict, linguas: list[str]) -> dict[str, list[str]]:
    keys = json.loads((ROOT / domain / "_keys.json").read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {lang: [] for lang in linguas}
    missing = []
    for key in keys:
        row = tm.get(key)
        if row is None:
            # try without msgctxt prefix for Window title key already in keys
            missing.append(("NO_TM", key[:60]))
            for lang in linguas:
                out[lang].append("")
            continue
        for lang in linguas:
            val = row.get(lang)
            if not val:
                missing.append((lang, key[:40]))
                out[lang].append("")
            else:
                out[lang].append(val)
    return out, missing
