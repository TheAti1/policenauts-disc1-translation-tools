#!/usr/bin/env python3
"""Build an experimental Disc 1 BIN with selected overlays and movies.

This is NOT a finished translation and cannot certify script pointers,
complete text coverage, subtitle timing, or safe gameplay.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import struct

from build_policenauts_tr_bin import load_edit, load_entries, verify_source, write_cue
from policenauts_disc_tools import (
    SECTOR_RAW, SECTOR_USER, _iso_records, dpk_records_from_image,
    fix_mode2_form1_edc_ecc_for_sectors, iso_nauts_files,
    read_user_bytes_from_raw, update_dpk_checksums_for_patched_entries,
    write_user_bytes_to_raw,
)
from policenauts_lossless_sz import encode_game_chunk, rebuild_sz_bank
from policenauts_lossless_voice import rebuild_voice_sector
from policenauts_lossless_overlay import rebuild_overlay
from policenauts_lossless_movie import encode_movie_slot_in_record
from policenauts_movie_integrity import movie_records, validate_dialogue_slot
from policenauts_turkish_font import patch_fonts
from audit_policenauts_sz_anchor_drift import audit_anchor_drift


# GAME1's first three GAME01 loader fragments are addressed outside the
# recognized 0x6F relocation path. Growth has reproducibly triggered Read
# Error 10007. GAME2 has the same fragment layout and is guarded defensively;
# its translated runtime path has not been tested.
FIXED_LOADER_FIELDS = {
    "GAME1.DPK": {
        17061: ("File loaded.", 15),
        17077: ("An error occurred during load", 33),
        17134: ("ing.", 8),
    },
    "GAME2.DPK": {
        17061: ("File loaded.", 16),
        17078: ("An error occurred during\nloa", 32),
        17134: ("ding.", 13),
    },
}


def verify_fixed_loader_widths(container: str,
                               items: list[tuple[dict, str]]) -> None:
    fields = FIXED_LOADER_FIELDS[container]
    seen = set()
    for entry, translation in items:
        offset = int(entry["local_offset"])
        field = fields.get(offset)
        if field is None:
            continue
        seen.add(offset)
        source_text, source_width = field
        if entry["text_with_breaks"] != source_text:
            raise ValueError(f"GAME01 loader source changed at {entry['index']}")
        encoded, _ = encode_game_chunk(entry, translation)
        if len(encoded) != source_width:
            raise ValueError(
                f"GAME01 loader entry {entry['index']} must stay {source_width} "
                f"bytes; translation encodes to {len(encoded)} bytes"
            )
    if seen != fields.keys():
        raise ValueError(f"GAME01 loader catalog is incomplete: {sorted(fields.keys() - seen)}")


def catalogued_movie_names(entries: list[dict]) -> tuple[str, ...]:
    return tuple(sorted({entry["container_entry_name"] for entry in entries
                         if entry["source"] == "movie_ascii"}))


def planned_changes(source_bin: Path, entries: list[dict], translations: list[str],
                    overlay_names: tuple[str, ...] = (),
                    relocation_plans: dict[str, dict] | None = None,
                    movie_names: tuple[str, ...] = ()
                    ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    if len(set(overlay_names)) != len(overlay_names):
        raise ValueError("Duplicate --overlay name")
    available = {entry["container_entry_name"] for entry in entries
                 if entry["source"] == "bin_ascii"}
    if not set(overlay_names) <= available:
        raise ValueError(f"Unknown catalogued overlay: {sorted(set(overlay_names) - available)}")
    movie_available = {entry["container_entry_name"] for entry in entries
                       if entry["source"] == "movie_ascii"}
    if len(set(movie_names)) != len(movie_names) or not set(movie_names) <= movie_available:
        raise ValueError(f"Unknown or duplicate catalogued movie: {movie_names}")
    relocation_plans = relocation_plans or {}
    if not isinstance(relocation_plans, dict):
        raise ValueError("Relocation plans must be an object")
    by_overlay = defaultdict(dict)
    for key, plan in relocation_plans.items():
        name, separator, position = key.partition(":")
        if not separator or name not in overlay_names or not position.isdecimal():
            raise ValueError(f"Invalid or unselected relocation overlay at {key}")
        by_overlay[name][int(position)] = plan
    game = defaultdict(list)
    voice = defaultdict(list)
    overlays = defaultdict(list)
    movie_items = defaultdict(list)
    for entry, translation in zip(entries, translations):
        if entry["source"] == "game_sz":
            game[(entry["container"], int(entry["container_entry_index"]))].append((entry, translation))
        elif entry["source"] == "jxs_voice":
            voice[int(entry["raw_offset"]) // SECTOR_RAW].append((entry, translation))
        elif entry["source"] == "bin_ascii" and entry["container_entry_name"] in overlay_names:
            overlays[entry["container_entry_name"]].append((entry, translation))
        elif entry["source"] == "movie_ascii" and entry["container_entry_name"] in movie_names:
            movie_items[entry["container_entry_name"]].append((entry, translation))
    banks = []
    sectors = []
    overlay_changes = []
    movie_changes = []
    with source_bin.open("rb") as fp:
        files = iso_nauts_files(fp)
        for name in ("GAME1.DPK", "GAME2.DPK"):
            extent, file_size = files[name]
            base = extent * SECTOR_USER
            records = dpk_records_from_image(fp, base)
            offsets = sorted(row["data_offset"] for row in records)
            for record in records:
                if not record["name"].upper().endswith(".SZ"):
                    continue
                key = (name, record["index"])
                start = record["data_offset"]
                old_end = start + record["data_size"]
                next_start = next((value for value in offsets if value > start), file_size)
                if old_end > next_start:
                    raise ValueError(f"DPK entries overlap near {key}")
                original = read_user_bytes_from_raw(fp, base + start, record["data_size"])
                if record["name"] == "GAME01.SZ" and name in FIXED_LOADER_FIELDS:
                    verify_fixed_loader_widths(name, game[key])
                pointer_mode = ("strict" if name == "GAME1.DPK" or
                                record["name"] == "GAME01.SZ" else "catalog")
                rebuilt, result = rebuild_sz_bank(
                    original, game[key], next_start - old_end, pointer_mode)
                banks.append({"container": name, "record": record["index"],
                              "user_offset": base + start,
                              "size_field_user_offset": base + record["record_offset"] + 16,
                              "original_size": record["data_size"],
                              "data": rebuilt, "entry_count": result.get("entries", 0),
                              "pointers_checked": result.get("pointers_checked", 0),
                              "pointers_relocated": result.get("pointers_relocated", 0),
                              "boundary_e5_audit": result["boundary_e5_audit"]})
        for sector_index, items in sorted(voice.items()):
            fp.seek(sector_index * SECTOR_RAW)
            sector = fp.read(SECTOR_RAW)
            rebuilt, result = rebuild_voice_sector(sector, sector_index, items)
            sectors.append({"sector": sector_index, "data": rebuilt[24:2072],
                            "entry_count": result["entries"]})
        if overlays:
            extent, _size = files["BIN.DPK"]
            base = extent * SECTOR_USER
            records = {record["name"]: record
                       for record in dpk_records_from_image(fp, base)}
            for name, items in sorted(overlays.items()):
                record = records[name]
                original = read_user_bytes_from_raw(
                    fp, base + record["data_offset"], record["data_size"])
                rebuilt, result = rebuild_overlay(original, items, by_overlay[name])
                overlay_changes.append({"name": name, "record": record["index"],
                                        "user_offset": base + record["data_offset"],
                                        "data": rebuilt, "entry_count": result["entries"],
                                        "changed": result["changed"]})
        movie_files = (_iso_records(fp, *files["MOVIE"]) if movie_items else {})
        for name, items in sorted(movie_items.items()):
            extent, size = movie_files[name]
            source_movie = read_user_bytes_from_raw(fp, extent * SECTOR_USER, size)
            records = movie_records(source_movie)
            for entry, translation in items:
                user_offset = int(entry["user_offset"])
                local_offset = user_offset - extent * SECTOR_USER
                blob = encode_movie_slot_in_record(
                    entry, translation, source_movie, records, local_offset)
                validate_dialogue_slot(
                    source_movie, records, local_offset,
                    bytes.fromhex(entry["old_bytes_hex"]) + b"\0", blob)
                movie_changes.append({"name": name, "index": entry["index"],
                                      "user_offset": user_offset, "data": blob})
    return banks, sectors, overlay_changes, movie_changes


def build(source_bin: Path, source_cue: Path, master: Path, edit: Path,
          out_bin: Path, out_cue: Path, report_path: Path,
          overlay_names: tuple[str, ...] = (),
          relocation_plans: dict[str, dict] | None = None,
          movie_names: tuple[str, ...] = ()) -> dict:
    paths = [source_bin, source_cue, master, edit, out_bin, out_cue, report_path]
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("All input and output paths must be distinct")
    if any(path.exists() for path in (out_bin, out_cue, report_path)):
        raise FileExistsError("Probe outputs must be new; no files will be overwritten")
    entries = load_entries(master)
    translations = load_edit(edit, entries)
    verify_source(source_bin, entries)
    banks, sectors, overlays, movies = planned_changes(
        source_bin, entries, translations, overlay_names, relocation_plans,
        movie_names)
    game01_items = [(entry, translation)
                    for entry, translation in zip(entries, translations)
                    if entry.get("container") == "GAME1.DPK" and
                    entry.get("container_entry_name") == "GAME01.SZ"]
    game01_drift = audit_anchor_drift(game01_items, (22943,))
    shutil.copyfile(source_bin, out_bin)
    touched = set()
    modified_records = []
    try:
        with out_bin.open("r+b") as target:
            for bank in banks:
                touched.update(write_user_bytes_to_raw(target, bank["user_offset"], bank["data"]))
                if len(bank["data"]) != bank["original_size"]:
                    touched.update(write_user_bytes_to_raw(
                        target, bank["size_field_user_offset"], struct.pack("<I", len(bank["data"]))))
                modified_records.append({"container": bank["container"],
                                         "container_entry_index": bank["record"]})
            for sector in sectors:
                target.seek(sector["sector"] * SECTOR_RAW + 24)
                target.write(sector["data"])
                touched.add(sector["sector"])
            for overlay in overlays:
                touched.update(write_user_bytes_to_raw(
                    target, overlay["user_offset"], overlay["data"]))
                modified_records.append({"container": "BIN.DPK",
                                         "container_entry_index": overlay["record"]})
            for movie in movies:
                touched.update(write_user_bytes_to_raw(
                    target, movie["user_offset"], movie["data"]))
            font_sectors, fonts = patch_fonts(target)
            touched.update(font_sectors)
            modified_records.extend(fonts)
            checksum_count, checksum_sectors = update_dpk_checksums_for_patched_entries(
                target, modified_records)
            touched.update(checksum_sectors)
            fixed_edc, fixed_ecc = fix_mode2_form1_edc_ecc_for_sectors(target, touched)
            for bank in banks:
                if read_user_bytes_from_raw(target, bank["user_offset"], len(bank["data"])) != bank["data"]:
                    raise AssertionError(f"Rebuilt SZ bytes differ in {bank['container']}/{bank['record']}")
            for sector in sectors:
                target.seek(sector["sector"] * SECTOR_RAW + 24)
                if target.read(2048) != sector["data"]:
                    raise AssertionError(f"Voice sector bytes differ at {sector['sector']}")
            for overlay in overlays:
                if read_user_bytes_from_raw(target, overlay["user_offset"],
                                            len(overlay["data"])) != overlay["data"]:
                    raise AssertionError(f"Overlay bytes differ in {overlay['name']}")
            for movie in movies:
                if read_user_bytes_from_raw(target, movie["user_offset"],
                                            len(movie["data"])) != movie["data"]:
                    raise AssertionError(f"Movie subtitle bytes differ at {movie['index']}")
        write_cue(source_cue, out_cue, out_bin)
        remaining_overlays = {entry["container_entry_name"] for entry in entries
                              if entry["source"] == "bin_ascii"} - set(overlay_names)
        remaining_movies = {entry["container_entry_name"] for entry in entries
                            if entry["source"] == "movie_ascii"} - set(movie_names)
        unpatched_sources = []
        if remaining_movies:
            unpatched_sources.append("movie_ascii" if not movies else
                                     "movie_ascii (other movies)")
        if remaining_overlays:
            unpatched_sources.append("bin_ascii" if not overlays else
                                     "bin_ascii (other overlays)")
        report = {
            "status": "EXPERIMENTAL_NOT_RELEASE_READY",
            "source_bin": str(source_bin), "output_bin": str(out_bin),
            "game_banks": len(banks),
            "game_entries": sum(bank["entry_count"] for bank in banks),
            "sz_pointers_checked": sum(bank["pointers_checked"] for bank in banks),
            "sz_pointers_relocated": sum(bank["pointers_relocated"] for bank in banks),
            "game1_game01_anchor_drift": {
                key: value for key, value in game01_drift.items()
                if key not in ("anchors", "largest_fixed_interval_overflows")
            },
            "sz_boundary_e5_risks": [
                {"container": bank["container"], "record": bank["record"],
                 **bank["boundary_e5_audit"]}
                for bank in banks if bank["boundary_e5_audit"]["needs_structural_review"]
            ],
            "voice_sectors": len(sectors),
            "voice_entries": sum(sector["entry_count"] for sector in sectors),
            "overlays": [{"name": overlay["name"], "entries": overlay["entry_count"],
                          "changed": overlay["changed"]} for overlay in overlays],
            "overlay_relocations": sorted((relocation_plans or {}).keys()),
            "movies": {name: sum(movie["name"] == name for movie in movies)
                       for name in sorted(movie_names)},
            "font_records": len(fonts), "dpk_checksums_updated": checksum_count,
            "touched_sectors": len(touched), "fixed_edc": fixed_edc,
            "fixed_ecc": fixed_ecc,
            "unpatched_sources": unpatched_sources,
            "runtime_safety": ("KNOWN_WRONG_DIALOGUE_AT_WEDDING_PHOTO"
                               if game01_drift["checkpoints"]["22943"]["drift_bytes"]
                               else "UNVERIFIED"),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    except Exception:
        # Preserve the partial probe for forensic inspection; never touch inputs.
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--source-cue", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--edit", type=Path, required=True)
    parser.add_argument("--out-bin", type=Path, required=True)
    parser.add_argument("--out-cue", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--overlay", action="append", default=[],
                        help="Patch one in-place BIN.DPK overlay in this diagnostic image")
    parser.add_argument("--overlay-relocations", type=Path,
                        help="Private JSON mapping overlay:offset to verified pointer moves")
    parser.add_argument("--movie", action="append", default=[],
                        help="Patch one wholly fitting MOV subtitle set in this diagnostic image")
    parser.add_argument("--all-catalogued-movies", action="store_true",
                        help="Patch every movie in the catalog; each slot must fit without truncation")
    args = parser.parse_args()
    if args.all_catalogued_movies and args.movie:
        parser.error("Use --all-catalogued-movies or --movie, not both")
    if args.overlay_relocations and args.overlay_relocations.resolve() in {
        args.out_bin.resolve(), args.out_cue.resolve(), args.report.resolve()
    }:
        parser.error("Relocation plan cannot be an output file")
    relocation_plans = (json.loads(args.overlay_relocations.read_text(encoding="utf-8"))
                        if args.overlay_relocations else {})
    movie_names = (catalogued_movie_names(load_entries(args.master))
                   if args.all_catalogued_movies else tuple(args.movie))
    report = build(args.source_bin, args.source_cue, args.master, args.edit,
                   args.out_bin, args.out_cue, args.report, tuple(args.overlay),
                   relocation_plans, movie_names)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
