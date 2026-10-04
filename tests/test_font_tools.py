import tempfile
import unittest
from pathlib import Path

from PIL import ImageFont

from policenauts_font_tools import (
    TURKISH,
    atlas_size,
    cell_box,
    checked_edits,
    decode_cell,
    encode_cell,
    font_atlas,
    render_turkish,
    sha256,
)


class FontToolsTests(unittest.TestCase):
    def test_2bpp_cell_roundtrip(self):
        raw = bytes(range(36))
        self.assertEqual(encode_cell(decode_cell(raw)), raw)

    def test_turkish_glyphs_are_editable_2bpp_cells(self):
        font_path = Path("C:/Windows/Fonts/arialbd.ttf")
        if not font_path.is_file():
            self.skipTest("Arial Bold is not installed on this platform")
        font = ImageFont.truetype(str(font_path), 12)
        for char in TURKISH:
            image = render_turkish(char, font)
            self.assertEqual(image.size, (12, 12))
            self.assertTrue(any(image.getdata()), char)
            self.assertEqual(decode_cell(encode_cell(image)).tobytes(), image.tobytes())

    def test_unapproved_atlas_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            export = root / "export"
            edit = root / "edit"
            export.mkdir()
            edit.mkdir()
            raw = bytes(72)
            item = {
                "raw": "test.raw", "image": "test.png", "length": len(raw),
                "sha256": sha256(raw), "fixed_start": 0, "count": 2,
            }
            (export / "test.raw").write_bytes(raw)
            original = font_atlas(raw, 0, 2)
            self.assertEqual(original.size, atlas_size(2))
            original.save(export / "test.png")
            changed = original.copy()
            changed.putpixel((cell_box(1)[0], 0), 255)
            changed.save(edit / "test.png")
            accepted = checked_edits(export, edit, {"files": [item]}, {1})
            self.assertEqual(set(accepted[0][1]), {1})
            with self.assertRaisesRegex(ValueError, "Unapproved font cell"):
                checked_edits(export, edit, {"files": [item]}, {0})
            changed.putpixel((0, 0), 255)
            changed.save(edit / "test.png")
            with self.assertRaisesRegex(ValueError, "Unapproved font cell"):
                checked_edits(export, edit, {"files": [item]}, {1})


if __name__ == "__main__":
    unittest.main()
