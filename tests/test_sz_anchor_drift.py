import unittest

from audit_policenauts_sz_anchor_drift import audit_anchor_drift


def entry(index, offset, text, prefix=b""):
    old = prefix + bytes((-ord(char)) & 0xFF for char in text)
    return {"index": index, "local_offset": offset, "old_bytes_hex": old.hex(),
            "text_with_breaks": text, "raw_command_text": text}


class AnchorDriftTests(unittest.TestCase):
    def test_shrinking_previous_entry_moves_anchor_into_another_entry(self):
        first = entry(1, 100, "HelloWorld")
        second = entry(2, 110, "There", b"\x6f\xff\xf4")
        result = audit_anchor_drift([(first, "Hi"), (second, "There")], (110,))
        anchor = result["checkpoints"]["110"]
        self.assertEqual(anchor["rebuilt_address"], 102)
        self.assertEqual(anchor["original_address_rebuilt_owner"], None)
        self.assertEqual(result["moved_anchor_count"], 1)

    def test_unchanged_source_keeps_anchor_at_same_address(self):
        row = entry(5, 200, "Photo", b"\x6f\x00\x01")
        result = audit_anchor_drift([(row, "Photo")], (200,))
        self.assertEqual(result["moved_anchor_count"], 0)
        self.assertEqual(result["checkpoints"]["200"]["drift_bytes"], 0)

    def test_reports_fixed_interval_overflow(self):
        left = entry(1, 10, "One", b"\x6f\x00\x01")
        right = entry(2, 16, "Two", b"\x6f\x00\x01")
        result = audit_anchor_drift([(left, "A longer line"), (right, "Two")])
        self.assertEqual(result["fixed_interval_over_budget_count"], 1)
        self.assertGreater(result["fixed_interval_extra_bytes_sum"], 0)


if __name__ == "__main__":
    unittest.main()
