#!/usr/bin/env python3
"""Read-only audit of source .SZ control addresses after a variable-length build.

This does not prove that every 0x6F control is externally addressed. It flags
the source addresses that no longer contain their corresponding control, a
failure observed in the GAME01 wedding-photo route.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
import json
from pathlib import Path

from build_policenauts_tr_bin import load_edit, load_entries
from policenauts_lossless_sz import encode_game_chunk, structural_tokens


def control_positions(data: bytes) -> list[int]:
    positions = []
    offset = 0
    for kind, blob in structural_tokens(data):
        if kind == "op" and len(blob) == 3 and blob[0] == 0x6F:
            positions.append(offset)
        offset += len(blob)
    return positions


def audit_anchor_drift(items: list[tuple[dict, str]],
                       checkpoints: tuple[int, ...] = ()) -> dict:
    ordered = sorted(items, key=lambda pair: int(pair[0]["local_offset"]))
    spans = []
    anchors = []
    shift = 0
    for entry, translation in ordered:
        old_start = int(entry["local_offset"])
        old = bytes.fromhex(entry["old_bytes_hex"])
        new, _ = encode_game_chunk(entry, translation)
        new_start = old_start + shift
        old_controls = control_positions(old)
        new_controls = control_positions(new)
        if len(old_controls) != len(new_controls):
            raise ValueError(f"Control count changed at entry {entry['index']}")
        spans.append((new_start, new_start + len(new), int(entry["index"])))
        for old_position, new_position in zip(old_controls, new_controls):
            anchors.append((old_start + old_position, new_start + new_position,
                            int(entry["index"])))
        shift += len(new) - len(old)

    starts = [span[0] for span in spans]

    def owner(address: int) -> int | None:
        index = bisect_right(starts, address) - 1
        if index >= 0 and address < spans[index][1]:
            return spans[index][2]
        return None

    rows = [{"original_address": old, "rebuilt_address": new,
             "source_entry": entry, "original_address_rebuilt_owner": owner(old),
             "drift_bytes": new - old}
            for old, new, entry in anchors]
    by_address = {row["original_address"]: row for row in rows}
    over_budget = []
    for left, right in zip(rows, rows[1:]):
        source_room = right["original_address"] - left["original_address"]
        translated_need = right["rebuilt_address"] - left["rebuilt_address"]
        if translated_need > source_room:
            over_budget.append({"from_entry": left["source_entry"],
                                "to_entry": right["source_entry"],
                                "source_bytes": source_room,
                                "translated_bytes": translated_need,
                                "extra_bytes": translated_need - source_room})
    return {
        "status": "UNSAFE_WHEN_EXTERNALLY_ADDRESSED_ANCHORS_MOVE",
        "caveat": "The audit detects address drift; external references and all nested .SZ structures still require runtime/structural proof.",
        "entry_count": len(ordered),
        "anchor_count": len(rows),
        "moved_anchor_count": sum(row["drift_bytes"] != 0 for row in rows),
        "moved_to_other_entry_count": sum(
            row["original_address_rebuilt_owner"] not in (None, row["source_entry"])
            for row in rows),
        "fixed_interval_over_budget_count": len(over_budget),
        "fixed_interval_extra_bytes_sum": sum(row["extra_bytes"] for row in over_budget),
        "largest_fixed_interval_overflows": sorted(
            over_budget, key=lambda row: row["extra_bytes"], reverse=True)[:20],
        "checkpoints": {str(address): by_address.get(address) for address in checkpoints},
        "anchors": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--edit", type=Path, required=True)
    parser.add_argument("--container", default="GAME1.DPK")
    parser.add_argument("--entry-name", default="GAME01.SZ")
    parser.add_argument("--checkpoint", type=int, action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Report path already exists; inputs and previous reports are preserved")
    master = load_entries(args.master)
    translations = load_edit(args.edit, master)
    items = [(entry, translation) for entry, translation in zip(master, translations)
             if entry.get("container") == args.container and
             entry.get("container_entry_name") == args.entry_name]
    if not items:
        parser.error("No catalogued entries match the selected bank")
    result = audit_anchor_drift(items, tuple(args.checkpoint))
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "anchors"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
