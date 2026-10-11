import copy
import json
from pathlib import Path
import tempfile
import unittest

from make_policenauts_rex_json import make_document
from policenauts_disc_tools import compute_crc32_bzip2
from policenauts_studio import entry_status
from policenauts_studio_geometry import FORMAT as GEOMETRY_FORMAT, geometry_from_master
from policenauts_studio_core import (
    FORMAT, fast_crc32_bzip2, file_sha256, open_project, save_json,
    save_translations, validate_cue, validate_document,
)


class StudioCoreTests(unittest.TestCase):
    def test_crc_table_matches_reference(self):
        for size in (0, 1, 4, 17, 255, 4096):
            value = bytes((index * 37 + 9) % 256 for index in range(size))
            self.assertEqual(fast_crc32_bzip2(value), compute_crc32_bzip2(value))

    def test_cue_rejects_other_bin(self):
        with tempfile.TemporaryDirectory() as root:
            cue = Path(root) / "game.cue"
            cue.write_text('FILE "other.bin" BINARY\n  TRACK 01 MODE2/2352\n', encoding="ascii")
            with self.assertRaisesRegex(ValueError, "selected BIN"):
                validate_cue(Path(root) / "game.bin", cue)

    def test_translation_identity_and_external_edit_guard(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            entries = [{"index": 0, "source": "bin_ascii", "text_with_breaks": "Yes",
                        "old_bytes_hex": "59 65 73", "old_byte_len": 3}]
            catalog = project / "catalog.json"
            save_json(catalog, {"entries": entries})
            document = make_document({"entries": entries})
            save_json(project / "translations.json", document)
            save_json(project / "project.json", {
                "format": FORMAT, "source_bin": str(project / "not-needed.bin"),
                "source_cue": str(project / "not-needed.cue"),
                "catalog_sha256": file_sha256(catalog), "catalog_entries": 1,
                "dpk_assets": [],
            })
            _manifest, _entries, loaded = open_project(project, verify_source_image=False)
            self.assertEqual(loaded["entries"][0]["source"], "Yes")
            old_hash = file_sha256(project / "translations.json")
            edited = copy.deepcopy(loaded)
            edited["entries"][0]["translation"] = "Evet"
            new_hash = save_translations(project, edited, old_hash)
            self.assertNotEqual(old_hash, new_hash)
            with self.assertRaisesRegex(ValueError, "changed on disk"):
                save_translations(project, edited, old_hash)
            tampered = copy.deepcopy(edited)
            tampered["entries"][0]["source"] = "No"
            with self.assertRaisesRegex(ValueError, "differs from catalog"):
                validate_document(entries, tampered)

    def test_row_does_not_silently_fold_turkish_glyph(self):
        entry = {"source": "bin_ascii", "text_with_breaks": "Yes",
                 "old_bytes_hex": "59 65 73", "old_byte_len": 3}
        self.assertEqual(entry_status(entry, "Evet")[0], "GROWS")
        self.assertEqual(entry_status(entry, "Eğ")[0], "FITS")
        self.assertEqual(entry_status(entry, None)[0], "UNTRANSLATED")

    def test_public_geometry_contains_offsets_not_dialogue(self):
        master = {"entries": [{
            "index": 0, "source": "bin_ascii", "container": "BIN.DPK",
            "container_entry_index": 6, "container_entry_name": "MENU.BIN",
            "local_offset": 120, "user_offset": 4096, "old_byte_len": 11,
            "text_with_breaks": "Secret line", "old_bytes_hex": "53 65 63 72 65 74",
        }]}
        geometry = geometry_from_master(master, "a" * 64)
        self.assertEqual(geometry["entries"][0]["record_index"], 6)
        self.assertEqual(geometry["entries"][0]["byte_length"], 11)
        self.assertNotIn("Secret line", str(geometry))
        self.assertNotIn("old_bytes_hex", str(geometry))

    def test_bundled_geometry_has_no_source_text_fields(self):
        path = Path(__file__).resolve().parents[1] / "data" / "disc1_english_geometry.json"
        geometry = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(geometry["format"], GEOMETRY_FORMAT)
        self.assertEqual(geometry["count"], 16533)
        forbidden = {"text", "text_with_breaks", "old_bytes_hex", "raw_command_text",
                     "translation", "source_file"}
        self.assertFalse(any(forbidden & set(row) for row in geometry["entries"]))


if __name__ == "__main__":
    unittest.main()
