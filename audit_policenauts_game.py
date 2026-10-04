#!/usr/bin/env python3
"""Audit GAME*.DPK .SZ strings against an existing private catalog."""

from __future__ import annotations

import argparse
import bisect
import json
import re
from collections import defaultdict
from pathlib import Path

from policenauts_disc_tools import SECTOR_USER, dpk_records_from_image, iso_nauts_files, read_user_bytes_from_raw
from policenauts_text_format import game_display_text


def decode_span(data: bytes, start: int) -> tuple[int, str, int]:
    pos = start
    out = []
    unknown = 0
    while pos < len(data):
        byte = data[pos]
        if byte == 0:
            break
        if byte == 0x80 and pos + 3 < len(data) and data[pos + 1] == 0x6F:
            pos += 4
            continue
        if byte == 0x30 and pos + 4 < len(data) and data[pos + 1] == 0x6F and data[pos + 4] == 0xFA:
            pos += 5
            continue
        if byte == 0x6F and pos + 2 < len(data):
            pos += 3
            continue
        if byte == 0x30 and pos + 1 < len(data) and data[pos + 1] == 0xFA:
            pos += 2
            continue
        if byte == 0x6E:
            pos += 3 if pos + 2 < len(data) and data[pos + 1] == 0 and data[pos + 2] == 0x6E else 1
            continue
        if byte == 0x7F and pos + 1 < len(data) and data[pos + 1] == 0x65:
            pos += 2
            continue
        if byte == 0x80 and pos + 1 < len(data):
            decoded = (-data[pos + 1]) & 0xFF
            pos += 2
        else:
            decoded = (-byte) & 0xFF
            pos += 1
        if decoded == 10:
            out.append("\n")
        elif decoded == 13:
            pass
        elif 0x20 <= decoded <= 0x7E:
            out.append(chr(decoded))
        else:
            unknown += 1
    return pos, "".join(out), unknown


def plausible(text: str) -> bool:
    value = " ".join(text.split())
    if len(value) < 4 or len(value) > 320 or not any(char.isalpha() for char in value):
        return False
    if re.fullmatch(r"(?:[Llrc]\d+|ACT\d+|DISK\d+)", value, re.I):
        return False
    if re.search(r"\.(?:pak|cpk|tim|bin)$", value, re.I) or "`" in value:
        return False
    if len(value) < 8 and not value.endswith((".", "!", "?")):
        return False
    letters = sum(char.isalpha() for char in value)
    allowed = sum(char.isalnum() or char in " .,!?;:'\"-()[]/%&+#" for char in value)
    return letters / len(value) >= 0.32 and allowed / len(value) >= 0.88


def scan_game(fp, entries: list[dict]) -> dict:
    known: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    for entry in entries:
        if entry["source"] == "game_sz":
            start = int(entry["local_offset"])
            known[(entry["container"], int(entry["container_entry_index"]))].append(
                (start, start + int(entry["old_byte_len"]))
            )
    for intervals in known.values():
        intervals.sort()
    files = iso_nauts_files(fp)
    candidates = []
    scanned = 0
    for name in ("GAME1.DPK", "GAME2.DPK"):
        extent, size = files[name]
        base = extent * SECTOR_USER
        for record in dpk_records_from_image(fp, base):
            if not record["name"].upper().endswith(".SZ"):
                continue
            if record["data_offset"] + record["data_size"] > size:
                raise RuntimeError(f"{name} entry {record['index']} exceeds ISO file")
            data = read_user_bytes_from_raw(fp, base + record["data_offset"], record["data_size"])
            intervals = known.get((name, record["index"]), [])
            starts = [item[0] for item in intervals]
            pos = 0
            while pos < len(data):
                while pos < len(data) and data[pos] == 0:
                    pos += 1
                if pos >= len(data):
                    break
                start = pos
                end, raw_text, unknown = decode_span(data, start)
                pos = end + 1
                scanned += 1
                prior = bisect.bisect_right(starts, start) - 1
                if prior >= 0 and intervals[prior][1] >= start:
                    continue
                if prior + 1 < len(intervals) and intervals[prior + 1][0] < end:
                    continue
                text = game_display_text(raw_text)
                if unknown or not plausible(text):
                    continue
                user_offset = base + record["data_offset"] + start
                candidates.append({
                    "container": name,
                    "container_entry_index": record["index"],
                    "container_entry_name": record["name"],
                    "local_offset": start,
                    "user_offset": user_offset,
                    "old_byte_len": end - start,
                    "old_bytes_hex": data[start:end].hex(" "),
                    "raw_command_text": raw_text,
                    "text_with_breaks": text,
                })
    return {"decoded_spans": scanned, "unmapped_plausible_count": len(candidates), "candidates": candidates}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads(args.master.read_text(encoding="utf-8"))["entries"]
    with args.source_bin.open("rb") as fp:
        report = scan_game(fp, entries)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "candidates"}, indent=2))
    for candidate in report["candidates"][:80]:
        print(candidate["container"], candidate["container_entry_name"], candidate["local_offset"], repr(candidate["text_with_breaks"][:100]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
