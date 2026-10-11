#!/usr/bin/env python3
"""Lossless one-byte Turkish glyph mapping for the English PS1 font records.

No game font data is bundled here. Reserved ASCII slots are repacked as a pool
because several Turkish capitals are wider than any single unused slot.
"""

from __future__ import annotations

from functools import lru_cache
import struct

from policenauts_disc_tools import write_user_bytes_to_raw
from policenauts_font_tools import locate_fonts

BASE_GLYPH = {
    "ı": "i", "ç": "c", "ü": "u", "ş": "s", "Ş": "S", "ö": "o", "İ": "I",
    "ğ": "g", "Ğ": "G", "Ç": "C", "Ö": "O", "Ü": "U", "â": "a", "î": "i", "û": "u",
}
TURKISH_CARRIERS = {
    "ı": "{", "ç": "^", "ü": "<", "ş": "\\", "Ş": "=", "ö": ">", "İ": "}",
    "ğ": "$", "Ğ": "@", "Ç": "[", "Ö": "]", "Ü": "_", "â": "`", "î": "~", "û": "&",
}
# DEL has a font-data slot but is never emitted as a visible character.
# Reserving its pixels keeps the ordinary Q glyph intact for source text.
SPARE_SLOTS = "|\x7f"
RESERVED = frozenset(TURKISH_CARRIERS.values()) | frozenset(SPARE_SLOTS)
assert len(TURKISH_CARRIERS) == len(set(TURKISH_CARRIERS.values())) == len(BASE_GLYPH)


def encode_text(text: str) -> bytes:
    """Encode Turkish without approximation; reject ambiguous literal carriers."""
    out = bytearray()
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char in TURKISH_CARRIERS:
            out.append(ord(TURKISH_CARRIERS[char]))
        elif char in RESERVED:
            raise ValueError(f"Literal {char!r} conflicts with the Turkish font mapping")
        elif ord(char) < 128:
            out.append(ord(char))
        else:
            raise ValueError(f"Unmapped Unicode character: {char!r} (U+{ord(char):04X})")
    return bytes(out)


def descriptor(data: bytes, code: int, table_size: int) -> tuple[int, int]:
    slot = code - 0x20
    if not 0 <= slot < (table_size - 8) // 4:
        raise ValueError(f"Character code {code:#x} is not in the variable font table")
    value = struct.unpack_from(">I", data, 8 + slot * 4)[0]
    return value >> 24, value & 0xFFFFFF


def _pixels(raw: bytes, width: int) -> list[int]:
    if len(raw) != 3 * width:
        raise ValueError("Glyph byte count is not width * 3")
    return [byte >> shift & 3 for byte in raw for shift in (6, 4, 2, 0)]


def _pack(pixels: list[int], width: int) -> bytes:
    if len(pixels) != 12 * width:
        raise ValueError("A variable glyph must have exactly 12 rows")
    return bytes(
        sum(pixels[index + bit] << (6 - 2 * bit) for bit in range(4))
        for index in range(0, len(pixels), 4)
    )


