#!/usr/bin/env python3
"""Repack aligned jXS subtitle records in their original PSX data sector.

Header word zero is a nominal record length/stride. It is zero in some middle
records, so physical parsing always follows the aligned NUL terminator.
"""

from __future__ import annotations

import struct

from policenauts_disc_tools import RAW_USERDATA_OFFSET, SECTOR_RAW, SECTOR_USER
from policenauts_turkish_font import encode_text


def encode_voice(translation: str, source_bytes: bytes) -> bytes:
    """Retain the English patch's special standalone dash and CO2 styling."""
    if "\0" in translation:
        raise ValueError("Voice translation contains a NUL")
    output = bytearray()
    styled_dash = b"\xd0\x06" in source_bytes
    styled_co2 = b"CO\x80#2" in source_bytes
    position = 0
    while position < len(translation):
        if styled_co2 and translation.startswith("CO2", position):
            output.extend(b"CO\x80#2")
            position += 3
            continue
        char = translation[position]
        if styled_dash and char == "-":
            before = translation[position - 1] if position else ""
            after = translation[position + 1] if position + 1 < len(translation) else ""
            if not (before.isalnum() and after.isalnum()):
                output.extend(b"\xd0\x06")
                position += 1
                continue
        output.extend(encode_text(char))
        position += 1
    return bytes(output)


def voice_records(sector: bytes, items: list[tuple[dict, str]], sector_index: int
                  ) -> tuple[list[dict], int, int]:
    """Parse every record between catalogued strings, including blank hops."""
    if len(sector) != SECTOR_RAW or not items:
        raise ValueError("Expected one complete sector and at least one subtitle")
    if sector[15] != 2 or sector[18] & 0x20:
        raise ValueError("jXS subtitle sector must be Mode2/Form1")
    ordered = sorted(items, key=lambda pair: int(pair[0]["raw_offset"]))
    base = sector_index * SECTOR_RAW
    by_start = {}
    for entry, translation in ordered:
        start = int(entry["raw_offset"]) - base
        if start in by_start or start % 4:
            raise ValueError("Duplicate or unaligned subtitle source")
        by_start[start] = (entry, translation)
    first = min(by_start) - 16
    last = max(by_start)
    limit = RAW_USERDATA_OFFSET + SECTOR_USER
    if first < RAW_USERDATA_OFFSET or last >= limit:
        raise ValueError("Subtitle records cross user-data sector bounds")
    records = []
    position = first
    seen = set()
    while position + 16 <= last:
        start = position + 16
        end = sector.find(b"\0", start, limit)
        if end < 0:
            raise ValueError("Unterminated jXS subtitle")
        aligned_end = (end + 4) & ~3
        if aligned_end > limit or any(sector[end + 1:aligned_end]):
            raise ValueError("Nonzero jXS record padding")
        old = sector[start:end]
        if start in by_start:
            entry, translation = by_start[start]
            if old != bytes.fromhex(entry["old_bytes_hex"]):
                raise ValueError(f"Source voice bytes differ at {entry['index']}")
            if len(old) != int(entry["old_byte_len"]):
                raise ValueError("Voice source length differs")
            payload = encode_voice(translation, old)
            seen.add(start)
        else:
            if old != b"  ":
                raise ValueError(f"Uncatalogued nonblank jXS record at {start}")
            payload = old
        stride, _timing, _duration, reserved = struct.unpack_from("<IIII", sector, position)
        if reserved:
            raise ValueError("Unknown nonzero jXS record control word")
        records.append({"position": position, "start": start, "end": end,
                        "aligned_end": aligned_end, "stride": stride,
                        "header": sector[position:start], "payload": payload,
                        "catalogued": start in by_start})
        position = aligned_end
    if seen != by_start.keys():
        raise ValueError("Catalogued jXS strings do not follow record layout")
    for current, following in zip(records, records[1:]):
        stride = current["stride"]
        if stride and current["start"] + stride != following["start"]:
            raise ValueError("jXS stride does not target the next record")
    return records, first, records[-1]["end"] + 1


