#!/usr/bin/env python3
"""Prove physical fit of untranslated-length Disc 1 text, without writing a BIN.

This audit cannot establish script pointer correctness, screen fit, complete
extraction, movie timing, or emulator stability. A clean physical-fit report is
not permission to publish a supposedly complete ROM.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from build_policenauts_tr_bin import load_edit, load_entries, movie_prefix, verify_source
from policenauts_disc_tools import (
    SECTOR_RAW, SECTOR_USER, dpk_records_from_image, iso_nauts_files,
    read_user_bytes_from_raw,
)
from policenauts_lossless_sz import rebuild_sz_bank
from policenauts_lossless_voice import rebuild_voice_sector
from policenauts_lossless_overlay import rebuild_overlay
from policenauts_text_format import GAME_MARKER_RE
from policenauts_turkish_font import encode_text


def audit(master: Path, edit: Path, source_bin: Path,
          overlay_relocations: dict[str, dict] | None = None) -> dict:
    entries = load_entries(master)
    translations = load_edit(edit, entries)
    verify_source(source_bin, entries)
    game = defaultdict(list)
    voice = defaultdict(list)
    movie = []
    menu = []
    unchanged = []
    for entry, translation in zip(entries, translations):
        kind = entry["source"]
        if len(translation.strip()) >= 20 and translation.strip() == entry["text_with_breaks"].strip():
            unchanged.append(entry["index"])
        if kind == "game_sz":
            game[(entry["container"], int(entry["container_entry_index"]))].append((entry, translation))
        elif kind == "jxs_voice":
            voice[int(entry["raw_offset"]) // SECTOR_RAW].append((entry, translation))
        elif kind == "movie_ascii":
            movie.append((entry, translation))
        elif kind == "bin_ascii":
            menu.append((entry, translation))
        else:
            raise ValueError(f"Unknown catalog source: {kind}")

    report = {
        "status": "NOT_RELEASE_READY",
        "source_bin": str(source_bin),
        "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        "edit_sha256": hashlib.sha256(edit.read_bytes()).hexdigest(),
        "entry_count": len(entries),
        "unchanged_long_english_ids": unchanged,
        "game": {"bank_count": 0, "entry_count": 0, "physical_fit_failures": [],
                 "banks_grown": [], "uncommanded_break_ids": [],
                 "pointers_checked": 0, "pointers_relocated": 0,
                 "boundary_e5_risks": []},
        "voice": {"sector_count": 0, "entry_count": 0, "physical_fit_failures": [],
                  "max_last_text_byte": 0},
        "movie": {"entry_count": len(movie), "fixed_span_overflow_ids": [],
                  "overwrites_live_data_ids": [], "encoding_failures": []},
        "menu": {"entry_count": len(menu), "fixed_span_overflow_ids": [],
                 "in_place_conflict_ids": [], "encoding_failures": [],
                 "relocations_checked": [], "overlay_rebuild_failures": []},
        "release_blockers": [
            "Recognized SZ 0x6F references are rebased; other nested script structures remain unverified",
            "Movie stream relocation and subtitle timing are not implemented",
            "Overlay binary relocation and unknown on-screen warnings are not mapped",
            "Screen width, full-disc coverage, and end-to-end emulator playthrough are unverified",
        ],
    }
    with source_bin.open("rb") as fp:
        files = iso_nauts_files(fp)
        for name in ("GAME1.DPK", "GAME2.DPK"):
            extent, size = files[name]
            base = extent * SECTOR_USER
            records = dpk_records_from_image(fp, base)
            offsets = sorted(record["data_offset"] for record in records)
            for record in records:
                if not record["name"].upper().endswith(".SZ"):
                    continue
                key = (name, record["index"])
                start = record["data_offset"]
                end = start + record["data_size"]
                next_start = next((offset for offset in offsets if offset > start), size)
                data = read_user_bytes_from_raw(fp, base + start, record["data_size"])
                report["game"]["bank_count"] += 1
                report["game"]["entry_count"] += len(game[key])
                report["game"]["uncommanded_break_ids"].extend(
                    entry["index"] for entry, translation in game[key]
                    if "\n" in translation and not GAME_MARKER_RE.findall(entry["raw_command_text"])
                )
                try:
                    pointer_mode = ("strict" if name == "GAME1.DPK" or
                                    record["name"] == "GAME01.SZ" else "catalog")
                    _rebuilt, result = rebuild_sz_bank(
                        data, game[key], next_start - end, pointer_mode)
                    report["game"]["pointers_checked"] += result.get("pointers_checked", 0)
                    report["game"]["pointers_relocated"] += result.get("pointers_relocated", 0)
                    e5_audit = result["boundary_e5_audit"]
                    if e5_audit["needs_structural_review"]:
                        report["game"]["boundary_e5_risks"].append({
                            "container": name, "record": record["index"],
                            "bank": record["name"], **e5_audit,
                        })
                    if result["growth_bytes"]:
                        report["game"]["banks_grown"].append({
                            "container": name, "record": record["index"],
                            "bytes": result["growth_bytes"], "available_gap": next_start - end,
                        })
                except (ValueError, AssertionError) as exc:
                    report["game"]["physical_fit_failures"].append({
                        "container": name, "record": record["index"], "error": str(exc),
                    })

        for sector_index, items in sorted(voice.items()):
            fp.seek(sector_index * SECTOR_RAW)
            sector = fp.read(SECTOR_RAW)
            report["voice"]["sector_count"] += 1
            report["voice"]["entry_count"] += len(items)
            try:
                _rebuilt, result = rebuild_voice_sector(sector, sector_index, items)
                report["voice"]["max_last_text_byte"] = max(
                    report["voice"]["max_last_text_byte"], result["last_text_byte"])
            except (ValueError, AssertionError) as exc:
                report["voice"]["physical_fit_failures"].append({
                    "sector": sector_index, "error": str(exc),
                })

        for entry, translation in movie:
            ident = entry["index"]
            try:
                encoded = movie_prefix(entry) + encode_text(translation).replace(b"\n", b"\x80|")
            except ValueError as exc:
                report["movie"]["encoding_failures"].append({"index": ident, "error": str(exc)})
                continue
            extra = len(encoded) - int(entry["old_byte_len"])
            if extra > 0:
                report["movie"]["fixed_span_overflow_ids"].append(ident)
                user_end = int(entry["user_offset"]) + int(entry["old_byte_len"]) + 1
                if any(read_user_bytes_from_raw(fp, user_end, extra)):
                    report["movie"]["overwrites_live_data_ids"].append(ident)

        menu_extent, _menu_container_size = files["BIN.DPK"]
        menu_base = menu_extent * SECTOR_USER
        overlays = {record["index"]: record
                    for record in dpk_records_from_image(fp, menu_base)}
        overlay_data = {}
        overlay_items = defaultdict(list)
        for entry, translation in menu:
            ident = entry["index"]
            record = overlays[int(entry["container_entry_index"])]
            if record["name"] != entry["container_entry_name"]:
                report["menu"]["encoding_failures"].append({
                    "index": ident, "error": "Overlay name/index mismatch",
                })
                continue
            if record["index"] not in overlay_data:
                overlay_data[record["index"]] = read_user_bytes_from_raw(
                    fp, menu_base + record["data_offset"], record["data_size"])
            data = overlay_data[record["index"]]
            overlay_items[record["name"]].append((entry, translation))
            if re.findall(r"%[-+0-9.]*[A-Za-z]", translation) != re.findall(
                r"%[-+0-9.]*[A-Za-z]", entry["text_with_breaks"]
            ):
                report["menu"]["encoding_failures"].append({
                    "index": ident, "error": "printf placeholders changed",
                })
                continue
            try:
                encoded = encode_text(translation)
            except ValueError as exc:
                report["menu"]["encoding_failures"].append({"index": ident, "error": str(exc)})
                continue
            if len(encoded) > int(entry["old_byte_len"]):
                report["menu"]["fixed_span_overflow_ids"].append(ident)
            local_end = int(entry["local_offset"]) + int(entry["old_byte_len"])
            if data[int(entry["local_offset"]):local_end] != bytes.fromhex(entry["old_bytes_hex"]):
                report["menu"]["encoding_failures"].append({
                    "index": ident, "error": "Overlay source bytes differ",
                })
                continue
            next_live = local_end
            while next_live < len(data) and data[next_live] == 0:
                next_live += 1
            if len(encoded) + 1 > next_live - int(entry["local_offset"]):
                report["menu"]["in_place_conflict_ids"].append(ident)
        if overlay_relocations is not None:
            plans = defaultdict(dict)
            for key, plan in overlay_relocations.items():
                name, separator, position = key.partition(":")
                if not separator or not position.isdecimal() or name not in overlay_items:
                    raise ValueError(f"Invalid or unused overlay relocation: {key}")
                plans[name][int(position)] = plan
            by_name = {record["name"]: record for record in overlays.values()}
            for name, items in sorted(overlay_items.items()):
                try:
                    rebuild_overlay(overlay_data[by_name[name]["index"]], items, plans[name])
                    report["menu"]["relocations_checked"].extend(
                        f"{name}:{position}" for position in sorted(plans[name]))
                except (ValueError, AssertionError) as exc:
                    report["menu"]["overlay_rebuild_failures"].append(
                        {"overlay": name, "error": str(exc)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--edit", type=Path, required=True)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--overlay-relocations", type=Path,
                        help="Validate an explicit, source-specific overlay pointer plan")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists() or args.report.resolve() in {
        args.master.resolve(), args.edit.resolve(), args.source_bin.resolve()
    }:
        parser.error("Report path must be new and must not overwrite an input")
    if args.overlay_relocations and args.overlay_relocations.resolve() == args.report.resolve():
        parser.error("Relocation plan cannot be the output report")
    plans = (json.loads(args.overlay_relocations.read_text(encoding="utf-8"))
             if args.overlay_relocations else None)
    if plans is not None and not isinstance(plans, dict):
        parser.error("Relocation plan must be a JSON object")
    report = audit(args.master, args.edit, args.source_bin, plans)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "status": report["status"], "entry_count": report["entry_count"],
        "game_banks": report["game"]["bank_count"],
        "game_physical_failures": len(report["game"]["physical_fit_failures"]),
        "sz_pointers_checked": report["game"]["pointers_checked"],
        "sz_pointers_relocated": report["game"]["pointers_relocated"],
        "sz_boundary_e5_risks": len(report["game"]["boundary_e5_risks"]),
        "voice_sectors": report["voice"]["sector_count"],
        "voice_physical_failures": len(report["voice"]["physical_fit_failures"]),
        "movie_overflow": len(report["movie"]["fixed_span_overflow_ids"]),
        "movie_live_data_conflicts": len(report["movie"]["overwrites_live_data_ids"]),
        "menu_overflow": len(report["menu"]["fixed_span_overflow_ids"]),
        "menu_live_data_conflicts": len(report["menu"]["in_place_conflict_ids"]),
        "menu_relocations_checked": len(report["menu"]["relocations_checked"]),
        "menu_overlay_rebuild_failures": len(report["menu"]["overlay_rebuild_failures"]),
        "report": str(args.report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
