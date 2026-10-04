#!/usr/bin/env python3
"""Find timed ASCII subtitle candidates in Disc 1 MOV files missing from a catalog."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from policenauts_disc_tools import SECTOR_USER, _iso_records, iso_nauts_files, read_user_bytes_from_raw

TEXT = re.compile(rb"(?:[\x20-\x7e]|\x80\|){4,}\x00")
TIMING = re.compile(b"\x00\x38[\x01\x02]\x00\x01\x00")


def plausible_subtitle(text: str) -> bool:
    value = " ".join(text.split()).strip()
    if len(value) < 6 or len(value) > 320 or "SCIPPDTS" in value or "SDNSSDTS" in value:
        return False
    if any(char in value for char in "`~^<>{}[]\\@_|_$%&+*/"):
        return False
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", value)
    if sum(ch.isalpha() for ch in value) / len(value) <= 0.6 or sum(ch.islower() for ch in value) < 4:
        return False
    if len(words) == 1:
        return len(value) >= 7 and value.endswith((".", "!", "?", ","))
    return len(words) >= 2 and (len(value) >= 14 or value.endswith((".", "!", "?", ",")))


def scan_movies(fp, entries: list[dict]) -> dict:
    known = [
        (int(e["user_offset"]), int(e["user_offset"]) + int(e["old_byte_len"]))
        for e in entries if e["source"] == "movie_ascii"
    ]
    timed_candidates = []
    untimed_review = []
    timed_total = 0
    files = _iso_records(fp, *iso_nauts_files(fp)["MOVIE"])
    for filename, (extent, size) in sorted(files.items(), key=lambda item: item[1][0]):
        if not filename.endswith(".MOV"):
            continue
        data = read_user_bytes_from_raw(fp, extent * SECTOR_USER, size)
        for match in TEXT.finditer(data):
            timed = TIMING.search(data[match.end() : match.end() + 64]) is not None
            if timed:
                timed_total += 1
            start = extent * SECTOR_USER + match.start()
            end = extent * SECTOR_USER + match.end() - 1
            value = match.group()[:-1].replace(b"\x80|", b"\n").decode("ascii")
            if not plausible_subtitle(value):
                continue
            if any(start < old_end and old_start < end for old_start, old_end in known):
                continue
            candidate = {
                "movie": filename,
                "local_offset": match.start(),
                "user_offset": start,
                "old_byte_len": end - start,
                "text": value,
                "raw_hex": match.group()[:-1].hex(" "),
                "has_movie_timing_header": timed,
            }
            (timed_candidates if timed else untimed_review).append(candidate)
    return {
        "movie_files": len([name for name in files if name.endswith(".MOV")]),
        "timed_ascii_chunks": timed_total,
        "new_plausible_timed_subtitles": len(timed_candidates),
        "untimed_plausible_strings_for_review": len(untimed_review),
        "timed_candidates": timed_candidates,
        "untimed_review": untimed_review,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads(args.master.read_text(encoding="utf-8"))["entries"]
    with args.source_bin.open("rb") as fp:
        result = scan_movies(fp, entries)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if not isinstance(v, list)}, indent=2))
    for item in result["timed_candidates"]:
        print(item["movie"], item["user_offset"], repr(item["text"][:100]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