def rebuild_voice_sector(sector: bytes, sector_index: int,
                         items: list[tuple[dict, str]]) -> tuple[bytes, dict]:
    """Rebase strides and preserve timing, record alignment, and live tail."""
    records, first, old_end = voice_records(sector, items, sector_index)
    limit = RAW_USERDATA_OFFSET + SECTOR_USER
    next_live = old_end
    while next_live < limit and sector[next_live] == 0:
        next_live += 1

    rebuilt = bytearray()
    new_starts = []
    header_positions = []
    for record in records:
        header_positions.append(first + len(rebuilt))
        rebuilt.extend(record["header"])
        new_starts.append(first + len(rebuilt))
        rebuilt.extend(record["payload"])
        rebuilt.append(0)
        rebuilt.extend(b"\0" * (-(first + len(rebuilt)) % 4))

    rebased = 0
    for index, record in enumerate(records):
        old_stride = record["stride"]
        if not old_stride:
            continue
        if index + 1 < len(records):
            target = new_starts[index + 1]
        else:
            target = record["start"] + old_stride
            if target - 16 < old_end or target > limit or target % 4:
                raise ValueError("Last jXS stride has an unknown external target")
            if target != next_live + 16:
                raise ValueError("Last jXS stride does not target a live record")
            if len(rebuilt) + first > next_live:
                raise ValueError("Translated jXS text overlaps the next record")
        new_stride = target - new_starts[index]
        if not 0 < new_stride <= 0xFFFFFFFF or new_stride % 4:
            raise ValueError("Translated jXS stride is invalid")
        struct.pack_into("<I", rebuilt, header_positions[index] - first, new_stride)
        rebased += new_stride != old_stride

    new_end = first + len(rebuilt)
    if new_end > next_live:
        raise ValueError("Voice growth would overwrite nonzero sector data or user-data end")
    result = bytearray(sector)
    result[first:new_end] = rebuilt
    result[new_end:max(new_end, old_end)] = b"\0" * max(0, old_end - new_end)
    return bytes(result), {"sector": sector_index,
                           "entries": sum(record["catalogued"] for record in records),
                           "blank_records": len(records) - len(items),
                           "records": len(records), "strides_rebased": rebased,
                           "growth_bytes": new_end - records[-1]["aligned_end"],
                           "last_text_byte": new_end - 1}


def rebuild_voice_sector_fixed(sector: bytes, sector_index: int,
                               items: list[tuple[dict, str]], *,
                               overflow_policy: str = "raise") -> tuple[bytes, dict]:
    """Replace voice text at fixed starts, rejecting rather than cutting overflow."""
    if overflow_policy not in ("raise", "source"):
        raise ValueError("Unknown fixed voice overflow policy")
    records, _first, _old_end = voice_records(sector, items, sector_index)
    by_start = {int(entry["raw_offset"]) % SECTOR_RAW: (entry, translation)
                for entry, translation in items}
    rebuilt = bytearray(sector)
    overflow_entries = []
    for record in records:
        if not record["catalogued"]:
            continue
        entry, translation = by_start[record["start"]]
        payload = encode_voice(translation, bytes.fromhex(entry["old_bytes_hex"]))
        capacity = record["aligned_end"] - record["start"] - 1
        if len(payload) > capacity:
            if overflow_policy == "raise":
                raise ValueError(
                    f"Voice entry {entry['index']} needs {len(payload)} bytes but fixed "
                    f"record at {record['start']} has {capacity}; no text was cut"
                )
            overflow_entries.append({"index": entry["index"], "needed": len(payload),
                                     "capacity": capacity})
            continue
        rebuilt[record["start"]:record["aligned_end"]] = (
            payload + b"\0" * (record["aligned_end"] - record["start"] - len(payload))
        )
    return bytes(rebuilt), {
        "sector": sector_index,
        "status": "FIXED_OFFSETS_EXPERIMENTAL",
        "entries": len(items),
        "translated_entries": len(items) - len(overflow_entries),
        "source_fallback_entries": overflow_entries,
        "records": len(records),
        "record_starts_preserved": True,
        "timing_headers_preserved": True,
    }
