#!/usr/bin/env python3
"""Audit an English Disc 1 image and extend an existing editable text catalog.

Game images and translation catalogs are intentionally not distributed here.
This tool preserves existing edits by raw source offset and writes a review report
for newly discovered strings rather than silently inventing translations.
"""

from __future__ import annotations

import argparse
import json
import mmap
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any

from audit_policenauts_movies import scan_movies
from policenauts_disc_tools import (
    SECTOR_RAW,
    SECTOR_USER,
    dpk_records_from_image,
    iso_nauts_files,
    raw_offset_from_user_offset,
    read_user_bytes_from_raw,
)

MENU_LABEL_OFFSETS = (
    0x3B2C, 0x3B3C, 0x3B44, 0x3B58, 0x3B64, 0x3B70, 0x3B7C,
    0x3B9C, 0x3BA6, 0x3BAC, 0x3BB8, 0x3BC8, 0x3BD4,
    0x3BFC, 0x3C08, 0x3C20, 0x3C2C, 0x3C44, 0x3C50,
    0x3C64, 0x3C6C, 0x3C78, 0x3C80, 0x3C8C,
    0x3CC8, 0x3CD8, 0x3CE8, 0x3D04, 0x3D28,
    0x421F, 0x4390, 0x469D, 0x46CC, 0x470E, 0x4750,
    0x4770, 0x47BC, 0x47F9, 0x4810, 0x4834, 0x4844,
    0x4858, 0x4874, 0x491C, 0x4924, 0x4938, 0x4948,
)

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def previous_edits(master_path: Path, edit_path: Path) -> dict[tuple[int, bytes], str]:
    if master_path.exists() != edit_path.exists():
        raise RuntimeError("Only one prior output exists; refusing to overwrite translations")
    if not master_path.exists():
        return {}
    entries = read_json(master_path)["entries"]
    edits = read_json(edit_path)
    if len(entries) != len(edits):
        raise RuntimeError("Prior master and editable JSON lengths differ")
    return {
        (int(entry["raw_offset"]), bytes.fromhex(entry["old_bytes_hex"])): edit
        for entry, edit in zip(entries, edits)
    }


def menu_entries(fp) -> list[dict[str, Any]]:
    extent, _size = iso_nauts_files(fp)["BIN.DPK"]
    base_user = extent * SECTOR_USER
    menu = dpk_records_from_image(fp, base_user)[6]
    if menu["name"] != "MENU.BIN":
        raise RuntimeError("Unexpected BIN.DPK entry 6")
    data = read_user_bytes_from_raw(fp, base_user + menu["data_offset"], menu["data_size"])
    out = []
    for offset in MENU_LABEL_OFFSETS:
        terminator = data.index(0, offset)
        actual = data[offset:terminator].decode("ascii")
        if not actual or not all(0x20 <= ord(ch) <= 0x7E for ch in actual):
            raise RuntimeError(f"Invalid menu text at {offset:#x}")
        zero_count = 0
        while terminator + zero_count < len(data) and data[terminator + zero_count] == 0:
            zero_count += 1
        # Keep the original terminator, and only use a few verified padding bytes.
        span = len(actual) + min(max(0, zero_count - 1), 3)
        chunk = data[offset : offset + span]
        user_offset = base_user + menu["data_offset"] + offset
        out.append({
            "source": "bin_ascii",
            "source_file": "NAUTS/BIN.DPK/MENU.BIN",
            "container": "BIN.DPK",
            "container_entry_index": 6,
            "container_entry_name": "MENU.BIN",
            "local_offset": offset,
            "user_offset": user_offset,
            "raw_offset": raw_offset_from_user_offset(user_offset),
            "old_byte_len": span,
            "old_bytes_hex": chunk.hex(" "),
            "text_with_breaks": actual,
            "text": actual,
        })
    return out


