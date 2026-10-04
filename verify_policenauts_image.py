#!/usr/bin/env python3
"""Independently verify changed sectors, DPK checksums and movie timing bytes."""

from __future__ import annotations

import argparse
import json
import mmap
import re
import struct
from collections import defaultdict
from pathlib import Path

from policenauts_disc_tools import (
    SECTOR_RAW,
    SECTOR_USER,
    compute_cdrom_edc,
    compute_crc32_bzip2,
    compute_mode2_form1_ecc,
    dpk_records_from_image,
    iso_nauts_files,
    read_user_bytes_from_raw,
)


def verify_sectors(source: Path, patched: Path) -> int:
    if source.stat().st_size != patched.stat().st_size:
        raise RuntimeError("Disc image sizes differ")
    if source.stat().st_size % SECTOR_RAW:
        raise RuntimeError("Image size is not a multiple of 2352 bytes")
    changed = 0
    with source.open("rb") as original, patched.open("rb") as target:
        with mmap.mmap(original.fileno(), 0, access=mmap.ACCESS_READ) as a:
            with mmap.mmap(target.fileno(), 0, access=mmap.ACCESS_READ) as b:
                for sector in range(len(a) // SECTOR_RAW):
                    pos = sector * SECTOR_RAW
                    if a[pos : pos + SECTOR_RAW] == b[pos : pos + SECTOR_RAW]:
                        continue
                    changed += 1
                    data = b[pos : pos + SECTOR_RAW]
                    if data[:24] != a[pos : pos + 24]:
                        raise RuntimeError(f"Sector {sector}: CD header/subheader changed")
                    if data[0:12] != b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00":
                        raise RuntimeError(f"Sector {sector}: invalid sync header")
                    if data[15] != 2 or data[18] & 0x20:
                        raise RuntimeError(f"Sector {sector}: changed non-Mode2/Form1 data")
                    expected_edc = compute_cdrom_edc(data[16:2072])
                    actual_edc = struct.unpack_from("<I", data, 2072)[0]
                    if actual_edc != expected_edc:
                        raise RuntimeError(f"Sector {sector}: EDC mismatch")
                    p_ecc, q_ecc = compute_mode2_form1_ecc(data)
                    if data[2076:2248] != p_ecc or data[2248:2352] != q_ecc:
                        raise RuntimeError(f"Sector {sector}: ECC mismatch")
    return changed


def verify_dpk(patched: Path, entries: list[dict]) -> int:
    touched: dict[str, set[int]] = defaultdict(set)
    for entry in entries:
        if entry["source"] in ("game_sz", "bin_ascii"):
            touched[entry["container"]].add(int(entry["container_entry_index"]))
    checked = 0
    with patched.open("rb") as fp:
        files = iso_nauts_files(fp)
        for name, indices in touched.items():
            extent, size = files[name]
            base = extent * SECTOR_USER
            records = dpk_records_from_image(fp, base)
            for index in indices:
                record = records[index]
                if record["data_offset"] + record["data_size"] > size:
                    raise RuntimeError(f"{name} entry {index} exceeds its ISO file")
                data = read_user_bytes_from_raw(
                    fp, base + record["data_offset"], record["data_size"]
                )
                if compute_crc32_bzip2(data) != record["checksum"]:
                    raise RuntimeError(f"{name} entry {index} has a bad DPK CRC")
                checked += 1
    return checked


def verify_movie_timing(source: Path, patched: Path, entries: list[dict]) -> int:
    checked = 0
    marker = re.compile(b"\x00\x38[\x01\x02]\x00\x01\x00")
    with source.open("rb") as original, patched.open("rb") as target:
        for entry in entries:
            if entry["source"] != "movie_ascii":
                continue
            after_text = int(entry["user_offset"]) + int(entry["old_byte_len"])
            before = read_user_bytes_from_raw(original, after_text, 64)
            after = read_user_bytes_from_raw(target, after_text, 64)
            match = marker.search(before)
            if match is None:
                continue
            start = max(0, match.start() - 4)
            end = min(64, match.end() + 4)
            if before[start:end] != after[start:end]:
                raise RuntimeError(f"Movie timing bytes changed at entry {entry['index']}")
            checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bin", type=Path, required=True)
    parser.add_argument("--patched-bin", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads(args.master.read_text(encoding="utf-8"))["entries"]
    report = {
        "changed_valid_form1_sectors": verify_sectors(args.source_bin, args.patched_bin),
        "verified_dpk_entries": verify_dpk(args.patched_bin, entries),
        "preserved_movie_timing_headers": verify_movie_timing(args.source_bin, args.patched_bin, entries),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
