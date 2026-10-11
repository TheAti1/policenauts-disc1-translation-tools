#!/usr/bin/env python3
"""Rebuild a private English catalog from text-free Disc 1 geometry.

Geometry stores byte locations and lengths only, never game dialogue or fonts.
It is pinned to one English-patched BIN hash and is not a completeness proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_policenauts_tr_bin import movie_prefix, verify_source
from extract_policenauts_audited import voice_text
from policenauts_disc_tools import (
    SECTOR_RAW, SECTOR_USER, RAW_USERDATA_OFFSET, _iso_records,
    dpk_records_from_image, iso_nauts_files, raw_offset_from_user_offset,
    read_user_bytes_from_raw,
)
from policenauts_lossless_sz import decoded_game_command
from policenauts_text_format import game_display_text


FORMAT = "policenauts-disc1-english-geometry-v1"
KINDS = {"game_sz", "jxs_voice", "movie_ascii", "bin_ascii"}


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def geometry_from_master(master: dict, expected_source_sha256: str) -> dict:
    entries = master.get("entries")
    if not isinstance(entries, list) or len(expected_source_sha256) != 64:
        raise ValueError("Invalid audited master or source SHA-256")
    rows = []
    for index, entry in enumerate(entries):
        kind = entry.get("source")
        if entry.get("index") != index or kind not in KINDS:
            raise ValueError(f"Invalid audited master entry {index}")
        row = {"index": index, "kind": kind,
               "byte_length": int(entry["old_byte_len"])}
        if kind == "jxs_voice":
            row.update(raw_offset=int(entry["raw_offset"]),
                       block_raw_offset=int(entry["block_raw_offset"]),
                       block_index=int(entry["block_index"]))
        else:
            row.update(container=entry["container"],
                       record_name=entry["container_entry_name"],
                       local_offset=int(entry["local_offset"]),
                       user_offset=int(entry["user_offset"]))
            if kind != "movie_ascii":
                row["record_index"] = int(entry["container_entry_index"])
            else:
                row["prefix_length"] = len(movie_prefix(entry))
        rows.append(row)
    return {"format": FORMAT, "source_sha256": expected_source_sha256,
            "count": len(rows), "entries": rows,
            "notice": "Offsets only; no text, pixels, fonts, or translation data."}


def reconstruct_master(source_bin: Path, geometry: dict) -> dict:
    rows = geometry.get("entries")
    if (geometry.get("format") != FORMAT or not isinstance(rows, list) or
            geometry.get("count") != len(rows) or
            source_sha256(source_bin) != geometry.get("source_sha256")):
        raise ValueError("Geometry is not valid for this source BIN")
    result = []
    with source_bin.open("rb") as stream:
        files = iso_nauts_files(stream)
        movie_files = _iso_records(stream, *files["MOVIE"])
        dpk = {}
        for container in {row["container"] for row in rows if row["kind"] not in ("movie_ascii", "jxs_voice")}:
            if container not in files:
                raise ValueError(f"Missing container: {container}")
            extent, _size = files[container]
            dpk[container] = (extent, dpk_records_from_image(stream, extent * SECTOR_USER))
        voice_extent, voice_size = files["PN_VOX1.PAC"]
        voice_start = voice_extent * SECTOR_RAW
        voice_end = voice_start + ((voice_size + SECTOR_USER - 1) // SECTOR_USER) * SECTOR_RAW
        for index, row in enumerate(rows):
            kind = row.get("kind")
            length = row.get("byte_length")
            if row.get("index") != index or kind not in KINDS or type(length) is not int or length <= 0:
                raise ValueError(f"Invalid geometry entry {index}")
            entry = {"index": index, "source": kind, "old_byte_len": length}
            if kind == "jxs_voice":
                raw_offset = int(row["raw_offset"])
                block = int(row["block_raw_offset"])
                inner = raw_offset % SECTOR_RAW
                if (not voice_start <= raw_offset < voice_end or
                        not voice_start <= block < voice_end or
                        block // SECTOR_RAW != raw_offset // SECTOR_RAW or
                        not RAW_USERDATA_OFFSET <= inner or
                        inner + length + 1 > RAW_USERDATA_OFFSET + SECTOR_USER):
                    raise ValueError(f"Voice location is invalid at {index}")
                stream.seek(raw_offset)
                raw = stream.read(length)
                text = voice_text(raw)
                if text is None or not text:
                    raise ValueError(f"Voice text cannot be decoded at {index}")
                entry.update(source_file="NAUTS/PN_VOX1.PAC", raw_offset=raw_offset,
                             block_raw_offset=block, block_index=int(row["block_index"]),
                             local_offset=raw_offset - block)
            else:
                container = row["container"]
                name = row["record_name"]
                local = int(row["local_offset"])
                user = int(row["user_offset"])
                if kind == "movie_ascii":
                    if container != "MOVIE" or name not in movie_files:
                        raise ValueError(f"Movie location is invalid at {index}")
                    extent, size = movie_files[name]
                    if local < 0 or local + length > size or user != extent * SECTOR_USER + local:
                        raise ValueError(f"Movie offset is invalid at {index}")
                    source_file = f"NAUTS/MOVIE/{name}"
                else:
                    extent, records = dpk[container]
                    record_index = int(row["record_index"])
                    if not 0 <= record_index < len(records):
                        raise ValueError(f"DPK record index is invalid at {index}")
                    record = records[record_index]
                    if (record["name"] != name or local < 0 or
                            local + length > record["data_size"] or
                            user != extent * SECTOR_USER + record["data_offset"] + local):
                        raise ValueError(f"DPK offset is invalid at {index}")
                    source_file = f"NAUTS/{container}/{name}"
                    entry["container_entry_index"] = record_index
                raw_offset = raw_offset_from_user_offset(user)
                raw = read_user_bytes_from_raw(stream, user, length)
                entry.update(source_file=source_file, container=container,
                             container_entry_name=name, local_offset=local,
                             user_offset=user, raw_offset=raw_offset)
                if kind == "game_sz":
                    command = decoded_game_command(raw)
                    text = game_display_text(command)
                    entry["raw_command_text"] = command
                elif kind == "movie_ascii":
                    prefix = int(row["prefix_length"])
                    if not 0 <= prefix <= 3 or prefix >= length:
                        raise ValueError(f"Movie prefix length is invalid at {index}")
                    text = raw[prefix:].replace(b"\x80|", b"\n").decode("ascii")
                else:
                    text = raw.split(b"\0", 1)[0].decode("ascii")
                if not text:
                    raise ValueError(f"Empty reconstructed text at {index}")
            entry.update(old_bytes_hex=raw.hex(" "), text_with_breaks=text,
                         text=" ".join(text.split()))
            result.append(entry)
    master = {"format": "policenauts-audited-source-v1", "count": len(result),
              "entries": result}
    verify_source(source_bin, result)
    return master


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Create text-free geometry from a private audited master")
    generate.add_argument("--source-bin", required=True, type=Path)
    generate.add_argument("--master", required=True, type=Path)
    generate.add_argument("--out-geometry", required=True, type=Path)
    reconstruct = commands.add_parser("reconstruct", help="Recover a private text catalog from a matching BIN")
    reconstruct.add_argument("--source-bin", required=True, type=Path)
    reconstruct.add_argument("--geometry", required=True, type=Path)
    reconstruct.add_argument("--out-master", required=True, type=Path)
    compare = commands.add_parser("compare", help="Verify geometry reproduces a private audited master")
    compare.add_argument("--source-bin", required=True, type=Path)
    compare.add_argument("--geometry", required=True, type=Path)
    compare.add_argument("--master", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        if args.out_geometry.exists():
            raise FileExistsError(args.out_geometry)
        master = json.loads(args.master.read_text(encoding="utf-8"))
        verify_source(args.source_bin, master["entries"])
        geometry = geometry_from_master(master, source_sha256(args.source_bin))
        args.out_geometry.write_text(json.dumps(geometry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {geometry['count']} text-free geometry entries")
    else:
        geometry = json.loads(args.geometry.read_text(encoding="utf-8"))
        master = reconstruct_master(args.source_bin, geometry)
        if args.command == "reconstruct":
            if args.out_master.exists():
                raise FileExistsError(args.out_master)
            args.out_master.write_text(json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Reconstructed {master['count']} private English source entries")
        else:
            source_entries = json.loads(args.master.read_text(encoding="utf-8"))["entries"]
            fields = ("source", "old_byte_len", "old_bytes_hex", "text_with_breaks",
                      "raw_command_text", "container", "container_entry_index",
                      "container_entry_name", "local_offset", "user_offset",
                      "raw_offset", "block_raw_offset", "block_index")
            differences = [(index, field)
                           for index, (expected, actual) in enumerate(zip(source_entries, master["entries"]))
                           for field in fields if expected.get(field) != actual.get(field)]
            if len(source_entries) != master["count"] or differences:
                raise ValueError(f"Geometry differs from audited master: {differences[:10]}")
            print(f"PASS: {master['count']} source rows and audited fields match exactly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
