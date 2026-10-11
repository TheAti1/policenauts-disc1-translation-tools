import unittest

from audit_policenauts_overlays import find_ascii_strings


class OverlayAuditTests(unittest.TestCase):
    def test_find_multiline_and_no_false_overlap_with_binary(self):
        data = b"\xffHello\nworld?\0\x02MEMCARD.DAT\0\xff"
        self.assertEqual(find_ascii_strings(data), [
            (1, b"Hello\nworld?"), (15, b"MEMCARD.DAT")])

    def test_short_labels_are_opt_in(self):
        data = b"\xffYes\0\xffNo\0\xffA\0"
        self.assertEqual(find_ascii_strings(data), [])
        self.assertEqual(find_ascii_strings(data, 2), [
            (1, b"Yes"), (6, b"No")])

    def test_invalid_minimum_is_rejected(self):
        with self.assertRaises(ValueError):
            find_ascii_strings(b"Yes\0", 1)


if __name__ == "__main__":
    unittest.main()
