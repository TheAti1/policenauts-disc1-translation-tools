#!/usr/bin/env python3
"""Inventory uncatalogued ASCII strings in every BIN.DPK overlay.

This is a read-only coverage audit, not an extractor or patcher. Some hits are
debug messages, file paths, duplicates, or fragments and require manual review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from policenauts_disc_tools import (
    SECTOR_USER, dpk_records_from_image, iso_nauts_files,
    raw_offset_from_user_offset, read_user_bytes_from_raw,
)


ASCII_NUL = re.compile(rb"[\x20-\x7e\n\r]{5,}\x00")


def find_ascii_strings(data: bytes, min_length: int = 5) -> list[tuple[int, bytes]]:
    if not 2 <= min_length <= 5:
        raise ValueError("Minimum ASCII string length must be 2-5")
    pattern = (ASCII_NUL if min_length == 5 else
               re.compile(rb"[\x20-\x7e\n\r]{" + str(min_length).encode() + rb",}\x00"))
    return [(match.start(), match.group()[:-1]) for match in pattern.finditer(data)]


def audit(source_bin: Path, master: Path, min_length: int = 5) -> dict:
    entries = json.loads(master.read_text(encoding="utf-8"))["entries"]
    known = {}
    by_text: dict[str, list[int]] = {}
    for entry in entries:
        if entry["source"] != "bin_ascii":
            continue
        key = (entry["container_entry_index"], entry["local_offset"])
        known[key] = entry
        by_text.setdefault(entry["text_with_breaks"], []).append(entry["index"])

    result = {"status": "REVIEW_ONLY", "source_bin": str(source_bin),
              "master": str(master), "min_length": min_length,
              "overlays": [], "uncatalogued": []}
    with source_bin.open("rb") as fp:
        extent, _size = iso_nauts_files(fp)["BIN.DPK"]
        base = extent * SECTOR_USER
        for record in dpk_records_from_image(fp, base):
            data = read_user_bytes_from_raw(
                fp, base + record["data_offset"], record["data_size"])
            found = find_ascii_strings(data, min_length)
            result["overlays"].append({"name": record["name"],
                                       "index": record["index"],
                                       "ascii_nul_count": len(found)})
            for offset, raw in found:
                if (record["index"], offset) in known:
                    continue
                # A scan can start inside a catalogued string after a control
                # byte; overlapping text is never independently importable.
                overlaps = [entry["index"] for (rec, start), entry in known.items()
                            if rec == record["index"]
                            and offset < start + int(entry["old_byte_len"])
                            and start < offset + len(raw)]
                text = raw.decode("ascii")
                user_offset = base + record["data_offset"] + offset
                result["uncatalogued"].append({
                    "overlay": record["name"], "overlay_index": record["index"],
                    "local_offset": offset, "user_offset": user_offset,
                    "raw_offset": raw_offset_from_user_offset(user_offset),
                    "old_bytes_hex": raw.hex(), "text": text,
                    "catalogue_overlap_ids": overlaps,
                    "same_text_catalogue_ids": by_text.get(text, []),
                })
    result["uncatalogued_count"] = len(result["uncatalogued"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--min-length", type=int, choices=range(2, 6), default=5,
                        help="Include short NUL-terminated labels (review-only)")
    args = parser.parse_args()
    if args.report.exists() or args.report.resolve() in {
        args.source_bin.resolve(), args.master.resolve()
    }:
        parser.error("Report path must be new and must not overwrite an input")
    result = audit(args.source_bin, args.master, args.min_length)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": result["status"],
                      "uncatalogued_count": result["uncatalogued_count"],
                      "overlays": result["overlays"],
                      "report": str(args.report)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
