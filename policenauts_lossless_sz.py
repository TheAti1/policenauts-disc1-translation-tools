#!/usr/bin/env python3
"""Turkish text encoder and guarded relative-reference rebasing for .SZ banks.

Recognized 0x6F references are relocated, but unknown script structures and
screen layout still prevent a runtime-safety claim.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import struct

from policenauts_text_format import GAME_MARKER_RE, game_tokens, marker_layout, normalize_newlines
from policenauts_turkish_font import TURKISH_CARRIERS, encode_text


_DECODE_CARRIERS = {carrier: character for character, carrier in TURKISH_CARRIERS.items()}


def structural_tokens(chunk: bytes) -> list[tuple[str, bytes]]:
    # 0xF3 is an invisible CR/end-of-text control, not a printable glyph slot.
    return [("op", blob) if blob == b"\xf3" else (kind, blob)
            for kind, blob in game_tokens(chunk)]


def command_text_for_translation(entry: dict, translation: str) -> str:
    clean = normalize_newlines(translation)
    if "\0" in clean or "\r" in clean:
        raise ValueError("GAME translation contains a forbidden control character")
    markers = GAME_MARKER_RE.findall(entry["raw_command_text"])
    if not markers:
        # No source line-break command exists in this span. A literal LF is
        # not an SZ line-break opcode, so keep both lines as one text run.
        return clean.replace("\n", " ")
    if clean.count("\n") == len(markers):
        parts = clean.split("\n")
        laid_out = parts[0] + "".join(marker + part for marker, part in zip(markers, parts[1:]))
    else:
        laid_out = marker_layout(clean, entry["text_with_breaks"], markers)
        if clean.endswith(" "):
            laid_out += " "
    # The English script uses sacrificial spaces at some page boundaries.
    # The renderer consumes them; removing one drops the first visible glyph.
    source_parts = GAME_MARKER_RE.split(entry["raw_command_text"])
    translated_parts = GAME_MARKER_RE.split(laid_out)
    if len(source_parts) != len(translated_parts):
        raise ValueError("GAME marker sequence changed during layout")
    for index in range(0, len(source_parts), 2):
        source_part = source_parts[index]
        translated_part = translated_parts[index]
        leading = len(source_part) - len(source_part.lstrip(" "))
        trailing = len(source_part) - len(source_part.rstrip(" "))
        translated_part = " " * max(0, leading - (len(translated_part) - len(translated_part.lstrip(" ")))) + translated_part
        translated_part += " " * max(0, trailing - (len(translated_part) - len(translated_part.rstrip(" "))))
        translated_parts[index] = translated_part
    return "".join(translated_parts)


def encode_game_chunk(entry: dict, translation: str, *,
                      preserve_byte_intervals: bool = False) -> tuple[bytes, str]:
    old = bytes.fromhex(entry["old_bytes_hex"])
    if translation == entry["text_with_breaks"]:
        return old, entry["raw_command_text"]
    tokens = structural_tokens(old)
    command = command_text_for_translation(entry, translation)
    encoded = encode_text(command)
    groups: list[list[tuple[str, bytes]]] = [[]]
    controls = []
    for kind, blob in tokens:
        if kind == "op":
            controls.append(blob)
            groups.append([])
        else:
            groups[-1].append((kind, blob))
    total_tokens = sum(len(group) for group in groups)
    total_bytes = sum(len(blob) for group in groups for _kind, blob in group)
    if not total_tokens:
        raise ValueError("GAME source span has no text glyphs")
    # When the new text fills the source byte capacity exactly, keep every
    # intervening control at its original byte offset, including 0x80 glyphs.
    exact_intervals = preserve_byte_intervals and len(encoded) == total_bytes
    boundaries = [0]
    used = 0
    for group in groups[:-1]:
        used += (sum(len(blob) for _kind, blob in group)
                 if exact_intervals else len(group))
        boundaries.append(used if exact_intervals
                          else round(len(encoded) * used / total_tokens))
    boundaries.append(len(encoded))
    result = bytearray()
    for index, group in enumerate(groups):
        for char in encoded[boundaries[index]:boundaries[index + 1]]:
            result.append((-char) & 0xFF)
        if index < len(controls):
            result.extend(controls[index])
    rebuilt = bytes(result)
    if [blob for kind, blob in structural_tokens(rebuilt) if kind == "op"] != controls:
        raise AssertionError("GAME control sequence changed during text encoding")
    return rebuilt, command


def decoded_game_command(chunk: bytes, *, decode_carriers: bool = False) -> str:
    out = []
    for kind, blob in structural_tokens(chunk):
        if kind == "char1":
            value = chr((-blob[0]) & 0xFF)
            out.append(_DECODE_CARRIERS.get(value, value) if decode_carriers else value)
        elif kind == "char2":
            value = chr((-blob[1]) & 0xFF)
            out.append(_DECODE_CARRIERS.get(value, value) if decode_carriers else value)
    return "".join(out)


def audit_boundary_e5(original: bytes,
                      replacements: list[tuple[int, bytes, bytes]],
                      code_size: int) -> dict:
    """Report, but do not modify, a boundary E5 operand affected by text shifts.

    This is a conservative heuristic, not a decoded opcode. A controlled
    GAME01 test still crashes after changing this operand, so treating its
    apparent target as a validated pointer would be unsafe.
    """
    boundary = 8 + code_size
    at = boundary - 8
    if at < 8 or original[at] != 0xE5 or at + 5 > boundary:
        return {"present": False, "needs_structural_review": False}
    operand = struct.unpack_from(">H", original, at + 1)[0]
    target = at + 2 + operand
    in_text = boundary <= target < len(original) - 2
    prior_shift = 0
    inside_changed_span = False
    if in_text:
        for start, old, new in replacements:
            delta = len(new) - len(old)
            if start + len(old) <= target:
                prior_shift += delta
            elif start <= target < start + len(old) and delta:
                inside_changed_span = True
    return {
        "present": True,
        "offset": at,
        "operand_u16": operand,
        "apparent_target": target,
        "apparent_target_in_text": in_text,
        "shift_before_target": prior_shift,
        "target_inside_changed_span": inside_changed_span,
        "needs_structural_review": in_text and (prior_shift != 0 or inside_changed_span),
    }


def relocate_sz_pointers(original: bytes, rebuilt: bytearray,
                         replacements: list[tuple[int, bytes, bytes]],
                         code_size: int, mode: str) -> dict[str, int]:
    """Rebase verified relative 0x6F targets after text records change size."""
    if mode not in {"strict", "catalog"}:
        raise ValueError(f"Unknown SZ pointer mode: {mode}")
    regions = []
    landmarks = {}
    shift = 0
    for start, old, new in replacements:
        end = start + len(old)
        new_start = start + shift
        old_ops = []
        new_ops = []
        offset = 0
        for kind, blob in structural_tokens(old):
            if kind == "op":
                old_ops.append((offset, blob))
            offset += len(blob)
        offset = 0
        for kind, blob in structural_tokens(new):
            if kind == "op":
                new_ops.append((offset, blob))
            offset += len(blob)
        if [blob for _, blob in old_ops] != [blob for _, blob in new_ops]:
            raise ValueError("SZ source and rebuilt control sequences differ")
        landmarks[start] = new_start
        for (old_offset, old_blob), (new_offset, new_blob) in zip(old_ops, new_ops):
            landmarks[start + old_offset] = new_start + new_offset
            landmarks[start + old_offset + len(old_blob)] = (
                new_start + new_offset + len(new_blob))
        landmarks[end] = new_start + len(new)
        regions.append((start, end, shift, len(new) - len(old)))
        shift += len(new) - len(old)

    starts = [region[0] for region in regions]

    def mapped(position: int) -> int | None:
        if position >= len(original) - 2:
            return len(rebuilt) - 2 + position - (len(original) - 2)
        index = bisect_right(starts, position) - 1
        if index < 0:
            return position
        start, end, prior_shift, delta = regions[index]
        if position < end:
            return landmarks.get(position)
        return position + prior_shift + delta

    first_text = 8 + code_size
    scan_start = min(first_text, starts[0]) if starts else first_text
    entry_starts = set(starts)
    candidates = []
    # The code stream may contain a small number of direct text references.
    # Require an exact catalogued entry target to avoid treating code data as an opcode.
    for position in range(8, scan_start):
        if original[position] != 0x6F or position + 2 >= len(original):
            continue
        target = position + 2 + struct.unpack_from(">h", original, position + 1)[0]
        if target in entry_starts:
            candidates.append((position, target, True))

    position = scan_start
    for kind, blob in game_tokens(original[scan_start:-2]):
        if kind == "op" and len(blob) == 3 and blob[0] == 0x6F:
            target = position + 2 + struct.unpack(">h", blob[1:])[0]
            if (target in entry_starts if mode == "catalog" else
                    scan_start <= target < len(original) - 2):
                candidates.append((position, target, False))
        position += len(blob)

    checked = relocated = code_checked = 0
    seen = set()
    for old_position, old_target, in_code in candidates:
        new_position = mapped(old_position)
        new_target = mapped(old_target)
        if new_position is None or new_target is None:
            raise ValueError(f"Unmapped SZ pointer at {old_position} -> {old_target}")
        if new_position in seen:
            raise ValueError(f"Duplicate SZ pointer at {new_position}")
        seen.add(new_position)
        if bytes(rebuilt[new_position:new_position + 3]) != original[old_position:old_position + 3]:
            raise ValueError(f"SZ pointer opcode moved unexpectedly at {old_position}")
        new_delta = new_target - (new_position + 2)
        if not -0x8000 <= new_delta <= 0x7FFF:
            raise ValueError(f"SZ pointer exceeds signed 16-bit range at {old_position}")
        if new_delta != struct.unpack_from(">h", original, old_position + 1)[0]:
            struct.pack_into(">h", rebuilt, new_position + 1, new_delta)
            relocated += 1
        if new_position + 2 + struct.unpack_from(">h", rebuilt, new_position + 1)[0] != new_target:
            raise AssertionError("Rebased SZ pointer does not reach its mapped target")
        checked += 1
        code_checked += in_code
    return {"pointers_checked": checked, "pointers_relocated": relocated,
            "code_pointers_checked": code_checked}


def rebuild_sz_bank(original: bytes, items: list[tuple[dict, str]],
                    gap_after_entry: int = 0,
                    pointer_mode: str = "none") -> tuple[bytes, dict]:
    if len(original) < 10:
        raise ValueError("SZ bank too short")
    code_size, text_size = struct.unpack_from(">HH", original)
    if code_size + text_size + 8 != len(original):
        raise ValueError("SZ two-length header disagrees with bank size")
    ordered = sorted(items, key=lambda pair: int(pair[0]["local_offset"]))
    cursor = 0
    body = bytearray()
    counts = Counter()
    replacements = []
    for entry, translation in ordered:
        start = int(entry["local_offset"])
        old = bytes.fromhex(entry["old_bytes_hex"])
        end = start + len(old)
        # The English patch sometimes starts a text command with 0x6f 00 01
        # three bytes before the nominal text-section boundary.
        if start < 5 + code_size or start < cursor or end > len(original) - 2:
            raise ValueError("GAME string is outside text section or overlaps another string")
        if original[start:end] != old:
            raise ValueError(f"SZ source bytes differ at {entry['index']}")
        if decoded_game_command(old) != entry["raw_command_text"]:
            raise ValueError(f"SZ source command differs at {entry['index']}")
        replacement, command = encode_game_chunk(entry, translation)
        translated = translation != entry["text_with_breaks"]
        if decoded_game_command(replacement, decode_carriers=translated) != command:
            raise AssertionError("GAME command did not round-trip")
        body.extend(original[cursor:start])
        body.extend(replacement)
        replacements.append((start, old, replacement))
        cursor = end
        counts["entries"] += 1
        counts["original_text_bytes"] += len(old)
        counts["translated_text_bytes"] += len(replacement)
    body.extend(original[cursor:-2])
    footer = original[-2:]
    candidate_size = len(body) + len(footer)
    growth = candidate_size - len(original)
    if growth <= 0:
        body.extend(b"\0" * -growth)
        growth = 0
    else:
        if growth > gap_after_entry:
            raise ValueError(f"SZ bank needs {growth} bytes but only {gap_after_entry} follow it")
        if text_size + growth > 0xFFFF:
            raise ValueError("SZ text length exceeds 16-bit header")
        struct.pack_into(">H", body, 2, text_size + growth)
    rebuilt = bytearray(body + footer)
    if pointer_mode != "none":
        counts.update(relocate_sz_pointers(original, rebuilt, replacements,
                                           code_size, pointer_mode))
    if int.from_bytes(rebuilt[:2], "big") + int.from_bytes(rebuilt[2:4], "big") + 8 != len(rebuilt):
        raise AssertionError("Rebuilt SZ header mismatch")
    counts["growth_bytes"] = growth
    counts["new_bank_size"] = len(rebuilt)
    result = dict(counts)
    result["boundary_e5_audit"] = audit_boundary_e5(original, replacements, code_size)
    return bytes(rebuilt), result
