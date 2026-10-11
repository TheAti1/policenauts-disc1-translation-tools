import struct
import unittest

from audit_policenauts_voice_toc import scan_sector
from policenauts_disc_tools import SECTOR_RAW


class VoiceTocAuditTests(unittest.TestCase):
    def test_header_pointer_finds_text_and_blank_record(self):
        sector = bytearray(SECTOR_RAW)
        sector[24:28] = b'"jXS'
        struct.pack_into("<I", sector, 64, 96)
        struct.pack_into("<IIII", sector, 120, 20, 2, 8, 0)
        sector[136:140] = b"  \0\0"
        struct.pack_into("<IIII", sector, 140, 0, 12, 6, 0)
        sector[156:160] = b"Hi!\0"
        rows, problem = scan_sector(bytes(sector))
        self.assertIsNone(problem)
        self.assertEqual([row["raw"] for row in rows], [b"  ", b"Hi!"])

    def test_unterminated_record_is_reported(self):
        sector = bytearray(SECTOR_RAW)
        sector[24:28] = b'"jXS'
        struct.pack_into("<I", sector, 64, 96)
        struct.pack_into("<IIII", sector, 120, 0, 2, 8, 0)
        sector[136:2072] = b"X" * (2072 - 136)
        rows, problem = scan_sector(bytes(sector))
        self.assertEqual(rows, [])
        self.assertEqual(problem["kind"], "unterminated_record")

    def test_header_without_room_for_text_is_rejected(self):
        sector = bytearray(SECTOR_RAW)
        sector[24:28] = b'"jXS'
        struct.pack_into("<I", sector, 64, 2056 - 24)
        rows, problem = scan_sector(bytes(sector))
        self.assertEqual(rows, [])
        self.assertEqual(problem["kind"], "invalid_header_position")


if __name__ == "__main__":
    unittest.main()
