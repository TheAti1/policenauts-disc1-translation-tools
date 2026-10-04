#!/usr/bin/env python3
"""Create a fresh English-source REX JSON from private Disc 1 master metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FORMAT = "policenauts-rex-v1"
KIND_BY_SOURCE = {
    "bin_ascii": "menu",
    "game_sz": "dialogue",
    "jxs_voice": "voiced_dialogue",
    "movie_ascii": "movie_subtitle",
}


def make_document(master: dict) -> dict:
    entries = master.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Master JSON must contain an entries list")
    rows = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("index") != index:
            raise ValueError(f"Master entry order is invalid at index {index}")
        source = entry.get("text_with_breaks")
        kind = KIND_BY_SOURCE.get(entry.get("source"))
        if not isinstance(source, str) or kind is None:
            raise ValueError(f"Master source text/type is invalid at index {index}")
        rows.append({
            "id": f"p{index:05d}",
            "index": index,
            "kind": kind,
            "source": source,
            "translation": None,
        })
    return {
        "format": FORMAT,
        "count": len(rows),
        "source_language": "en",
        "target_language": "tr",
        "entries": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    args = parser.parse_args()
    if args.out.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists; refusing to overwrite translations: {args.out}")
    master = json.loads(args.master.read_text(encoding="utf-8"))
    document = make_document(master)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {document['count']} English source entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