def valid_jxs_markers(raw: mmap.mmap, begin: int, end: int) -> list[int]:
    markers = []
    cursor = begin
    while True:
        marker = raw.find(b"jXS", cursor, end)
        if marker < 0:
            break
        base = marker + 3
        if base + 32 < end and (
            struct.unpack_from("<I", raw, base)[0] == 0x800
            and struct.unpack_from("<I", raw, base + 8)[0] == 0xFFFFFFFF
            and struct.unpack_from("<I", raw, base + 16)[0] == 0x7FFF
            and struct.unpack_from("<I", raw, base + 24)[0] == 0x30
        ):
            markers.append(marker)
        cursor = marker + 3
    return markers


def voice_text(chunk: bytes) -> str | None:
    value = chunk.replace(b"\xd0\x06", b"-").replace(b"\x80#", b"")
    if any(byte not in (10, 13) and not 0x20 <= byte <= 0x7E for byte in value):
        return None
    return value.decode("ascii").replace("\r", "").strip()


def voice_candidate(text: str) -> bool:
    if len(text) < 2 or len(text) > 320:
        return False
    if not text[0].isalpha() and text[0] not in ('"', "'", "(", "-"):
        return False
    if any(c in text for c in "@`|~^<>={}[]\\_"):
        return False
    letters = sum(c.isalpha() for c in text)
    if letters < 2 or letters / len(text) < 0.42:
        return False
    if re.search(r"[a-z][A-Z]", text) or re.fullmatch(r"U+(?:\s+U+)*", text):
        return False
    if re.fullmatch(r"[A-Z]{2,8}", text):
        return False
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    if len(words) == 1:
        return text.endswith((".", "!", "?", ",")) or text in {"Ed", "Dad"}
    return not all(len(word) <= 2 for word in words)


