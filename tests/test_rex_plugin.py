import json
import tempfile
import unittest
from pathlib import Path

from build_policenauts_tr_bin import load_edit
from make_policenauts_rex_json import make_document
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

    def test_structured_json_writes_only_translation_fields(self):
        master = json.loads(self.master.read_text(encoding="utf-8"))
        document = make_document(master)
        self.assertEqual(document["count"], 4)
        self.assertTrue(all(row["translation"] is None for row in document["entries"]))
        content = json.dumps(document)
        source = self.folder / "policenauts_rex_english.json"
        source.write_text(content, encoding="utf-8")
        texts = plugin.extract_translatable_texts(content, source)
        self.assertEqual(texts["p00000"][0], "First|line")
        originals = {key: item[0] for key, item in texts.items()}
        answers = {"p00000": "Bir|iki", "p00001": "Diski tak", "p00002": "Again"}
        rebuilt = json.loads(plugin.rebuild_content_with_translations(content, originals, answers))
        self.assertEqual(rebuilt["count"], 4)
        self.assertEqual(rebuilt["entries"][0]["source"], "First\nline")
        self.assertEqual(rebuilt["entries"][0]["translation"], "Bir\niki")
        self.assertEqual(rebuilt["entries"][1]["translation"], "Diski tak")
        self.assertEqual(rebuilt["entries"][2]["translation"], "Again")
        self.assertIsNone(rebuilt["entries"][3]["translation"])

    def test_builder_rejects_incomplete_or_reordered_structured_json(self):
        master = json.loads(self.master.read_text(encoding="utf-8"))
        rows = master["entries"]
        document = make_document(master)
        source = self.folder / "policenauts_rex_english.json"
        source.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "untranslated entries"):
            load_edit(source, rows)
        for item in document["entries"]:
            item["translation"] = "Ceviri"
        source.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(load_edit(source, rows), ["Ceviri"] * 4)
        document["entries"][1]["source"] = "Wrong source"
        source.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "source/ID/order"):
            load_edit(source, rows)

    def test_structured_resume_preserves_existing_translation_on_english_echo(self):
        master = json.loads(self.master.read_text(encoding="utf-8"))
        document = make_document(master)
        document["entries"][0]["translation"] = "Onceki ceviri"
        content = json.dumps(document)
        source = self.folder / "TR_policenauts_rex_english.json"
        source.write_text(content, encoding="utf-8")
        texts = plugin.extract_translatable_texts(content, source)
        originals = {key: item[0] for key, item in texts.items()}
        rebuilt = json.loads(plugin.rebuild_content_with_translations(
            content, originals, {"p00000": "First|line", "p00001": "Diski tak"}
        ))
        self.assertEqual(rebuilt["entries"][0]["translation"], "Onceki ceviri")
        self.assertEqual(rebuilt["entries"][1]["translation"], "Diski tak")


if __name__ == "__main__":
    unittest.main()
