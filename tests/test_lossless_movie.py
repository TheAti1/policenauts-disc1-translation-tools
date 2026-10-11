import unittest

from build_lossless_game_voice_probe import catalogued_movie_names
from build_policenauts_tr_bin import movie_prefix
from policenauts_lossless_movie import encode_movie_slot, encode_movie_slot_in_record
from policenauts_movie_integrity import MovieRecord


class LosslessMovieTests(unittest.TestCase):
    def test_catalogued_movie_selection_is_unique_and_sorted(self):
        entries = [{"source": "movie_ascii", "container_entry_name": "B.MOV"},
                   {"source": "game_sz", "container_entry_name": "A.SZ"},
                   {"source": "movie_ascii", "container_entry_name": "A.MOV"},
                   {"source": "movie_ascii", "container_entry_name": "B.MOV"}]
        self.assertEqual(catalogued_movie_names(entries), ("A.MOV", "B.MOV"))

    def test_break_and_padding_preserve_slot(self):
        entry = {"index": 3, "old_bytes_hex": b"Hello there".hex(),
                 "text_with_breaks": "Hello there"}
        result = encode_movie_slot(entry, "Mer\nhaba")
        self.assertEqual(len(result), len(b"Hello there") + 1)
        self.assertTrue(result.startswith(b"Mer\x80|haba\0"))

    def test_overflow_is_rejected(self):
        entry = {"index": 3, "old_bytes_hex": b"Hi".hex(),
                 "text_with_breaks": "Hi"}
        with self.assertRaisesRegex(ValueError, "fixed slot"):
            encode_movie_slot(entry, "Merhaba")

    def test_unmodified_source_keeps_bytes(self):
        entry = {"index": 3, "old_bytes_hex": b"Hi  ".hex(),
                 "text_with_breaks": "Hi"}
        self.assertEqual(encode_movie_slot(entry, "Hi"), b"Hi  \0")

    def test_leading_source_guard_is_not_replaced_with_space(self):
        entry = {"index": 4, "old_bytes_hex": b"?Hello".hex(),
                 "text_with_breaks": "Hello"}
        self.assertEqual(movie_prefix(entry), b"?")
        self.assertEqual(encode_movie_slot(entry, "Merha"), b"?Merha\0")

    def test_line_break_in_source_prefix_is_preserved_as_two_bytes(self):
        entry = {"old_bytes_hex": b" \x80|Hello\x80|there".hex(),
                 "text_with_breaks": "Hello\nthere"}
        self.assertEqual(movie_prefix(entry), b" \x80|")

    def test_record_encoder_keeps_previous_picture_byte(self):
        entry = {"index": 8, "old_bytes_hex": b"?Hello".hex(),
                 "text_with_breaks": "?Hello"}
        movie = b"?Hello\0" + b"\0" * 16
        records = [MovieRecord(1, 7, b"SCTEGOLD")]
        self.assertEqual(encode_movie_slot_in_record(entry, "Selam", movie,
                                                    records, 0), b"?Selam\0")

    def test_record_encoder_preserves_source_after_declared_size(self):
        entry = {"index": 9, "old_bytes_hex": b"Hello old tail".hex(),
                 "text_with_breaks": "Hello old tail"}
        movie = b"Hello old tail\0" + b"\0" * 16
        records = [MovieRecord(0, 6, b"SCTEGOLD")]
        self.assertEqual(encode_movie_slot_in_record(entry, "Selam", movie,
                                                    records, 0),
                         b"Selam\0old tail\0")

    def test_record_encoder_does_not_duplicate_visible_prefix_punctuation(self):
        entry = {"index": 10, "old_bytes_hex": b'?"#"Hello'.hex(),
                 "text_with_breaks": '"#"Hello'}
        movie = b'?"#"Hello\0'
        records = [MovieRecord(4, len(movie), b"SCTEGOLD")]
        self.assertEqual(encode_movie_slot_in_record(entry, '"#"Merha', movie,
                                                    records, 0), b'?"#"Merha\0')


if __name__ == "__main__":
    unittest.main()
