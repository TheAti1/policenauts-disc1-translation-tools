#!/usr/bin/env python3
"""Text fitting helpers for Policenauts translation builds.

This module contains only format/encoding helpers. It does not contain game
script dumps, translated script dumps, disc images, or copyrighted assets.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable

SECTOR_USER = 2048
SECTOR_RAW = 2352

GAME_MARKER_RE = re.compile(
    r"(##N|#W|#N|#T\{|#\{|#[A-Za-z0-9_+\-%{}\[\]]+|\n)"
)
SPACE_RE = re.compile(r"\s+")

TURKISH_ASCII_MAP = str.maketrans(
    {
        "ç": "c",
        "Ç": "C",
        "ğ": "g",
        "Ğ": "G",
        "ı": "i",
        "İ": "I",
        "ö": "o",
        "Ö": "O",
        "ş": "s",
        "Ş": "S",
        "ü": "u",
        "Ü": "U",
        "â": "a",
        "Â": "A",
        "î": "i",
        "Î": "I",
        "û": "u",
        "Û": "U",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        "\u00a0": " ",
    }
)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def ascii_safe(text: str) -> str:
    """Return text that is safe for the current ASCII-only font setup."""
    text = normalize_newlines(text).translate(TURKISH_ASCII_MAP)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", errors="ignore").decode("ascii")


def flat(text: str) -> str:
    return SPACE_RE.sub(" ", normalize_newlines(text)).strip()


def game_display_text(command_text: str) -> str:
    """Strip in-band game text markers for translator-facing display."""
    text = command_text.replace("##N", "\n")
    text = text.replace("#W", "\n")
    text = text.replace("#N", "\n")
    text = text.replace("#T{", "")
    text = text.replace("#{", "")
    text = re.sub(r"#[A-Za-z0-9_+\-%{}\[\]]+", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def marker_layout(text: str, original_display: str, markers: list[str]) -> str:
    """Reflow a translation around the same number of line/control markers."""
    value = flat(text)
    if not markers:
        return value

    words = value.split()
    segments = len(markers) + 1
    original_parts = original_display.split("\n")
    weights = [max(1, len(part)) for part in original_parts]
    if len(weights) < segments:
        weights.extend([weights[-1] if weights else 20] * (segments - len(weights)))
    weights = weights[:segments]

    total_weight = sum(weights)
    total_chars = sum(len(word) for word in words) + max(0, len(words) - 1)
    targets = [max(1, round(total_chars * weight / total_weight)) for weight in weights]

    built: list[str] = []
    word_index = 0
    for segment_index in range(segments):
        if segment_index == segments - 1:
            built.append(" ".join(words[word_index:]))
            break
        remaining_segments = segments - segment_index - 1
        current: list[str] = []
        while word_index < len(words):
            remaining_words = len(words) - word_index
            if remaining_words <= remaining_segments:
                break
            candidate = " ".join(current + [words[word_index]])
            if current and len(candidate) > targets[segment_index]:
                break
            current.append(words[word_index])
            word_index += 1
        built.append(" ".join(current))

    while len(built) < segments:
        built.append("")

    out = built[0]
    for marker, part in zip(markers, built[1:]):
        out += marker + part
    return out


def fit_layout(
    translation: str,
    original_display: str,
    markers: list[str],
    capacity: int,
    encoder: Callable[[str], bytes],
) -> tuple[str, bool]:
    """Fit text into a fixed byte capacity, truncating from the end if needed."""
    clean = ascii_safe(translation)
    candidate = marker_layout(clean, original_display, markers)
    if len(encoder(candidate)) <= capacity:
        return candidate, False

    flat_translation = flat(clean)
    low, high = 0, len(flat_translation)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        prefix = flat_translation[:middle].rstrip()
        laid_out = marker_layout(prefix, original_display, markers)
        if len(encoder(laid_out)) <= capacity:
            best = laid_out
            low = middle + 1
        else:
            high = middle - 1
    return best, True


def game_tokens(chunk: bytes) -> list[tuple[str, bytes]]:
    """Tokenize fixed-size game text while preserving embedded control opcodes."""
    tokens: list[tuple[str, bytes]] = []
    pos = 0
    while pos < len(chunk):
        if chunk[pos] == 0x80 and pos + 3 < len(chunk) and chunk[pos + 1] == 0x6F:
            tokens.append(("op", chunk[pos : pos + 4]))
            pos += 4
        elif (
            chunk[pos] == 0x30
            and pos + 4 < len(chunk)
            and chunk[pos + 1] == 0x6F
            and chunk[pos + 4] == 0xFA
        ):
            tokens.append(("op", chunk[pos : pos + 5]))
            pos += 5
        elif chunk[pos] == 0x6F and pos + 2 < len(chunk):
            tokens.append(("op", chunk[pos : pos + 3]))
            pos += 3
        elif chunk[pos] == 0x30 and pos + 1 < len(chunk) and chunk[pos + 1] == 0xFA:
            tokens.append(("op", chunk[pos : pos + 2]))
            pos += 2
        elif chunk[pos] == 0x6E:
            if pos + 2 < len(chunk) and chunk[pos + 1] == 0 and chunk[pos + 2] == 0x6E:
                tokens.append(("op", chunk[pos : pos + 3]))
                pos += 3
            else:
                tokens.append(("op", chunk[pos : pos + 1]))
                pos += 1
        elif chunk[pos] == 0x7F and pos + 1 < len(chunk) and chunk[pos + 1] == 0x65:
            tokens.append(("op", chunk[pos : pos + 2]))
            pos += 2
        elif chunk[pos] == 0x80 and pos + 1 < len(chunk):
            tokens.append(("char2", chunk[pos : pos + 2]))
            pos += 2
        else:
            tokens.append(("char1", chunk[pos : pos + 1]))
            pos += 1
    return tokens


def build_game_chunk(chunk: bytes, text: str) -> tuple[bytes, int]:
    """Encode display text back into the negated-byte game text slots."""
    tokens = game_tokens(chunk)
    slots = sum(kind != "op" for kind, _blob in tokens)
    encoded = text.encode("ascii")
    if len(encoded) > slots:
        raise RuntimeError("Fitted GAME text still exceeds its character slots")

    out = bytearray()
    source_index = 0
    for kind, blob in tokens:
        if kind == "op":
            out.extend(blob)
            continue
        value = encoded[source_index] if source_index < len(encoded) else 0x20
        if source_index < len(encoded):
            source_index += 1
        negated = (-value) & 0xFF
        if kind == "char2":
            out.extend((0x80, negated))
        else:
            out.append(negated)
    return bytes(out), slots
