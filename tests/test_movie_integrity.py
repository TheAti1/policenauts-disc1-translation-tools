import struct
import unittest

from policenauts_movie_integrity import (
    changed_outside_dialogue, movie_records, validate_dialogue_slot,
)


def sample_movie():
    data = bytearray(0x800 + 0x40000)
    data[:4] = b"VMNK"
    chunk = 0x800
    data[chunk + 8:chunk + 12] = b"KLBS"
    struct.pack_into("<II", data, chunk + 0x10, 2, 2)
    first = chunk + 0x20
    data[first + 8:first + 16] = b"SCIPPDTS"
    struct.pack_into("<II", data, first + 0x18, 0x80, 4)
    data[chunk + 0x80:chunk + 0x84] = b"ABCD"
    second = first + 0x30
    data[second + 8:second + 16] = b"SCTEGOLD"
    struct.pack_into("<II", data, second + 0x18, 0x84, 6)
    data[chunk + 0x84:chunk + 0x8a] = b"Hello\0"
    return bytes(data)


class MovieIntegrityTests(unittest.TestCase):
    def test_video_boundary_cannot_be_replaced_by_prefix_space(self):
        source = sample_movie()
        records = movie_records(source)
        changed = bytearray(source)
        changed[0x800 + 0x83] = ord(" ")
        changed[0x800 + 0x84] = ord("M")
        self.assertEqual(changed_outside_dialogue(source, changed, records),
                         [(0x800 + 0x83, ord("D"), ord(" "), b"SCIPPDTS")])
        with self.assertRaisesRegex(ValueError, "non-dialogue"):
            validate_dialogue_slot(source, records, 0x800 + 0x83,
                                   source[0x800 + 0x83:0x800 + 0x86],
                                   bytes(changed[0x800 + 0x83:0x800 + 0x86]))

    def test_dialogue_only_edit_is_valid(self):
        source = sample_movie()
        records = movie_records(source)
        validate_dialogue_slot(source, records, 0x800 + 0x84,
                               b"Hello\0", b"Selam\0")


if __name__ == "__main__":
    unittest.main()
