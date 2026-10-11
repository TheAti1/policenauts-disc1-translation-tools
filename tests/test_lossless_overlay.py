import unittest
import struct

from policenauts_lossless_overlay import rebuild_overlay


class LosslessOverlayTests(unittest.TestCase):
    def test_replacement_uses_zero_padding_only(self):
        old = b"Start?\0\0\0Next\0"
        entry = {"index": 0, "local_offset": 0,
                 "old_bytes_hex": b"Start?".hex(), "text_with_breaks": "Start?"}
        new, report = rebuild_overlay(old, [(entry, "Devam mı")])
        self.assertEqual(len(new), len(old))
        self.assertEqual(new[-5:], b"Next\0")
        self.assertEqual(report["changed"], 1)

    def test_overflow_is_rejected_without_touching_input(self):
        old = b"Hi\0No\0"
        entry = {"index": 1, "local_offset": 0,
                 "old_bytes_hex": b"Hi".hex(), "text_with_breaks": "Hi"}
        with self.assertRaisesRegex(ValueError, "live bytes"):
            rebuild_overlay(old, [(entry, "Merhaba")])
        self.assertEqual(old, b"Hi\0No\0")

    def test_relocates_string_and_all_verified_mips_references(self):
        data = bytearray(128)
        data[32:38] = b"File!\0"
        struct.pack_into("<I", data, 64, 0x80070020)
        struct.pack_into("<I", data, 76, 0x3C058007)  # lui a1, 0x8007
        struct.pack_into("<I", data, 80, 0x24A50020)  # addiu a1, a1, 0x20
        entry = {"index": 5, "local_offset": 32,
                 "old_bytes_hex": b"File!".hex(), "text_with_breaks": "File!"}
        plan = {32: {"target_offset": 10, "va_base": "0x80070000",
                     "pointer_offsets": [64], "addiu_offsets": [80]}}
        new, result = rebuild_overlay(bytes(data), [(entry, "Dosya")], plan)
        self.assertEqual(new[10:16], b"Dosya\0")
        self.assertEqual(new[32:38], b"\0" * 6)
        self.assertEqual(struct.unpack_from("<I", new, 64)[0], 0x8007000A)
        self.assertEqual(struct.unpack_from("<I", new, 80)[0], 0x24A5000A)
        self.assertEqual(result["changed"], 1)

    def test_relocation_rejects_unlisted_pointer(self):
        data = bytearray(128)
        data[32:38] = b"File!\0"
        struct.pack_into("<I", data, 64, 0x80070020)
        struct.pack_into("<I", data, 68, 0x80070020)
        struct.pack_into("<I", data, 76, 0x3C058007)
        struct.pack_into("<I", data, 80, 0x24A50020)
        entry = {"index": 5, "local_offset": 32,
                 "old_bytes_hex": b"File!".hex(), "text_with_breaks": "File!"}
        plan = {32: {"target_offset": 10, "va_base": "0x80070000",
                     "pointer_offsets": [64], "addiu_offsets": [80]}}
        with self.assertRaisesRegex(ValueError, "unlisted"):
            rebuild_overlay(bytes(data), [(entry, "Dosya")], plan)

    def test_relocation_rejects_neighbor_translation_consuming_separator(self):
        data = bytearray(128)
        data[2:7] = b"Old\0\0"
        data[32:38] = b"File!\0"
        struct.pack_into("<I", data, 64, 0x80070020)
        struct.pack_into("<I", data, 76, 0x3C058007)
        struct.pack_into("<I", data, 80, 0x24A50020)
        earlier = {"index": 1, "local_offset": 2,
                   "old_bytes_hex": b"Old".hex(), "text_with_breaks": "Old"}
        entry = {"index": 5, "local_offset": 32,
                 "old_bytes_hex": b"File!".hex(), "text_with_breaks": "File!"}
        plan = {32: {"target_offset": 6, "va_base": "0x80070000",
                     "pointer_offsets": [64], "addiu_offsets": [80]}}
        with self.assertRaisesRegex(ValueError, "zero hole"):
            rebuild_overlay(bytes(data), [(earlier, "Long"), (entry, "Dosya")], plan)


if __name__ == "__main__":
    unittest.main()
