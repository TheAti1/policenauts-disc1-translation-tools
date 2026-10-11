#!/usr/bin/env python3
"""Replace BIN.DPK overlay strings without moving any nonzero bytes."""

from __future__ import annotations

import re
import struct

from policenauts_turkish_font import encode_text


PLACEHOLDER = re.compile(r"%[-+0-9.]*[A-Za-z]")


def _relocate(result: bytearray, original: bytes, start: int, old: bytes,
              encoded: bytes, plan: dict) -> None:
    target = int(plan["target_offset"])
    base = int(plan["va_base"], 0) if isinstance(plan["va_base"], str) else int(plan["va_base"])
    pointer_offsets = [int(value) for value in plan["pointer_offsets"]]
    addiu_offsets = [int(value) for value in plan["addiu_offsets"]]
    if not pointer_offsets or not addiu_offsets or len(set(pointer_offsets + addiu_offsets)) != len(pointer_offsets + addiu_offsets):
        raise ValueError("Relocation must name distinct pointer and addiu sites")
    if (target <= 0 or target + len(encoded) + 1 > len(original)
            or target < start + len(old) + 1 and start < target + len(encoded) + 1):
        raise ValueError("Relocation destination is invalid or overlaps its source")
    if (original[target - 1] != 0 or any(original[target:target + len(encoded) + 1])
            or result[target - 1] != 0 or any(result[target:target + len(encoded) + 1])):
        raise ValueError("Relocation destination is not a NUL-bounded zero hole")
    old_va, new_va = base + start, base + target
    if not (0 <= old_va <= 0xFFFFFFFF and 0 <= new_va <= 0xFFFFFFFF):
        raise ValueError("Relocation VA exceeds 32 bits")
    old_low = struct.pack("<H", old_va & 0xFFFF)
    observed_lows = {offset for offset in range(len(original) - 1)
                     if original[offset:offset + 2] == old_low}
    if observed_lows != set(pointer_offsets + addiu_offsets):
        raise ValueError("Relocation has unlisted or missing low-address references")
    old_pointer = struct.pack("<I", old_va)
    observed_pointers = {offset for offset in range(len(original) - 3)
                         if original[offset:offset + 4] == old_pointer}
    if observed_pointers != set(pointer_offsets):
        raise ValueError("Relocation has unlisted or missing full pointers")
    old_hi = ((old_va + 0x8000) >> 16) & 0xFFFF
    new_hi = ((new_va + 0x8000) >> 16) & 0xFFFF
    if old_hi != new_hi:
        raise ValueError("Relocation requires changing LUI high address")
    for offset in addiu_offsets:
        if offset < 4 or offset + 4 > len(original) or offset % 4:
            raise ValueError("Unaligned MIPS addiu relocation site")
        lui = struct.unpack_from("<I", original, offset - 4)[0]
        addiu = struct.unpack_from("<I", original, offset)[0]
        register = (addiu >> 16) & 31
        if ((lui >> 26) != 15 or (lui >> 16) & 31 != register
                or lui & 0xFFFF != old_hi or (addiu >> 26) != 9
                or (addiu >> 21) & 31 != register
                or addiu & 0xFFFF != old_va & 0xFFFF):
            raise ValueError("MIPS LUI/addiu sequence does not match source VA")
    result[start:start + len(old) + 1] = b"\0" * (len(old) + 1)
    result[target:target + len(encoded) + 1] = encoded + b"\0"
    for offset in pointer_offsets:
        struct.pack_into("<I", result, offset, new_va)
    for offset in addiu_offsets:
        word = struct.unpack_from("<I", result, offset)[0]
        struct.pack_into("<I", result, offset, (word & 0xFFFF0000) | (new_va & 0xFFFF))


def rebuild_overlay(original: bytes, items: list[tuple[dict, str]],
                    relocations: dict[int, dict] | None = None) -> tuple[bytes, dict]:
    result = bytearray(original)
    changed = 0
    plans = relocations or {}
    used_plans = set()
    for entry, translation in sorted(items, key=lambda pair: int(pair[0]["local_offset"])):
        start = int(entry["local_offset"])
        old = bytes.fromhex(entry["old_bytes_hex"])
        end = start + len(old)
        if end >= len(original) or original[start:end + 1] != old + b"\0":
            raise ValueError(f"Overlay source span differs at {entry['index']}")
        if PLACEHOLDER.findall(entry["text_with_breaks"]) != PLACEHOLDER.findall(translation):
            raise ValueError(f"Overlay placeholders differ at {entry['index']}")
        if "\0" in translation:
            raise ValueError(f"Overlay translation contains NUL at {entry['index']}")
        if translation == entry["text_with_breaks"]:
            continue
        encoded = encode_text(translation)
        if start in plans:
            _relocate(result, original, start, old, encoded, plans[start])
            used_plans.add(start)
            changed += 1
            continue
        next_live = end
        while next_live < len(original) and original[next_live] == 0:
            next_live += 1
        if start + len(encoded) + 1 > next_live:
            raise ValueError(f"Overlay text would overwrite live bytes at {entry['index']}")
        # NUL the old tail so a shorter translation cannot leave visible text.
        result[start:max(end + 1, start + len(encoded) + 1)] = (
            encoded + b"\0" + b"\0" * max(0, end - start - len(encoded)))
        changed += 1
    if used_plans != set(plans):
        raise ValueError(f"Unused overlay relocation plans: {sorted(set(plans) - used_plans)}")
    return bytes(result), {"entries": len(items), "changed": changed,
                           "original_size": len(original)}
