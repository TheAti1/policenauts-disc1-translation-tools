#!/usr/bin/env python3
"""Build a patched Policenauts Disc 1 BIN/CUE from ordered translation JSON.

This script intentionally does not ship with game data, disc images, extracted
script text, or translation dumps. Users must provide their own private metadata
and source image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import policenauts_text_format as textfmt
from make_policenauts_rex_json import FORMAT as REX_FORMAT, KIND_BY_SOURCE
from policenauts_disc_tools import (
    SECTOR_RAW,
    SECTOR_USER,
    fix_mode2_form1_edc_ecc_for_sectors,
    update_dpk_checksums_for_patched_entries,
    write_user_bytes_to_raw,
)

DEFAULT_SOURCE_BIN = Path("Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].bin")
DEFAULT_SOURCE_CUE = Path("Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].cue")
DEFAULT_MASTER = Path("policenauts_texts_master_audited.json")
DEFAULT_EDIT = Path("policenauts_texts_tr_edit_audited.json")
DEFAULT_OUT_BIN = Path("Policenauts (Japan) (Disc 1) [TR Audited].bin")
DEFAULT_OUT_CUE = Path("Policenauts (Japan) (Disc 1) [TR Audited].cue")
DEFAULT_REPORT = Path("policenauts_tr_custom_build_report.json")
DEFAULT_CUTS = Path("policenauts_tr_custom_truncation_report.json")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_entries(master_path: Path) -> list[dict[str, Any]]:
    payload = read_json(master_path)
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError(f"{master_path} must contain an 'entries' list")
    return entries


def load_edit(edit_path: Path, master_entries: list[dict[str, Any]]) -> list[str]:
    payload = read_json(edit_path)
    expected_count = len(master_entries)
    if isinstance(payload, dict) and payload.get("format") == REX_FORMAT:
        if set(payload) != {"format", "count", "source_language", "target_language", "entries"}:
            raise RuntimeError("REX JSON root fields are invalid")
        rows = payload.get("entries")
        if (not isinstance(rows, list)
                or type(payload.get("count")) is not int
                or payload["count"] != expected_count
                or payload.get("source_language") != "en"
                or payload.get("target_language") != "tr"):
            raise RuntimeError("REX JSON header or entry count does not match the master")
        if len(rows) != expected_count:
            raise RuntimeError(f"REX JSON has {len(rows)} rows; master has {expected_count}")
        edits = []
        missing = []
        for index, (row, master) in enumerate(zip(rows, master_entries)):
            if (not isinstance(row, dict)
                    or set(row) != {"id", "index", "kind", "source", "translation"}
                    or row.get("id") != f"p{index:05d}"
                    or type(row.get("index")) is not int
                    or row["index"] != index
                    or row.get("kind") != KIND_BY_SOURCE.get(master["source"])
                    or row.get("source") != master["text_with_breaks"]):
                raise RuntimeError(f"REX source/ID/order differs from master at index {index}")
            translation = row.get("translation")
            if not isinstance(translation, str) or not translation.strip():
                missing.append(row["id"])
            else:
                edits.append(translation)
        if missing:
            raise RuntimeError(
                f"REX JSON has {len(missing)} untranslated entries; first IDs: {missing[:10]}"
            )
        return edits
    if not isinstance(payload, list):
        raise RuntimeError(f"{edit_path} must be a text-only list or {REX_FORMAT} document")
    if len(payload) != expected_count:
        raise RuntimeError(
            f"Text count mismatch: {edit_path} has {len(payload)} strings, "
            f"but the master metadata has {expected_count} entries"
        )
    if any(not isinstance(item, str) for item in payload):
        raise RuntimeError("Every translation entry must be a JSON string")
    return payload


def movie_prefix(entry: dict[str, Any]) -> bytes:
    raw = bytes.fromhex(str(entry["old_bytes_hex"])).replace(b"\x80|", b"\n")
    display = str(entry["text_with_breaks"]).encode("ascii", errors="ignore")
    # Some subtitles have source leading-byte guards consumed by the renderer.
    # Preserve their occupied width instead of dropping the first visible glyph.
    core = raw.rstrip(b" ")
    if core.endswith(display) and 0 < len(core) - len(display) <= 3:
        return b" " * (len(core) - len(display))
    return b""


def fit_one(entry: dict[str, Any], translated: str) -> tuple[str, str, bool]:
    """Return (command_text, display_text, was_truncated)."""
    source = str(entry["source"])
    original = str(entry["text_with_breaks"])

    if source == "game_sz":
        markers = textfmt.GAME_MARKER_RE.findall(str(entry["raw_command_text"]))
        chunk = bytes.fromhex(str(entry["old_bytes_hex"]))
        slots = sum(kind != "op" for kind, _blob in textfmt.game_tokens(chunk))
        command_text, was_truncated = textfmt.fit_layout(
            translated,
            original,
            markers,
            slots,
            lambda value: value.encode("ascii"),
        )
        return command_text, textfmt.game_display_text(command_text), was_truncated

    if source == "movie_ascii":
        markers = ["\n"] * original.count("\n")
        capacity = int(entry["old_byte_len"]) - len(movie_prefix(entry))
        command_text, was_truncated = textfmt.fit_layout(
            translated,
            original,
            markers,
            capacity,
            lambda value: value.replace("\n", "\x80|").encode("latin1"),
        )
        return command_text, command_text, was_truncated

    if source == "bin_ascii":
        source_formats = re.findall(r"%[-+0-9.]*[A-Za-z]", original)
        translated_formats = re.findall(r"%[-+0-9.]*[A-Za-z]", translated)
        if source_formats != translated_formats:
            raise RuntimeError(f"Printf placeholders changed: {original!r} -> {translated!r}")
        command_text, was_truncated = textfmt.fit_layout(
            translated, original, [], int(entry["old_byte_len"]),
            lambda value: value.encode("ascii"),
        )
        return command_text, command_text, was_truncated

    markers = ["\n"] * original.count("\n")
    command_text, was_truncated = textfmt.fit_layout(
        translated,
        original,
        markers,
        int(entry["old_byte_len"]),
        lambda value: value.encode("ascii"),
    )
    return command_text, command_text, was_truncated


def prepare_entries(
    entries: list[dict[str, Any]], edits: list[str]
) -> tuple[list[dict[str, Any]], Counter, list[dict[str, Any]]]:
    stats: Counter = Counter()
    cuts: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []

    for index, (entry, translated) in enumerate(zip(entries, edits)):
        item = dict(entry)
        command_text, display_text, was_truncated = fit_one(item, translated)
        item["index"] = index
        item["translation_unfitted"] = translated
        item["translation_fitted_command_text"] = command_text
        item["translation_fitted"] = display_text
        item["was_truncated"] = was_truncated
        if was_truncated:
            stats["truncated"] += 1
            cuts.append(
                {
                    "index": index,
                    "source": item["source"],
                    "original": item["text_with_breaks"],
                    "translation": translated,
                    "fitted": display_text,
                    "capacity": int(item["old_byte_len"]),
                }
            )
        stats[str(item["source"])] += 1
        prepared.append(item)

    return prepared, stats, cuts


def raw_sectors_for_raw_write(raw_offset: int, byte_count: int) -> set[int]:
    first = raw_offset // SECTOR_RAW
    last = (raw_offset + max(0, byte_count - 1)) // SECTOR_RAW
    return set(range(first, last + 1))


def verify_source(source_bin: Path, entries: list[dict[str, Any]]) -> None:
    """Reject metadata from a different disc before modifying a single byte."""
    with source_bin.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        for entry in entries:
            expected = bytes.fromhex(str(entry["old_bytes_hex"]))
            source = str(entry["source"])
            if source == "jxs_voice":
                offset = int(entry["raw_offset"])
                actual = raw[offset : offset + len(expected)]
                inner = offset % SECTOR_RAW
                if inner < 24 or inner + len(expected) + 1 > 24 + SECTOR_USER:
                    raise RuntimeError(f"JXS text crosses sector user data: index {entry['index']}")
            else:
                offset = int(entry["user_offset"])
                actual = bytearray()
                remaining = len(expected)
                while remaining:
                    sector, inner = divmod(offset, SECTOR_USER)
                    count = min(remaining, SECTOR_USER - inner)
                    raw_offset = sector * SECTOR_RAW + 24 + inner
                    actual.extend(raw[raw_offset : raw_offset + count])
                    offset += count
                    remaining -= count
            if actual != expected:
                raise RuntimeError(f"Source bytes mismatch at entry {entry['index']} ({source})")


def patch_bin(entries: list[dict[str, Any]], source_bin: Path, out_bin: Path) -> dict[str, Any]:
    shutil.copyfile(source_bin, out_bin)
    touched_sectors: set[int] = set()
    game_results: list[dict[str, Any]] = []
    patched = Counter()

    with out_bin.open("r+b") as target:
        for entry in entries:
            source = str(entry["source"])
            old_span = int(entry["old_byte_len"])
            fitted = str(entry["translation_fitted_command_text"])

            if source == "game_sz":
                old_chunk = bytes.fromhex(str(entry["old_bytes_hex"]))
                encoded, _slots = textfmt.build_game_chunk(old_chunk, fitted)
                blob = encoded + b"\0" + (b"\0" * (old_span - len(encoded)))
                user_offset = int(entry["user_offset"])
                touched_sectors.update(write_user_bytes_to_raw(target, user_offset, blob))
                game_results.append(
                    {
                        "source": "game_sz",
                        "status": "patchable_opcode_aware",
                        "container": entry["container"],
                        "container_entry_index": entry["container_entry_index"],
                        "container_entry_name": entry["container_entry_name"],
                        "source_file": entry.get("source_file"),
                        "local_offset": entry["local_offset"],
                        "user_offset": user_offset,
                        "patched": True,
                    }
                )
            elif source == "movie_ascii":
                encoded = movie_prefix(entry) + fitted.replace("\n", "\x80|").encode("latin1")
                blob = encoded + b"\0" + (b"\0" * (old_span - len(encoded)))
                touched_sectors.update(write_user_bytes_to_raw(target, int(entry["user_offset"]), blob))
            elif source == "bin_ascii":
                encoded = fitted.encode("ascii")
                blob = encoded + b"\0" + (b"\0" * (old_span - len(encoded)))
                touched_sectors.update(write_user_bytes_to_raw(target, int(entry["user_offset"]), blob))
                game_results.append({
                    "source": "bin_ascii",
                    "container": entry["container"],
                    "container_entry_index": entry["container_entry_index"],
                })
            else:
                encoded = fitted.encode("ascii")
                blob = encoded + b"\0" + (b"\0" * (old_span - len(encoded)))
                raw_offset = int(entry["raw_offset"])
                target.seek(raw_offset)
                target.write(blob)
                touched_sectors.update(raw_sectors_for_raw_write(raw_offset, len(blob)))

            patched[source] += 1

        crc_updates, crc_sectors = update_dpk_checksums_for_patched_entries(
            target, game_results
        )
        touched_sectors.update(crc_sectors)
        fixed_edc, fixed_ecc = fix_mode2_form1_edc_ecc_for_sectors(target, touched_sectors)

    return {
        "patched_by_source": dict(patched),
        "dpk_crc_updates": crc_updates,
        "touched_raw_sectors": len(touched_sectors),
        "fixed_edc_sectors": fixed_edc,
        "fixed_ecc_sectors": fixed_ecc,
        "output_bin_size": out_bin.stat().st_size,
    }


def write_cue(source_cue: Path, out_cue: Path, out_bin: Path) -> None:
    cue_text = source_cue.read_text(encoding="ascii", errors="ignore")
    lines = cue_text.splitlines()
    if not lines:
        raise RuntimeError(f"{source_cue} is empty")
    lines[0] = f'FILE "{out_bin.name}" BINARY'
    out_cue.write_text("\n".join(lines) + "\n", encoding="ascii")


def verify_exact_bytes(out_bin: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = out_bin.read_bytes()
    mismatches: list[dict[str, Any]] = []

    def read_user(user_offset: int, byte_count: int) -> bytes:
        out = bytearray()
        current = user_offset
        remaining = byte_count
        while remaining:
            sector = current // SECTOR_USER
            inner = current % SECTOR_USER
            take = min(remaining, SECTOR_USER - inner)
            raw_offset = sector * SECTOR_RAW + 24 + inner
            out.extend(raw[raw_offset : raw_offset + take])
            current += take
            remaining -= take
        return bytes(out)

    for entry in entries:
        source = str(entry["source"])
        fitted = str(entry["translation_fitted_command_text"])
        if source == "game_sz":
            expected = (
                textfmt.build_game_chunk(bytes.fromhex(str(entry["old_bytes_hex"])), fitted)[0]
                + b"\0"
            )
            actual = read_user(int(entry["user_offset"]), len(expected))
        elif source == "movie_ascii":
            expected = movie_prefix(entry) + fitted.replace("\n", "\x80|").encode("latin1") + b"\0"
            actual = read_user(int(entry["user_offset"]), len(expected))
        elif source == "bin_ascii":
            expected = fitted.encode("ascii") + b"\0"
            actual = read_user(int(entry["user_offset"]), len(expected))
        else:
            expected = fitted.encode("ascii") + b"\0"
            offset = int(entry["raw_offset"])
            actual = raw[offset : offset + len(expected)]
        if actual != expected:
            mismatches.append(
                {
                    "index": entry["index"],
                    "source": source,
                    "raw_offset": entry["raw_offset"],
                    "expected_prefix_hex": expected[:40].hex(),
                    "actual_prefix_hex": actual[:40].hex(),
                }
            )
    return mismatches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a patched Policenauts Disc 1 BIN/CUE from a plain array or REX JSON"
    )
    parser.add_argument("--source-bin", type=Path, default=DEFAULT_SOURCE_BIN)
    parser.add_argument("--source-cue", type=Path, default=DEFAULT_SOURCE_CUE)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--edit", type=Path, default=DEFAULT_EDIT)
    parser.add_argument("--out-bin", type=Path, default=DEFAULT_OUT_BIN)
    parser.add_argument("--out-cue", type=Path, default=DEFAULT_OUT_CUE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cuts", type=Path, default=DEFAULT_CUTS)
    parser.add_argument("--allow-truncate", action="store_true", help="Explicitly allow strings to be cut to fit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.source_bin.resolve() == args.out_bin.resolve():
        raise RuntimeError("Output BIN must not overwrite the English source BIN")
    if args.source_cue.resolve() == args.out_cue.resolve():
        raise RuntimeError("Output CUE must not overwrite the English source CUE")
    required = [args.source_bin, args.source_cue, args.master, args.edit]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required files: {missing}")

    entries = load_entries(args.master)
    edits = load_edit(args.edit, entries)
    prepared, stats, cuts = prepare_entries(entries, edits)
    if cuts and not args.allow_truncate:
        write_json(args.cuts, cuts)
        raise RuntimeError(f"{len(cuts)} translations do not fit. Shorten them or pass --allow-truncate.")

    verify_source(args.source_bin, prepared)

    partial_bin = args.out_bin.with_name(args.out_bin.name + ".partial")
    patch_report = patch_bin(prepared, args.source_bin, partial_bin)
    mismatches = verify_exact_bytes(partial_bin, prepared)
    if mismatches:
        write_json(args.report.with_suffix(".mismatches.json"), mismatches[:200])
        raise RuntimeError(f"Exact byte verification failed: {len(mismatches)} mismatches")

    os.replace(partial_bin, args.out_bin)
    write_cue(args.source_cue, args.out_cue, args.out_bin)

    write_json(args.cuts, cuts)
    report = {
        "source_bin": str(args.source_bin),
        "edit_json": str(args.edit),
        "master_json": str(args.master),
        "out_bin": str(args.out_bin),
        "out_cue": str(args.out_cue),
        "entry_count": len(prepared),
        "counts_by_source": dict(Counter(entry["source"] for entry in prepared)),
        "fit_stats": dict(stats),
        "patch_report": patch_report,
        "exact_byte_mismatches": 0,
        "out_bin_sha256": hashlib.sha256(args.out_bin.read_bytes()).hexdigest(),
    }
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
