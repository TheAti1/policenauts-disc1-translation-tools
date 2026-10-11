import unittest

from build_lossless_game_voice_probe import verify_fixed_loader_widths


def loader_item(index, offset, source, control, footer=b""):
    old = control + bytes((-ord(char)) & 0xFF for char in source) + footer
    entry = {
        "index": index,
        "local_offset": offset,
        "text_with_breaks": source,
        "raw_command_text": source,
        "old_bytes_hex": old.hex(),
    }
    return entry


class FixedLoaderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            (loader_item(174, 17061, "File loaded.", b"\x6f\x00\x01"),
             "Dosya yüklü."),
            (loader_item(175, 17077, "An error occurred during load",
                         b"\x6f\x00\x01", b"\xf3"),
             "Yükleme sırasında bir hata ol"),
            (loader_item(176, 17134, "ing.", b"\x6f\x00\x0a", b"\xf3"),
             "du. "),
        ]

    def test_matching_widths_pass(self):
        verify_fixed_loader_widths("GAME1.DPK", self.items)

    def test_loader_growth_fails_before_repack(self):
        items = list(self.items)
        items[0] = (items[0][0], "Dosya yüklendi.")
        with self.assertRaisesRegex(ValueError, "must stay"):
            verify_fixed_loader_widths("GAME1.DPK", items)

    def test_missing_loader_span_fails(self):
        with self.assertRaisesRegex(ValueError, "catalog is incomplete"):
            verify_fixed_loader_widths("GAME1.DPK", self.items[:-1])

    def test_game2_fragment_growth_is_also_guarded(self):
        rows = [
            (loader_item(12430, 17061, "File loaded.", b"\x6f\x00\x01", b"\xf3"),
             "File loaded."),
            (loader_item(12431, 17078, "An error occurred during\nloa",
                         b"\x6f\x00\x01", b"\xf3"),
             "An error occurred during\nloa"),
            (loader_item(12432, 17134, "ding.", b"\x6f\x00\x01", b"\xf3" * 5),
             "ding."),
        ]
        verify_fixed_loader_widths("GAME2.DPK", rows)
        rows[0] = (rows[0][0], "Dosya yüklendi.")
        with self.assertRaisesRegex(ValueError, "must stay"):
            verify_fixed_loader_widths("GAME2.DPK", rows)


if __name__ == "__main__":
    unittest.main()
