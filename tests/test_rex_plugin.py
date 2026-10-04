import json
import tempfile
import unittest
from pathlib import Path

from rex_plugin import policenauts_disc1 as plugin


class RexPluginTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.folder = Path(self.tempdir.name)
        self.edit = self.folder / "policenauts_texts_tr_edit_audited.json"
        sources = ["First\nline", "Insert Disc %d", "Again", "Again"]
        self.current = ["Eski\nsatir", "Disk %d Tak", "Bir", "Iki"]
        self.edit.write_text(json.dumps(self.current), encoding="utf-8")
        master = {
            "entries": [
                {"index": index, "source": "bin_ascii", "text_with_breaks": value}
                for index, value in enumerate(sources)
            ]
        }
        self.master = self.folder / plugin.MASTER_FILENAME
        self.master.write_text(json.dumps(master), encoding="utf-8")

    def test_english_source_and_unique_ordered_keys(self):
        texts = plugin.extract_translatable_texts(self.edit.read_text(encoding="utf-8"), self.edit)
        self.assertEqual(list(texts), ["p00000", "p00001", "p00002", "p00003"])
        self.assertEqual(texts["p00000"][0], "First|line")
        self.assertEqual(texts["p00001"][0], "Insert Disc %d")
        self.assertEqual(texts["p00002"][0], texts["p00003"][0])

    def test_rebuild_keeps_missing_answers_and_allows_omitted_codes(self):
        content = self.edit.read_text(encoding="utf-8")
        texts = plugin.extract_translatable_texts(content, self.edit)
        originals = {key: item[0] for key, item in texts.items()}
        answers = {
            "p00000": "Yeni|satir",
            "p00001": "Diski tak",  # An omitted %d is not a plugin error.
            "p00002": "Again",  # Unchanged English must not erase a Turkish edit.
        }
        result = json.loads(plugin.rebuild_content_with_translations(content, originals, answers))
        self.assertEqual(result, ["Yeni\nsatir", "Diski tak", "Bir", "Iki"])

    def test_literal_escape_and_physical_newline(self):
        self.assertEqual(plugin._restore("Bir\\nIki"), "Bir\nIki")
        self.assertEqual(plugin._restore("Bir\nIki"), "Bir\nIki")

    def test_mismatched_master_is_rejected(self):
        self.master.write_text(json.dumps({"entries": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metin sayısı"):
            plugin.extract_translatable_texts(self.edit.read_text(encoding="utf-8"), self.edit)


if __name__ == "__main__":
    unittest.main()
