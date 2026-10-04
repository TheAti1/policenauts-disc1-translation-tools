#!/usr/bin/env python3
"""Export, edit, and safely patch the PS1 Policenauts 12x12 font cells.

Game-derived fonts and atlases are private outputs; this tool ships no glyph data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from policenauts_disc_tools import (
    SECTOR_USER,
    compute_crc32_bzip2,
    dpk_records_from_image,
    fix_mode2_form1_edc_ecc_for_sectors,
    iso_nauts_files,
    read_user_bytes_from_raw,
    update_dpk_checksums_for_patched_entries,
    write_user_bytes_to_raw,
)

FORMAT = "policenauts-font-v1"
CELL = 12
CELL_BYTES = 36
COLUMNS = 64
TURKISH = "çÇğĞıİöÖşŞüÜâÂîÎûÛ"
SLOT_START = 1700
FONT_RECORDS = {
    "FONT.DPK": {
        "KANJIFNT.MDB": (28, 3444),
        "KANJIFNT.RB": (29, 1755),
        "KPRFONT.MDB": (29, 1755),
        "KPRFONT.RB": (29, 1755),
    },
    "SHOTPAC.DPK": {
        "KANJIFNT.MDB": (28, 3444),
        "KANJIFNT.RB": (28, 3444),
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_key(container: str, name: str) -> str:
    return f"{container.replace('.', '_')}__{name.replace('.', '_')}"


def locate_fonts(fp):
    files = iso_nauts_files(fp)
    for container, names in FONT_RECORDS.items():
        if container not in files:
            raise ValueError(f"Missing ISO file: {container}")
        sector, file_size = files[container]
        base = sector * SECTOR_USER
        records = dpk_records_from_image(fp, base)
        by_name = {record["name"]: record for record in records}
        for name, (prefix, count) in names.items():
            if name not in by_name:
                raise ValueError(f"Missing {container}/{name}")
            record = by_name[name]
            if record["data_offset"] + record["data_size"] > file_size:
                raise ValueError(f"Invalid bounds: {container}/{name}")
            data = read_user_bytes_from_raw(fp, base + record["data_offset"], record["data_size"])
            if compute_crc32_bzip2(data) != record["checksum"]:
                raise ValueError(f"Bad source DPK checksum: {container}/{name}")
            if len(data) < 8:
                raise ValueError(f"Short font: {container}/{name}")
            table_size, first_section_size = struct.unpack_from(">II", data)
            if table_size % 4 or table_size not in (392, 640):
                raise ValueError(f"Unexpected variable font table: {container}/{name}")
            start = 8 + first_section_size + prefix
            if start + count * CELL_BYTES > len(data):
                raise ValueError(f"Fixed glyph cells exceed {container}/{name}")
            yield container, record, data, start, count, base


def decode_cell(raw: bytes) -> Image.Image:
    if len(raw) != CELL_BYTES:
        raise ValueError("A font cell must be exactly 36 bytes")
    levels = bytearray((byte >> shift & 3) * 85 for byte in raw for shift in (6, 4, 2, 0))
    return Image.frombytes("L", (CELL, CELL), bytes(levels))


def encode_cell(image: Image.Image) -> bytes:
    if image.mode != "L" or image.size != (CELL, CELL):
        raise ValueError("A font cell must be a 12x12 grayscale image")
    pixels = list(image.getdata())
    if any(value not in (0, 85, 170, 255) for value in pixels):
        raise ValueError("Font pixels must use exactly 0, 85, 170, or 255")
    return bytes(
        sum((pixels[index + shift] // 85) << (6 - shift * 2) for shift in range(4))
        for index in range(0, CELL * CELL, 4)
    )


def atlas_size(count: int) -> tuple[int, int]:
    return COLUMNS * CELL, math.ceil(count / COLUMNS) * CELL


def cell_box(index: int) -> tuple[int, int, int, int]:
    x = (index % COLUMNS) * CELL
    y = (index // COLUMNS) * CELL
    return x, y, x + CELL, y + CELL


def font_atlas(data: bytes, start: int, count: int) -> Image.Image:
    image = Image.new("L", atlas_size(count), 0)
    for index in range(count):
        offset = start + index * CELL_BYTES
        image.paste(decode_cell(data[offset:offset + CELL_BYTES]), cell_box(index))
    return image


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract(source: Path, out_dir: Path) -> None:
    if out_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing font export: {out_dir}")
    out_dir.mkdir(parents=True)
    manifest = {"format": FORMAT, "cell_size": CELL, "columns": COLUMNS, "files": []}
    with source.open("rb") as fp:
        for container, record, data, start, count, _base in locate_fonts(fp):
            key = file_key(container, record["name"])
            (out_dir / f"{key}.raw").write_bytes(data)
            font_atlas(data, start, count).save(out_dir / f"{key}.png")
            manifest["files"].append({
                "container": container,
                "name": record["name"],
                "record_index": record["index"],
                "length": len(data),
                "sha256": sha256(data),
                "fixed_start": start,
                "count": count,
                "image": f"{key}.png",
                "raw": f"{key}.raw",
            })
        sector, _size = iso_nauts_files(fp)["FONT.DPK"]
        rubi = next(r for r in dpk_records_from_image(fp, sector * SECTOR_USER) if r["name"] == "RUBI.DAT")
        rubi_bytes = read_user_bytes_from_raw(fp, sector * SECTOR_USER + rubi["data_offset"], rubi["data_size"])
        (out_dir / "FONT_DPK__RUBI_DAT.raw").write_bytes(rubi_bytes)
    manifest["unparsed"] = ["FONT.DPK/RUBI.DAT: preserved as raw; glyph layout not verified"]
    write_json(out_dir / "manifest.json", manifest)
    print(f"Exported {len(manifest['files'])} editable font atlases to {out_dir}")


def render_turkish(char: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    image = Image.new("L", (CELL, CELL), 0)
    box = font.getbbox(char)
    width, height = box[2] - box[0], box[3] - box[1]
    if width > CELL or height > CELL:
        raise ValueError(f"Glyph does not fit the 12x12 cell: {char!r} ({width}x{height})")
    ImageDraw.Draw(image).text(
        ((CELL - width) // 2 - box[0], (CELL - height) // 2 - box[1]),
        char,
        font=font,
        fill=255,
    )
    return image.point(lambda value: min(3, (value + 42) // 85) * 85)


def prepare(export_dir: Path, out_dir: Path, font_path: Path, size: int, slot_start: int) -> None:
    if out_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing edited fonts: {out_dir}")
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise ValueError("Unsupported font manifest")
    if not font_path.is_file():
        raise FileNotFoundError(font_path)
    font = ImageFont.truetype(str(font_path), size)
    replacement = {slot_start + i: render_turkish(char, font) for i, char in enumerate(TURKISH)}
    out_dir.mkdir(parents=True)
    preview = Image.new("RGB", (len(TURKISH) * 58, len(manifest["files"]) * 66), (24, 28, 32))
    preview_draw = ImageDraw.Draw(preview)
    for row_index, item in enumerate(manifest["files"]):
        if max(replacement) >= item["count"]:
            raise ValueError(f"Selected slot is outside {item['container']}/{item['name']}")
        original = Image.open(export_dir / item["image"])
        original.load()
        if original.mode != "L" or original.size != atlas_size(item["count"]):
            raise ValueError(f"Original atlas has wrong size: {item['image']}")
        edited = original.copy()
        for slot, glyph in replacement.items():
            edited.paste(glyph, cell_box(slot))
            char = TURKISH[slot - slot_start]
            x = (slot - slot_start) * 58
            y = row_index * 66
            preview_draw.text((x + 2, y + 1), char, fill="white", font=font)
            for image, dy in ((original.crop(cell_box(slot)), 16), (glyph, 40)):
                preview.paste(image.resize((24, 24), Image.Resampling.NEAREST).convert("RGB"), (x + 2, y + dy))
        edited.save(out_dir / item["image"])
    preview.save(out_dir / "turkish_preview.png")
    write_json(out_dir / "mapping.json", {
        "format": FORMAT,
        "export_manifest": str((export_dir / "manifest.json").resolve()),
        "font_source": str(font_path.resolve()),
        "font_size": size,
        "slots": [{"character": char, "slot": slot_start + index} for index, char in enumerate(TURKISH)],
        "warning": "Font cells alone do not change game text encoding; no ISO was modified.",
    })
    print(f"Prepared {len(TURKISH)} Turkish glyphs in {len(manifest['files'])} editable atlases: {out_dir}")


def checked_edits(export_dir: Path, edit_dir: Path, manifest: dict, allowed: set[int]) -> list[tuple[dict, dict[int, bytes]]]:
    result = []
    for item in manifest["files"]:
        raw = (export_dir / item["raw"]).read_bytes()
        if len(raw) != item["length"] or sha256(raw) != item["sha256"]:
            raise ValueError(f"Raw export differs from manifest: {item['raw']}")
        original = Image.open(export_dir / item["image"])
        edited = Image.open(edit_dir / item["image"])
        original.load()
        edited.load()
        if original.mode != "L" or edited.mode != "L" or original.size != edited.size or original.size != atlas_size(item["count"]):
            raise ValueError(f"Atlas mode/size mismatch: {item['image']}")
        if original.tobytes() != font_atlas(raw, item["fixed_start"], item["count"]).tobytes():
            raise ValueError(f"Original atlas differs from raw font: {item['image']}")
        changes = {}
        for index in range(item["count"]):
            before = original.crop(cell_box(index))
            after = edited.crop(cell_box(index))
            if before.tobytes() != after.tobytes():
                if index not in allowed:
                    raise ValueError(f"Unapproved font cell changed: {item['image']} slot {index}")
                changes[index] = encode_cell(after)
        if not changes:
            raise ValueError(f"No edited glyphs found: {item['image']}")
        result.append((item, changes))
    return result


def patch_bin(source: Path, out_bin: Path, export_dir: Path, edit_dir: Path) -> None:
    if source.resolve() == out_bin.resolve() or out_bin.exists():
        raise FileExistsError("Output BIN must be a new path, never the source image")
    source_cue = source.with_suffix(".cue")
    out_cue = out_bin.with_suffix(".cue")
    if not source_cue.is_file():
        raise FileNotFoundError(f"Source CUE is missing: {source_cue}")
    if out_cue.exists():
        raise FileExistsError(f"Output CUE already exists: {out_cue}")
    cue_lines = source_cue.read_text(encoding="ascii").splitlines()
    if not cue_lines or not cue_lines[0].startswith("FILE "):
        raise ValueError(f"Invalid source CUE: {source_cue}")
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    mapping = json.loads((edit_dir / "mapping.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or mapping.get("format") != FORMAT:
        raise ValueError("Invalid font manifest or Turkish mapping")
    if Path(mapping["export_manifest"]).resolve() != (export_dir / "manifest.json").resolve():
        raise ValueError("Edited atlas was prepared from another export")
    allowed = {item["slot"] for item in mapping["slots"]}
    edits = checked_edits(export_dir, edit_dir, manifest, allowed)
    with source.open("rb") as fp:
        found = {(container, record["name"]): (record, data, start, count, base)
                 for container, record, data, start, count, base in locate_fonts(fp)}
    for item, _changes in edits:
        record, data, start, count, _base = found[(item["container"], item["name"])]
        if (record["index"] != item["record_index"] or len(data) != item["length"]
                or sha256(data) != item["sha256"] or start != item["fixed_start"]
                or count != item["count"]):
            raise ValueError(f"Source font differs from export: {item['container']}/{item['name']}")
    shutil.copyfile(source, out_bin)
    touched = set()
    patched = []
    with out_bin.open("r+b") as fp:
        for item, changes in edits:
            record, _data, start, _count, base = found[(item["container"], item["name"])]
            for slot, encoded in changes.items():
                user_offset = base + record["data_offset"] + start + slot * CELL_BYTES
                touched.update(write_user_bytes_to_raw(fp, user_offset, encoded))
                if read_user_bytes_from_raw(fp, user_offset, CELL_BYTES) != encoded:
                    raise RuntimeError(f"Font write verification failed: {item['name']} slot {slot}")
            patched.append({"container": item["container"], "container_entry_index": record["index"]})
        crc_count, crc_sectors = update_dpk_checksums_for_patched_entries(fp, patched)
        touched.update(crc_sectors)
        edc_count, ecc_count = fix_mode2_form1_edc_ecc_for_sectors(fp, touched)
    cue_lines[0] = f'FILE "{out_bin.name}" BINARY'
    out_cue.write_text("\n".join(cue_lines) + "\n", encoding="ascii")
    print(json.dumps({
        "out_bin": str(out_bin),
        "out_cue": str(out_cue),
        "font_records": len(edits),
        "glyph_cells": sum(len(changes) for _item, changes in edits),
        "changed_sectors": len(touched),
        "dpk_crc_updates": crc_count,
        "edc_fixes": edc_count,
        "ecc_fixes": ecc_count,
        "warning": "Text encoding is not yet mapped to the new glyph slots.",
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract_cmd = sub.add_parser("extract", help="Export raw fonts and editable 12x12 PNG atlases")
    extract_cmd.add_argument("--source-bin", type=Path, required=True)
    extract_cmd.add_argument("--out-dir", type=Path, required=True)
    prepare_cmd = sub.add_parser("prepare", help="Draw Turkish glyphs over chosen Japanese cells")
    prepare_cmd.add_argument("--export-dir", type=Path, required=True)
    prepare_cmd.add_argument("--out-dir", type=Path, required=True)
    prepare_cmd.add_argument("--ttf", type=Path, required=True)
    prepare_cmd.add_argument("--size", type=int, default=12)
    prepare_cmd.add_argument("--slot-start", type=int, default=SLOT_START)
    patch_cmd = sub.add_parser("patch-bin", help="Patch only approved font cells in a new BIN")
    patch_cmd.add_argument("--source-bin", type=Path, required=True)
    patch_cmd.add_argument("--out-bin", type=Path, required=True)
    patch_cmd.add_argument("--export-dir", type=Path, required=True)
    patch_cmd.add_argument("--edit-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "extract":
        extract(args.source_bin, args.out_dir)
    elif args.command == "prepare":
        prepare(args.export_dir, args.out_dir, args.ttf, args.size, args.slot_start)
    else:
        patch_bin(args.source_bin, args.out_bin, args.export_dir, args.edit_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
