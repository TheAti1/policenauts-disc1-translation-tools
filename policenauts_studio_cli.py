#!/usr/bin/env python3
"""Command-line companion to the English Policenauts Disc 1 Studio GUI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from policenauts_studio_core import (
    build_diagnostic, create_project, open_project, preflight_project, roundtrip_source,
    scan_coverage,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("extract", help="Create a new private text/asset/font project")
    create.add_argument("--source-bin", required=True, type=Path)
    create.add_argument("--source-cue", required=True, type=Path)
    create.add_argument("--master", type=Path,
                        help="Optional private audited catalog; otherwise reconstruct from bundled text-free geometry")
    create.add_argument("--edit", type=Path, help="Optional existing translations")
    create.add_argument("--project", required=True, type=Path)
    create.add_argument("--no-fonts", action="store_true")
    verify = commands.add_parser("verify", help="Validate source and extracted asset hashes")
    verify.add_argument("--project", required=True, type=Path)
    coverage = commands.add_parser("scan", help="Write review-only uncatalogued text candidates")
    coverage.add_argument("--project", required=True, type=Path)
    preflight = commands.add_parser("preflight", help="Audit known build blockers without writing a BIN")
    preflight.add_argument("--project", required=True, type=Path)
    roundtrip = commands.add_parser("roundtrip", help="Reassemble a bit-identical untranslated BIN/CUE")
    roundtrip.add_argument("--project", required=True, type=Path)
    roundtrip.add_argument("--out-bin", required=True, type=Path)
    build = commands.add_parser("build-diagnostic", help="Experimental translation rebuild; never a final ROM")
    build.add_argument("--project", required=True, type=Path)
    build.add_argument("--out-bin", required=True, type=Path)
    build.add_argument("--relocation-plan", type=Path,
                       help="Optional source-specific reviewed MIPS relocation plan")
    args = parser.parse_args()
    if args.command == "extract":
        manifest = create_project(args.source_bin, args.source_cue, args.master,
                                  args.project, args.edit, not args.no_fonts)
        result = {"status": manifest["release_status"],
                  "project": str(args.project), "text_rows": manifest["catalog_entries"],
                  "dpk_assets": len(manifest["dpk_assets"]),
                  "source_dpk_checksum_mismatches": len(manifest["source_dpk_checksum_mismatches"])}
    elif args.command == "verify":
        manifest, entries, _document = open_project(args.project, verify_assets=True)
        result = {"status": "PROJECT_INTEGRITY_PASS_NOT_RUNTIME_PROOF",
                  "text_rows": len(entries), "dpk_assets": len(manifest["dpk_assets"]),
                  "source_dpk_checksum_mismatches": len(manifest["source_dpk_checksum_mismatches"])}
    elif args.command == "scan":
        path, counts = scan_coverage(args.project)
        result = {"status": "REVIEW_ONLY_NOT_EXHAUSTIVE", "report": str(path), **counts}
    elif args.command == "preflight":
        path, summary = preflight_project(args.project)
        result = {"report": str(path), **summary}
    elif args.command == "roundtrip":
        result = roundtrip_source(args.project, args.out_bin)
    else:
        result = build_diagnostic(args.project, args.out_bin,
                                  relocation_plan=args.relocation_plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
