#!/usr/bin/env python3
"""Private, source-pinned Disc 1 project extraction and diagnostic rebuilding.

No game data is bundled with this module. Unknown text and image pixels remain
opaque; a successful static check is not evidence of runtime safety.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from build_policenauts_tr_bin import load_edit, load_entries, verify_source, write_cue
from make_policenauts_rex_json import KIND_BY_SOURCE, make_document
from policenauts_disc_tools import (
    SECTOR_USER, dpk_records_from_image, iso_nauts_files,
    read_user_bytes_from_raw,
)


FORMAT = "policenauts-studio-project-v1"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
KNOWN_TYPES = {"game_sz", "jxs_voice", "movie_ascii", "bin_ascii"}


def _bzip2_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        crc = value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
        table.append(crc)
    return tuple(table)


BZIP2_TABLE = _bzip2_table()


def fast_crc32_bzip2(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = ((crc << 8) ^ BZIP2_TABLE[((crc >> 24) ^ byte) & 0xFF]) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path: Path, payload: dict, *, replace: bool = False) -> None:
    if path.exists() and not replace:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def validate_cue(source_bin: Path, source_cue: Path) -> None:
    lines = source_cue.read_text(encoding="ascii").splitlines()
    if not lines or lines[0] != f'FILE "{source_bin.name}" BINARY':
        raise ValueError("CUE must reference the selected BIN by filename")
    if not any(line.strip() == "TRACK 01 MODE2/2352" for line in lines):
        raise ValueError("Expected a MODE2/2352 PlayStation CUE")


def validate_master(entries: list[dict]) -> None:
    for index, entry in enumerate(entries):
        if (not isinstance(entry, dict) or type(entry.get("index")) is not int or
                entry["index"] != index or entry.get("source") not in KNOWN_TYPES or
                not isinstance(entry.get("text_with_breaks"), str)):
            raise ValueError(f"Invalid catalog entry {index}")


def extract_dpk_assets(source_bin: Path, project_dir: Path) -> list[dict]:
    result = []
    with source_bin.open("rb") as stream:
        files = iso_nauts_files(stream)
        for container, (extent, size) in sorted(files.items()):
            if not container.endswith(".DPK"):
                continue
            if not SAFE_NAME.fullmatch(container):
                raise ValueError(f"Unsafe DPK name: {container!r}")
            base = extent * SECTOR_USER
            for record in dpk_records_from_image(stream, base):
                name = record["name"]
                if not SAFE_NAME.fullmatch(name):
                    raise ValueError(f"Unsafe DPK record name: {name!r}")
                offset = record["data_offset"]
                length = record["data_size"]
                if offset < 0 or length < 0 or offset + length > size:
                    raise ValueError(f"DPK entry exceeds ISO bounds: {container}/{name}")
                data = read_user_bytes_from_raw(stream, base + offset, length)
                calculated_crc = fast_crc32_bzip2(data)
                relative = Path("assets") / container / f'{record["index"]:04d}_{name}'
                path = project_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    output.write(data)
                result.append({
                    "container": container, "name": name, "index": record["index"],
                    "iso_extent": extent, "data_offset": offset, "size": length,
                    "crc32_bzip2": record["checksum"], "calculated_crc32_bzip2": calculated_crc,
                    "crc_matches": calculated_crc == record["checksum"],
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "path": relative.as_posix(),
                })
    return result


def create_project(source_bin: Path, source_cue: Path, master: Path | None,
                   project_dir: Path, edit: Path | None = None,
                   export_fonts: bool = True) -> dict:
    paths = [source_bin, source_cue, project_dir]
    if master is not None:
        paths.append(master)
    if edit is not None:
        paths.append(edit)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("Inputs and project directory must be distinct")
    if project_dir.exists():
        raise FileExistsError(f"Refusing to overwrite project: {project_dir}")
    if (not source_bin.is_file() or not source_cue.is_file() or
            master is not None and not master.is_file()):
        raise FileNotFoundError("Source BIN/CUE or selected audited master is missing")
    validate_cue(source_bin, source_cue)
    source_digest = file_sha256(source_bin)
    cue_digest = file_sha256(source_cue)
    if master is None:
        from policenauts_studio_geometry import reconstruct_master
        geometry_path = Path(__file__).resolve().parent / "data" / "disc1_english_geometry.json"
        source_master = reconstruct_master(source_bin, read_json(geometry_path))
        entries = source_master["entries"]
        catalog_origin = "source_bin_plus_text_free_geometry"
    else:
        source_master = None
        entries = load_entries(master)
        catalog_origin = "user_supplied_audited_master"
    validate_master(entries)
    verify_source(source_bin, entries)
    document = make_document({"entries": entries})
    if edit is not None:
        translations = load_edit(edit, entries)
        for row, translation in zip(document["entries"], translations):
            row["translation"] = translation

    project_dir.mkdir(parents=True)
    if master is None:
        save_json(project_dir / "catalog.json", source_master)
    else:
        shutil.copyfile(master, project_dir / "catalog.json")
    save_json(project_dir / "translations.json", document)
    assets = extract_dpk_assets(source_bin, project_dir)
    if export_fonts:
        from policenauts_font_tools import extract, extract_rubi
        extract(source_bin, project_dir / "fonts")
        extract_rubi(source_bin, project_dir / "rubi")
    if file_sha256(source_bin) != source_digest or file_sha256(source_cue) != cue_digest:
        raise ValueError("Source BIN/CUE changed during extraction; project is incomplete")
    stale_checksums = [f"{item['container']}/{item['name']}"
                       for item in assets if not item["crc_matches"]]
    manifest = {
        "format": FORMAT, "source_bin": str(source_bin.resolve()),
        "source_cue": str(source_cue.resolve()),
        "source_sha256": source_digest, "source_cue_sha256": cue_digest,
        "catalog_sha256": file_sha256(project_dir / "catalog.json"),
        "catalog_origin": catalog_origin,
        "catalog_entries": len(entries), "dpk_assets": assets,
        "font_exported": export_fonts,
        "source_dpk_checksum_mismatches": stale_checksums,
        "release_status": "INCOMPLETE_COVERAGE_NOT_RUNTIME_VERIFIED",
        "known_gaps": [
            "The audited catalog does not prove that every displayed string is found.",
            "Some words are baked into movie or picture pixels and are not editable text rows.",
            "Unclassified script references and runtime behavior require separate proof.",
        ],
    }
    save_json(project_dir / "project.json", manifest)
    return manifest


def open_project(project_dir: Path, *, verify_source_image: bool = True,
                 verify_assets: bool = False) -> tuple[dict, list[dict], dict]:
    manifest = read_json(project_dir / "project.json")
    if manifest.get("format") != FORMAT:
        raise ValueError("Unsupported studio project format")
    catalog = project_dir / "catalog.json"
    if file_sha256(catalog) != manifest.get("catalog_sha256"):
        raise ValueError("Audited text catalog changed since extraction")
    entries = load_entries(catalog)
    validate_master(entries)
    if len(entries) != manifest.get("catalog_entries"):
        raise ValueError("Catalog count differs from project manifest")
    source_bin = Path(manifest["source_bin"])
    source_cue = Path(manifest["source_cue"])
    if verify_source_image:
        validate_cue(source_bin, source_cue)
        if file_sha256(source_bin) != manifest["source_sha256"]:
            raise ValueError("Source BIN hash changed since extraction")
        if "source_cue_sha256" in manifest and file_sha256(source_cue) != manifest["source_cue_sha256"]:
            raise ValueError("Source CUE hash changed since extraction")
    document = read_json(project_dir / "translations.json")
    validate_document(entries, document)
    if verify_assets:
        for item in manifest["dpk_assets"]:
            path = (project_dir / item["path"]).resolve()
            if not path.is_relative_to(project_dir.resolve()) or file_sha256(path) != item["sha256"]:
                raise ValueError(f"Extracted asset changed: {item['path']}")
    return manifest, entries, document


def validate_document(entries: list[dict], document: dict) -> None:
    if (set(document) != {"format", "count", "source_language", "target_language", "entries"} or
            document.get("format") != "policenauts-rex-v1" or
            document.get("source_language") != "en" or
            document.get("target_language") != "tr" or
            type(document.get("count")) is not int or document["count"] != len(entries) or
            not isinstance(document.get("entries"), list) or
            len(document["entries"]) != len(entries)):
        raise ValueError("Translation document shape is invalid")
    for index, (entry, row) in enumerate(zip(entries, document["entries"])):
        if (not isinstance(row, dict) or
                set(row) != {"id", "index", "kind", "source", "translation"} or
                row.get("id") != f"p{index:05d}" or
                type(row.get("index")) is not int or row["index"] != index or
                row.get("source") != entry["text_with_breaks"] or
                row.get("kind") != KIND_BY_SOURCE[entry["source"]] or
                row.get("translation") is not None and not isinstance(row.get("translation"), str)):
            raise ValueError(f"Translation row differs from catalog at {index}")


def save_translations(project_dir: Path, document: dict, expected_sha256: str) -> str:
    path = project_dir / "translations.json"
    if file_sha256(path) != expected_sha256:
        raise ValueError("Translations changed on disk; reload before saving")
    _manifest, entries, _old = open_project(project_dir, verify_source_image=False)
    validate_document(entries, document)
    save_json(path, document, replace=True)
    return file_sha256(path)


def export_build_edit(project_dir: Path, output: Path) -> dict:
    _manifest, entries, document = open_project(project_dir)
    if output.exists():
        raise FileExistsError(output)
    missing = 0
    resolved = make_document({"entries": entries})
    for source, target in zip(document["entries"], resolved["entries"]):
        translation = source["translation"]
        if translation is None or not translation.strip():
            translation = target["source"]
            missing += 1
        target["translation"] = translation
    save_json(output, resolved)
    return {"catalog_entries": len(entries), "untranslated_fallbacks": missing,
            "release_status": "NOT_RELEASE_READY"}


def roundtrip_source(project_dir: Path, out_bin: Path) -> dict:
    """Reassemble an unmodified BIN/CUE exactly; do not apply translations."""
    manifest, _entries, _document = open_project(project_dir, verify_assets=True)
    if out_bin.suffix.lower() != ".bin":
        raise ValueError("Output filename must end in .bin")
    out_cue = out_bin.with_suffix(".cue")
    partial = out_bin.with_name(out_bin.name + ".partial")
    if any(path.exists() for path in (out_bin, out_cue, partial)):
        raise FileExistsError("Round-trip output already exists")
    source = Path(manifest["source_bin"])
    if out_bin.resolve() == source.resolve():
        raise ValueError("Output cannot replace the source BIN")
    shutil.copyfile(source, partial)
    digest = file_sha256(partial)
    if digest != manifest["source_sha256"]:
        raise AssertionError("Bit-exact round-trip check failed")
    os.replace(partial, out_bin)
    write_cue(Path(manifest["source_cue"]), out_cue, out_bin)
    return {"status": "BIT_EXACT_SOURCE_COPY", "out_bin": str(out_bin),
            "out_cue": str(out_cue), "sha256": digest,
            "translations_applied": 0}


def preflight_project(project_dir: Path, *,
                      resolved_edit: Path | None = None) -> tuple[Path, dict]:
    """Audit full bank/sector fit and known loader hazards without writing a BIN."""
    from audit_policenauts_repack_dry_run import audit
    from build_lossless_game_voice_probe import verify_fixed_loader_widths

    manifest, entries, _document = open_project(project_dir)
    index = 1
    if resolved_edit is None:
        while ((project_dir / f"preflight_resolved_{index:03d}.json").exists() or
               (project_dir / f"preflight_report_{index:03d}.json").exists()):
            index += 1
        resolved_edit = project_dir / f"preflight_resolved_{index:03d}.json"
    else:
        while (project_dir / f"preflight_report_{index:03d}.json").exists():
            index += 1
    export = export_build_edit(project_dir, resolved_edit)
    source = Path(manifest["source_bin"])
    result = audit(project_dir / "catalog.json", resolved_edit, source)
    translations = load_edit(resolved_edit, entries)
    loader_errors = []
    for container in ("GAME1.DPK", "GAME2.DPK"):
        items = [(entry, translation) for entry, translation in zip(entries, translations)
                 if entry["source"] == "game_sz" and
                 entry["container"] == container and
                 entry["container_entry_name"] == "GAME01.SZ"]
        try:
            verify_fixed_loader_widths(container, items)
        except ValueError as exc:
            loader_errors.append(str(exc))
    blockers = {
        "game_physical_failures": len(result["game"]["physical_fit_failures"]),
        "game_boundary_e5_risks": len(result["game"]["boundary_e5_risks"]),
        "voice_physical_failures": len(result["voice"]["physical_fit_failures"]),
        "movie_slot_overflows": len(result["movie"]["fixed_span_overflow_ids"]),
        "movie_live_conflicts": len(result["movie"]["overwrites_live_data_ids"]),
        "menu_live_conflicts": len(result["menu"]["in_place_conflict_ids"]),
        "menu_encoding_failures": len(result["menu"]["encoding_failures"]),
        "loader_width_errors": len(loader_errors),
    }
    summary = {"status": "BLOCKED" if any(blockers.values()) else "STATIC_PREFLIGHT_PASS_NOT_RUNTIME_PROOF",
               "untranslated_fallbacks": export["untranslated_fallbacks"],
               "blockers": blockers, "loader_errors": loader_errors,
               "menu_source_span_overflows_advisory": len(result["menu"]["fixed_span_overflow_ids"]),
               "source_dpk_checksum_mismatches": len(manifest["source_dpk_checksum_mismatches"])}
    report_path = project_dir / f"preflight_report_{index:03d}.json"
    save_json(report_path, {"summary": summary, "details": result,
                            "resolved_edit": str(resolved_edit)})
    return report_path, summary


def build_diagnostic(project_dir: Path, out_bin: Path, *,
                     relocation_plan: Path | None = None) -> dict:
    """Create a new experimental image; static checks do not certify gameplay."""
    from build_lossless_game_voice_probe import build, catalogued_movie_names
    from verify_policenauts_image import verify_dpk, verify_movie_timing, verify_sectors

    manifest, entries, _document = open_project(project_dir, verify_assets=True)
    source = Path(manifest["source_bin"])
    cue = Path(manifest["source_cue"])
    if out_bin.suffix.lower() != ".bin":
        raise ValueError("Output filename must end in .bin")
    out_cue = out_bin.with_suffix(".cue")
    report = out_bin.with_suffix(".diagnostic.json")
    edit = out_bin.with_suffix(".resolved.json")
    if any(path.exists() for path in (out_bin, out_cue, report, edit)):
        raise FileExistsError("Output files already exist; choose a new basename")
    _preflight_path, preflight = preflight_project(project_dir, resolved_edit=edit)
    if preflight["status"] == "BLOCKED":
        raise ValueError(f"Static preflight blocked diagnostic build: {preflight['blockers']}")
    plans = read_json(relocation_plan) if relocation_plan else {}
    overlays = tuple(sorted({entry["container_entry_name"] for entry in entries
                             if entry["source"] == "bin_ascii"}))
    movies = catalogued_movie_names(entries)
    try:
        result = build(source, cue, project_dir / "catalog.json", edit,
                       out_bin, out_cue, report, overlays, plans, movies)
        result["static_verification"] = {
            "form1_sectors": verify_sectors(source, out_bin),
            "dpk_entries": verify_dpk(out_bin, entries),
            "movie_timing_headers": verify_movie_timing(source, out_bin, entries),
        }
        result["untranslated_fallbacks"] = preflight["untranslated_fallbacks"]
        result["release_status"] = "DIAGNOSTIC_NOT_FINAL"
        save_json(report, result, replace=True)
        return result
    except Exception:
        # Keep partial output for inspection. Never alter the source or project.
        raise


def scan_coverage(project_dir: Path) -> tuple[Path, dict]:
    """Save heuristic omissions for review, never auto-approve them as text."""
    from audit_policenauts_game import scan_game
    from audit_policenauts_movies import scan_movies
    from audit_policenauts_overlays import audit as audit_overlays
    from audit_policenauts_voice_toc import audit as audit_voice

    manifest, entries, _document = open_project(project_dir)
    source = Path(manifest["source_bin"])
    catalog = project_dir / "catalog.json"
    with source.open("rb") as stream:
        game = scan_game(stream, entries)
        movies = scan_movies(stream, entries)
    overlays = audit_overlays(source, catalog, min_length=3)
    voice = audit_voice(source, catalog)
    report = {
        "status": "REVIEW_ONLY_NOT_EXHAUSTIVE",
        "game": game, "movies": movies, "overlays": overlays, "voice": voice,
        "warning": "Heuristic candidates can be data; picture and video pixels are not scanned.",
    }
    index = 1
    while (project_dir / f"coverage_review_{index:03d}.json").exists():
        index += 1
    path = project_dir / f"coverage_review_{index:03d}.json"
    save_json(path, report)
    return path, {
        "game_plausible": game["unmapped_plausible_count"],
        "game_short_review": game["unmapped_short_review_count"],
        "movie_timed": movies["new_plausible_timed_subtitles"],
        "movie_untimed": movies["untimed_plausible_strings_for_review"],
        "overlay_candidates": overlays["uncatalogued_count"],
        "voice_status": voice["status"],
    }
