#!/usr/bin/env python3
"""Read-only boundaries for the VMNK/KLBS movie container."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class MovieRecord:
    start: int
    end: int
    kind: bytes


def movie_records(movie: bytes) -> list[MovieRecord]:
    if len(movie) < 0x800 or movie[:4] != b"VMNK":
        raise ValueError("Movie is not VMNK")
    records = []
    for chunk in range(0x800, len(movie), 0x40000):
        if chunk + 0x20 > len(movie) or movie[chunk + 8:chunk + 12] != b"KLBS":
            raise ValueError(f"Missing KLBS chunk at {chunk:#x}")
        count, duplicate = struct.unpack_from("<II", movie, chunk + 0x10)
        if count != duplicate or count > (0x40000 - 0x20) // 0x30:
            raise ValueError(f"Invalid KLBS record count at {chunk:#x}")
        table_end = 0x20 + count * 0x30
        for index in range(count):
            header = chunk + 0x20 + index * 0x30
            kind = movie[header + 8:header + 16]
            offset, size = struct.unpack_from("<II", movie, header + 0x18)
            if size == 0:
                continue
            start, end = chunk + offset, chunk + offset + size
            if offset < table_end or end > min(chunk + 0x40000, len(movie)):
                raise ValueError(f"KLBS record {index} outside chunk at {chunk:#x}")
            records.append(MovieRecord(start, end, kind))
    records.sort(key=lambda record: record.start)
    if any(left.end > right.start for left, right in zip(records, records[1:])):
        raise ValueError("Overlapping KLBS records")
    return records


def changed_outside_dialogue(source: bytes, patched: bytes,
                             records: list[MovieRecord]) -> list[tuple[int, int, int, bytes]]:
    if len(source) != len(patched):
        raise ValueError("Movie size changed")
    starts = [record.start for record in records]
    outside = []
    for offset, (old, new) in enumerate(zip(source, patched)):
        if old == new:
            continue
        index = bisect_right(starts, offset) - 1
        kind = (records[index].kind if index >= 0 and offset < records[index].end
                else b"OUTSIDE")
        if kind != b"SCTEGOLD":
            outside.append((offset, old, new, kind))
    return outside


def validate_dialogue_slot(movie: bytes, records: list[MovieRecord],
                           offset: int, original: bytes, replacement: bytes) -> None:
    if len(original) != len(replacement) or movie[offset:offset + len(original)] != original:
        raise ValueError("Movie subtitle source or length differs")
    starts = [record.start for record in records]
    for relative, (old, new) in enumerate(zip(original, replacement)):
        if old == new:
            continue
        position = offset + relative
        index = bisect_right(starts, position) - 1
        if index < 0 or position >= records[index].end or records[index].kind != b"SCTEGOLD":
            raise ValueError(f"Subtitle edit touches non-dialogue movie data at {position:#x}")