def voice_entries(raw: mmap.mmap, fp, known: set[int], source_name: str) -> tuple[list[dict[str, Any]], int]:
    extent, size = iso_nauts_files(fp)["PN_VOX1.PAC"]
    begin = extent * SECTOR_RAW
    end = (extent + (size + SECTOR_USER - 1) // SECTOR_USER) * SECTOR_RAW
    markers = valid_jxs_markers(raw, begin, min(end, len(raw)))
    out = []
    for block_index, marker in enumerate(markers):
        region = raw[marker : marker + 0x800]
        cursor = 0
        while cursor < len(region):
            terminator = region.find(b"\0", cursor)
            if terminator < 0:
                break
            chunk = region[cursor:terminator]
            raw_offset = marker + cursor
            cursor = terminator + 1
            if raw_offset in known or cursor < 33 or not chunk:
                continue
            # Text must fit wholly in this Mode2/Form1 user-data sector.
            inner = raw_offset % SECTOR_RAW
            if inner < 24 or inner + len(chunk) + 1 > 24 + SECTOR_USER:
                continue
            text = voice_text(chunk)
            if text is None or not voice_candidate(text):
                continue
            out.append({
                "source": "jxs_voice",
                "source_file": source_name,
                "block_index": block_index,
                "block_raw_offset": marker,
                "local_offset": raw_offset - marker,
                "raw_offset": raw_offset,
                "old_byte_len": len(chunk),
                "old_bytes_hex": chunk.hex(" "),
                "text_with_breaks": text,
                "text": " ".join(text.split()),
            })
    return out, len(markers)


def movie_entries(fp, existing: list[dict[str, Any]], overrides: dict[str, Any]) -> tuple[list[dict[str, Any]], dict]:
    audit = scan_movies(fp, existing)
    approved = overrides.get("movie", {})
    cleanup = overrides.get("movie_source_cleanup", {})
    out = []
    review = []
    for candidate in audit["timed_candidates"] + audit["untimed_review"]:
        raw_text = candidate["text"]
        display = cleanup.get(raw_text, raw_text)
        key = " ".join(display.split())
        if key not in approved:
            review.append({"movie": candidate["movie"], "user_offset": candidate["user_offset"], "text": raw_text})
            continue
        user_offset = int(candidate["user_offset"])
        out.append({
            "source": "movie_ascii",
            "source_file": "NAUTS/MOVIE/" + candidate["movie"],
            "container": "MOVIE",
            "container_entry_name": candidate["movie"],
            "local_offset": int(candidate["local_offset"]),
            "user_offset": user_offset,
            "raw_offset": raw_offset_from_user_offset(user_offset),
            "old_byte_len": int(candidate["old_byte_len"]),
            "old_bytes_hex": candidate["raw_hex"],
            "text_with_breaks": display,
            "text": " ".join(display.split()),
            "has_movie_timing_header": candidate["has_movie_timing_header"],
            "audit_status": "approved_by_private_override",
        })
    return out, {"scan": {k: v for k, v in audit.items() if not isinstance(v, list)}, "review": review}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--base-master", type=Path, required=True)
    parser.add_argument("--base-edit", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True, help="Private JSON with menu/voice/movie translations")
    parser.add_argument("--out-master", type=Path, required=True)
    parser.add_argument("--out-edit", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = read_json(args.base_master)
    entries = payload["entries"]
    edits = read_json(args.base_edit)
    overrides = read_json(args.overrides)
    prior = previous_edits(args.out_master, args.out_edit)
    if len(entries) != len(edits):
        raise RuntimeError("Base master and edit lengths differ")
    original = {
        int(e["raw_offset"]): (
            dict(e),
            prior.get(
                (int(e["raw_offset"]), bytes.fromhex(e["old_bytes_hex"])),
                overrides.get("existing", {}).get(e["text_with_breaks"], value),
            ),
        )
        for e, value in zip(entries, edits)
    }
    if len(original) != len(entries):
        raise RuntimeError("Base catalog contains duplicate raw offsets")
    with args.source_bin.open("rb") as fp, mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        menus = menu_entries(fp)
        voices, marker_count = voice_entries(raw, fp, set(original), args.source_bin.name)
        movies, movie_audit = movie_entries(fp, entries, overrides)
        new_entries = menus + voices + movies
        for entry in new_entries:
            offset = int(entry["raw_offset"])
            if offset in original:
                raise RuntimeError(f"New source overlaps existing catalog: {offset:#x}")
            normalized = " ".join(entry["text_with_breaks"].split())
            group = {"bin_ascii": "menu", "jxs_voice": "voice", "movie_ascii": "movie"}[entry["source"]]
            choices = overrides.get(group, {})
            translation = choices.get(entry["text_with_breaks"], choices.get(normalized, entry["text_with_breaks"]))
            translation = prior.get((offset, bytes.fromhex(entry["old_bytes_hex"])), translation)
            original[offset] = (entry, translation)

    ordered = sorted(original.items())
    final_entries = []
    final_edits = []
    pending = []
    for index, (offset, (entry, edit)) in enumerate(ordered):
        entry["index"] = index
        final_entries.append(entry)
        final_edits.append(edit)
        if (entry in new_entries and edit == entry["text_with_breaks"]
                and edit not in overrides.get("intentionally_unchanged", [])
                and re.search(r"[A-Za-z]{3}", edit)):
            pending.append({"index": index, "raw_offset": offset, "source": entry["source"], "text": edit})
    write_json(args.out_master, {
        "format": "policenauts_audited_v1",
        "count": len(final_entries),
        "counts_by_source": dict(Counter(e["source"] for e in final_entries)),
        "entries": final_entries,
    })
    write_json(args.out_edit, final_edits)
    report = {
        "base_count": len(entries),
        "added_by_source": dict(Counter(e["source"] for e in new_entries)),
        "total_count": len(final_entries),
        "valid_voice_blocks": marker_count,
        "prior_edits_available": len(prior),
        "movie_audit": movie_audit,
        "new_strings_needing_translation": pending,
        "known_limitation": "Only privately approved movie candidates are patched; graphical text and in-game timing are not verified.",
    }
    write_json(args.report, report)
    print(json.dumps({k: v if k != "new_strings_needing_translation" else len(v) for k, v in report.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