def render_accent(raw: bytes, width: int, character: str) -> bytes:
    if character not in BASE_GLYPH:
        raise ValueError(f"Unsupported Turkish glyph: {character!r}")
    pixels = _pixels(raw, width)

    def put(x: int, y: int, level: int) -> None:
        if 0 <= x < width and 0 <= y < 12:
            pos = y * width + x
            pixels[pos] = max(pixels[pos], level)

    def move_body(top: int, bottom: int) -> None:
        nonlocal pixels
        occupied = [y for y in range(12) if any(pixels[y * width:(y + 1) * width])]
        if not occupied:
            raise ValueError("Native glyph is empty")
        old_top, old_bottom = occupied[0], occupied[-1]
        original = pixels
        pixels = [0] * (12 * width)
        for y in range(top, bottom + 1):
            source_y = old_top + round((y - top) * (old_bottom - old_top) / (bottom - top))
            pixels[y * width:(y + 1) * width] = original[source_y * width:(source_y + 1) * width]

    if character == "ı":
        if not any(pixels[:4 * width]) or not any(pixels[4 * width:]):
            raise ValueError("Native i has no expected dot and stem")
        pixels[:4 * width] = [0] * (4 * width)
    elif character == "İ":
        move_body(3, 11)
        put(width // 2, 1, 3)
        put(width // 2, 0, 1)
    elif character in "çşŞÇ":
        if character in "ŞÇ" and any(pixels[10 * width:]):
            move_body(0, 9)
        center = width // 2
        put(center, 10, 2)
        put(center - 1, 11, 3)
        put(center, 11, 2)
    elif character in "öüÖÜ":
        if character in "ÖÜ":
            move_body(3, 11)
        left, right = max(1, width // 3), min(width - 2, width - 1 - width // 3)
        put(left, 1, 3)
        put(right, 1, 3)
        put(left, 2, 1)
        put(right, 2, 1)
    elif character in "ğĞ":
        if character == "Ğ":
            move_body(3, 11)
        center = width // 2
        put(center - 1, 1, 2)
        put(center + 1, 1, 2)
        put(center, 2, 3)
    elif character in "âîû":
        if character == "î":
            pixels[:4 * width] = [0] * (4 * width)
        center = width // 2
        put(center, 1, 3)
        put(center - 1, 2, 2)
        put(center + 1, 2, 2)
    else:
        raise AssertionError(character)
    return _pack(pixels, width)


def _runs(data: bytes, table_size: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    previous_code = -1
    for char in sorted(RESERVED):
        code = ord(char)
        width, offset = descriptor(data, code, table_size)
        if runs and code == previous_code + 1 and runs[-1][0] + runs[-1][1] == offset:
            start, capacity = runs[-1]
            runs[-1] = (start, capacity + 3 * width)
        else:
            runs.append((offset, 3 * width))
        previous_code = code
    return runs


def _allocate(widths: dict[str, int], runs: list[tuple[int, int]]) -> dict[str, int]:
    items = sorted(widths.items(), key=lambda item: (-item[1], item[0]))
    capacities = tuple(length // 3 for _start, length in runs)

    @lru_cache(None)
    def solve(index: int, remaining: tuple[int, ...]) -> tuple[int, ...] | None:
        if index == len(items):
            return ()
        width = items[index][1]
        for run_index, capacity in enumerate(remaining):
            if capacity < width:
                continue
            next_remaining = list(remaining)
            next_remaining[run_index] -= width
            tail = solve(index + 1, tuple(next_remaining))
            if tail is not None:
                return (run_index,) + tail
        return None

    placement = solve(0, capacities)
    if placement is None:
        raise ValueError("Turkish glyphs do not fit the reserved font spans")
    cursors = [start for start, _length in runs]
    offsets = {}
    for (character, width), run_index in zip(items, placement):
        offsets[character] = cursors[run_index]
        cursors[run_index] += 3 * width
        start, length = runs[run_index]
        if cursors[run_index] > start + length:
            raise AssertionError("Font allocator overran its reserved span")
    return offsets


def rebuilt_record(data: bytes) -> tuple[bytes, dict[str, int]]:
    table_size, first_size = struct.unpack_from(">II", data)
    if table_size not in (392, 640) or table_size >= first_size:
        raise ValueError("Unexpected English variable font header")
    slot_count = (table_size - 8) // 4
    last_width, last_offset = descriptor(data, 0x20 + slot_count - 1, table_size)
    if table_size + last_offset + 3 * last_width != first_size:
        raise ValueError("Variable font pool does not end at the declared boundary")
    runs = _runs(data, table_size)
    widths = {}
    glyphs = {}
    for character, native in BASE_GLYPH.items():
        width, offset = descriptor(data, ord(native), table_size)
        raw = data[table_size + offset:table_size + offset + 3 * width]
        widths[character] = width
        glyphs[character] = render_accent(raw, width, character)
    offsets = _allocate(widths, runs)
    output = bytearray(data)
    for character, carrier in TURKISH_CARRIERS.items():
        width = widths[character]
        offset = offsets[character]
        glyph = glyphs[character]
        if not any(start <= offset and offset + len(glyph) <= start + length for start, length in runs):
            raise AssertionError("Turkish glyph escaped reserved storage")
        output[table_size + offset:table_size + offset + len(glyph)] = glyph
        struct.pack_into(">I", output, 8 + (ord(carrier) - 0x20) * 4, (width << 24) | offset)
    for character, carrier in TURKISH_CARRIERS.items():
        width, offset = descriptor(output, ord(carrier), table_size)
        if output[table_size + offset:table_size + offset + 3 * width] != glyphs[character]:
            raise AssertionError(f"Font round-trip failed for {character!r}")
    return bytes(output), widths


def patch_fonts(target) -> tuple[set[int], list[dict]]:
    """Patch an already-copied BIN and return changed sectors plus DPK records."""
    patches = []
    reports = []
    for container, record, original, _start, _count, base in locate_fonts(target):
        updated, widths = rebuilt_record(original)
        if len(original) != len(updated):
            raise AssertionError("Font record size changed")
        report = {"container": container, "container_entry_index": record["index"],
                  "name": record["name"], "glyph_count": len(widths)}
        reports.append(report)
        record_user_offset = base + record["data_offset"]
        position = 0
        while position < len(original):
            if original[position] == updated[position]:
                position += 1
                continue
            start = position
            while position < len(original) and original[position] != updated[position]:
                position += 1
            patches.append((record_user_offset + start, updated[start:position]))
    sectors: set[int] = set()
    for offset, blob in patches:
        sectors.update(write_user_bytes_to_raw(target, offset, blob))
    return sectors, reports
