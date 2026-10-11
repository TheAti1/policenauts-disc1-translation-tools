#!/usr/bin/env python3
"""Audit PS1 jXS subtitle tables against a private translation catalog."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import mmap
from pathlib import Path
import struct

from build_policenauts_tr_bin import load_entries
from extract_policenauts_audited import valid_jxs_markers
from policenauts_disc_tools import iso_nauts_files, SECTOR_RAW, SECTOR_USER


def scan_sector(sector: bytes) -> tuple[list[dict], dict | None]:
    """Follow the jXS header's subtitle pointer, not printable-byte runs."""
    if len(sector) != SECTOR_RAW or sector[24:28] != b'"jXS':
        raise ValueError("Not a complete jXS sector")
    position = 24 + struct.unpack_from("<I", sector, 64)[0]
    records = []
    for _ in range(128):
        if position < 24 or position + 16 >= 2072 or position % 4:
            return records, {"kind": "invalid_header_position", "position": position}
        words = struct.unpack_from("<IIII", sector, position)
        start = position + 16
        if words == (0, 0, 0, 0) and sector[start] == 0:
            return records, None
        end = sector.find(b"\0", start, 2072)
        if end < 0:
            return records, {"kind": "unterminated_record", "header": position,
                             "start": start, "words": list(words),
                             "prefix_hex": sector[start:min(start + 8, 2072)].hex()}
        next_position = (end + 4) & ~3
        if next_position > 2072 or any(sector[end + 1:next_position]):
            return records, {"kind": "nonzero_padding", "header": position}
        records.append({"header": position, "start": start, "end": end,
                        "words": words, "raw": sector[start:end]})
        position = next_position
    return records, {"kind": "record_limit", "position": position}


def audit(source_bin: Path, master: Path) -> dict:
    entries = load_entries(master)
    known = {int(entry["raw_offset"]): entry for entry in entries
             if entry["source"] == "jxs_voice"}
    observed = set()
    counters = Counter()
    uncatalogued = []
    damaged = []
    source_mismatches = []
    with source_bin.open("rb") as fp:
        extent, size = iso_nauts_files(fp)["PN_VOX1.PAC"]
        begin = extent * SECTOR_RAW
        end = (extent + (size + SECTOR_USER - 1) // SECTOR_USER) * SECTOR_RAW
        with mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ) as image:
            markers = valid_jxs_markers(image, begin, min(end, len(image)))
            for marker in markers:
                sector_index = marker // SECTOR_RAW
                if marker % SECTOR_RAW != 25:
                    damaged.append({"sector": sector_index, "kind": "misaligned_magic"})
                    continue
                sector = image[sector_index * SECTOR_RAW:(sector_index + 1) * SECTOR_RAW]
                rows, problem = scan_sector(sector)
                counters["blocks"] += 1
                if problem:
                    damaged.append({"sector": sector_index, **problem})
                for row in rows:
                    raw_offset = sector_index * SECTOR_RAW + row["start"]
                    if row["raw"] == b"  ":
                        counters["blank_records"] += 1
                    elif raw_offset in known:
                        observed.add(raw_offset)
                        counters["catalogued_records"] += 1
                        if row["raw"] != bytes.fromhex(known[raw_offset]["old_bytes_hex"]):
                            source_mismatches.append(known[raw_offset]["index"])
                    else:
                        uncatalogued.append({"sector": sector_index,
                                            "raw_offset": raw_offset,
                                            "raw_hex": row["raw"].hex()})
    return {"status": "PASS" if not (uncatalogued or source_mismatches or damaged
                                       or known.keys() - observed) else "REVIEW_REQUIRED",
            "blocks": counters["blocks"],
            "catalogued_records": counters["catalogued_records"],
            "blank_records": counters["blank_records"],
            "uncatalogued_complete_records": uncatalogued,
            "damaged_records": damaged,
            "source_mismatch_ids": source_mismatches,
            "catalogued_offsets_not_found": sorted(known.keys() - observed)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source_bin, args.master)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: len(value) if isinstance(value, list) else value
                      for key, value in result.items()}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
