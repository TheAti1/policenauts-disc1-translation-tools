#!/usr/bin/env python3
"""Encode one fixed MOV subtitle slot without changing timing or stream layout."""

from __future__ import annotations

from build_policenauts_tr_bin import movie_prefix
from policenauts_movie_integrity import MovieRecord, validate_dialogue_slot
from policenauts_turkish_font import encode_text


def encode_movie_slot(entry: dict, translation: str) -> bytes:
    old = bytes.fromhex(entry["old_bytes_hex"])
    if b"\0" in old or "\0" in translation or "\r" in translation:
        raise ValueError("Movie subtitle contains an unsupported control")
    if translation == entry["text_with_breaks"]:
        return old + b"\0"
    encoded = movie_prefix(entry) + encode_text(translation).replace(b"\n", b"\x80|")
    if len(encoded) > len(old):
        raise ValueError(f"Movie subtitle {entry['index']} exceeds its fixed slot")
    return encoded + b"\0" + b"\0" * (len(old) - len(encoded))


def encode_movie_slot_in_record(entry: dict, translation: str, movie: bytes,
                                records: list[MovieRecord], local_offset: int) -> bytes:
    """Keep all MDEC/audio/padding bytes outside the owning DLOG record."""
    original = bytes.fromhex(entry["old_bytes_hex"]) + b"\0"
    if movie[local_offset:local_offset + len(original)] != original:
        raise ValueError(f"Movie subtitle source differs at {entry['index']}")
    if translation == entry["text_with_breaks"]:
        return original
    end = local_offset + len(original)
    owners = [record for record in records if record.kind == b"SCTEGOLD"
              and record.start < end and record.end > local_offset]
    if len(owners) != 1:
        raise ValueError(f"Movie subtitle {entry['index']} has no unique DLOG record")
    owner = owners[0]
    prefix_len = max(0, owner.start - local_offset)
    if prefix_len:
        prefix = original[:prefix_len]
        body = encode_text(translation).replace(b"\n", b"\x80|")
        # A prefix can contain a binary guard followed by visible punctuation.
        # Do not duplicate the punctuation when the edit repeats it.
        overlap = next((count for count in range(min(len(prefix), len(body)), 0, -1)
                        if body.startswith(prefix[-count:])), 0)
        body = body[overlap:]
        encoded = prefix + body
        if len(encoded) > len(original) - 1:
            raise ValueError(f"Movie subtitle {entry['index']} exceeds its fixed slot")
        candidate = encoded + b"\0" + b"\0" * (len(original) - len(encoded) - 1)
    else:
        candidate = encode_movie_slot(entry, translation)

    # A catalog match can run past the Name List's declared DLOG size. Only
    # discard zero padding after the new terminator, never translated text.
    suffix_at = min(end, owner.end) - local_offset
    if any(candidate[i] not in (0, original[i])
           for i in range(suffix_at, len(original))):
        raise ValueError(f"Movie subtitle {entry['index']} exceeds its DLOG record")
    candidate = candidate[:suffix_at] + original[suffix_at:]
    validate_dialogue_slot(movie, records, local_offset, original, candidate)
    return candidate
