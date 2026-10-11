import struct
import unittest

from policenauts_turkish_font import (
    BASE_GLYPH,
    RESERVED,
    TURKISH_CARRIERS,
    descriptor,
    encode_text,
    rebuilt_record,
)


class TurkishFontTests(unittest.TestCase):
    def test_lossless_mapping_and_reserved_literal_rejection(self):
        sample = "Çç Ğğ Öö Üü Şş İı âîû"
        encoded = encode_text(sample)
        self.assertEqual(len(encoded), len(sample))
        for character, carrier in TURKISH_CARRIERS.items():
            self.assertEqual(encode_text(character), carrier.encode("ascii"))
        self.assertEqual(encode_text("Q"), b"Q")
        self.assertNotIn("Q", RESERVED)
        self.assertIn("\x7f", RESERVED)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            encode_text("Literal &")
        with self.assertRaisesRegex(ValueError, "Unmapped Unicode"):
            encode_text("é")

    def test_repacking_only_uses_reserved_glyph_storage(self):
        table_size = 392
        width = 8
        slots = (table_size - 8) // 4
        first_size = table_size + slots * width * 3
        raw = bytearray(first_size)
        struct.pack_into(">II", raw, 0, table_size, first_size)
        for slot in range(slots):
            struct.pack_into(">I", raw, 8 + 4 * slot, (width << 24) | slot * width * 3)
            start = table_size + slot * width * 3
            raw[start:start + width * 3] = bytes([0x55] * (width * 3))
        before = bytes(raw)
        after, widths = rebuilt_record(before)
        self.assertEqual(set(widths), set(BASE_GLYPH))
        self.assertEqual(len(after), len(before))
        for code in range(0x20, 0x80):
            old_width, old_offset = descriptor(before, code, table_size)
            if chr(code) not in RESERVED:
                self.assertEqual(descriptor(after, code, table_size), (old_width, old_offset))
                start = table_size + old_offset
                self.assertEqual(after[start:start + width * 3], before[start:start + width * 3])
        for carrier in TURKISH_CARRIERS.values():
            new_width, offset = descriptor(after, ord(carrier), table_size)
            self.assertEqual(new_width, width)
            self.assertTrue(table_size <= table_size + offset < first_size)


if __name__ == "__main__":
    unittest.main()
