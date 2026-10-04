#!/usr/bin/env python3
"""Small PSX Mode2/Form1 and DPK helpers used by the build script.

No game data is included here. The caller must provide their own legally-owned
source image and any private metadata generated from it.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

SECTOR_USER = 2048
SECTOR_RAW = 2352
RAW_USERDATA_OFFSET = 24
MODE2_FORM1_EDC_OFFSET = 2072

EDC_POLY = 0xD8018001
EDC_TABLE: list[int] = []
for _edc_i in range(256):
    _crc = _edc_i
    for _ in range(8):
        if _crc & 1:
            _crc = (_crc >> 1) ^ EDC_POLY
        else:
            _crc >>= 1
    EDC_TABLE.append(_crc & 0xFFFFFFFF)

ECC_F_LUT: list[int] = [0] * 256
ECC_B_LUT: list[int] = [0] * 256
for _ecc_i in range(256):
    _ecc_j = ((_ecc_i << 1) ^ (0x11D if (_ecc_i & 0x80) else 0)) & 0xFF
    ECC_F_LUT[_ecc_i] = _ecc_j
    ECC_B_LUT[_ecc_i ^ _ecc_j] = _ecc_i


def compute_cdrom_edc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc >> 8) ^ EDC_TABLE[(crc ^ byte) & 0xFF]) & 0xFFFFFFFF
    return crc


def compute_crc32_bzip2(data: bytes) -> int:
    crc = 0xFFFFFFFF
    poly = 0x04C11DB7
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ poly) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


def compute_cdrom_ecc(
    address: bytes,
    data: bytes,
    major_count: int,
    minor_count: int,
    major_mult: int,
    minor_inc: int,
) -> bytes:
    size = major_count * minor_count
    ecc = [0] * (major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _minor in range(minor_count):
            temp = address[index] if index < 4 else data[index - 4]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ECC_F_LUT[ecc_a]
        ecc_a = ECC_B_LUT[ECC_F_LUT[ecc_a] ^ ecc_b]
        ecc[major] = ecc_a
        ecc[major + major_count] = ecc_a ^ ecc_b
    return bytes(ecc)


def compute_mode2_form1_ecc(sector: bytes) -> tuple[bytes, bytes]:
    # XA Mode2/Form1 ECC uses a zeroed address field.
    address = b"\0\0\0\0"
    p_ecc = compute_cdrom_ecc(address, sector[16:2248], 86, 24, 2, 86)
    data_for_q = bytearray(sector[16:2248])
    data_for_q[2060 : 2060 + len(p_ecc)] = p_ecc
    q_ecc = compute_cdrom_ecc(address, bytes(data_for_q), 52, 43, 86, 88)
    return p_ecc, q_ecc


def fix_mode2_form1_edc_ecc_for_sectors(fp, sectors: set[int]) -> tuple[int, int]:
    fixed_edc = 0
    fixed_ecc = 0
    for sector in sorted(sectors):
        base = sector * SECTOR_RAW
        fp.seek(base)
        data = bytearray(fp.read(SECTOR_RAW))
        if len(data) != SECTOR_RAW:
            continue
        if data[0:12] != b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00":
            continue
        if data[15] != 2:
            continue
        # Mode2/Form1 has bit 0x20 clear in the subheader form byte.
        if data[18] & 0x20:
            continue

        new_edc = compute_cdrom_edc(bytes(data[16:2072]))
        old_edc = struct.unpack_from("<I", data, MODE2_FORM1_EDC_OFFSET)[0]
        if old_edc != new_edc:
            struct.pack_into("<I", data, MODE2_FORM1_EDC_OFFSET, new_edc)
            fixed_edc += 1

        new_p, new_q = compute_mode2_form1_ecc(bytes(data))
        sector_fixed_ecc = False
        if bytes(data[2076:2248]) != new_p or bytes(data[2248:2352]) != new_q:
            data[2076:2248] = new_p
            data[2248:2352] = new_q
            fixed_ecc += 1
            sector_fixed_ecc = True

        if old_edc != new_edc or sector_fixed_ecc:
            fp.seek(base)
            fp.write(data)
    return fixed_edc, fixed_ecc


def raw_offset_from_user_offset(user_offset: int) -> int:
    sector = user_offset // SECTOR_USER
    inner = user_offset % SECTOR_USER
    return sector * SECTOR_RAW + RAW_USERDATA_OFFSET + inner


def write_user_bytes_to_raw(fp, user_offset: int, data: bytes) -> set[int]:
    sectors: set[int] = set()
    remaining = len(data)
    current = user_offset
    written = 0
    while remaining > 0:
        sector = current // SECTOR_USER
        inner = current % SECTOR_USER
        chunk_size = min(remaining, SECTOR_USER - inner)
        raw_offset = sector * SECTOR_RAW + RAW_USERDATA_OFFSET + inner
        fp.seek(raw_offset)
        fp.write(data[written : written + chunk_size])
        sectors.add(sector)
        written += chunk_size
        remaining -= chunk_size
        current += chunk_size
    return sectors


def read_user_bytes_from_raw(fp, user_offset: int, byte_count: int) -> bytes:
    out = bytearray()
    remaining = byte_count
    current = user_offset
    while remaining > 0:
        sector = current // SECTOR_USER
        inner = current % SECTOR_USER
        chunk_size = min(remaining, SECTOR_USER - inner)
        raw_offset = sector * SECTOR_RAW + RAW_USERDATA_OFFSET + inner
        fp.seek(raw_offset)
        out.extend(fp.read(chunk_size))
        remaining -= chunk_size
        current += chunk_size
    return bytes(out)


def parse_dpk_records_from_file(path: Path) -> tuple[int, int, list[dict[str, Any]]]:
    data = path.read_bytes()
    if data[:4] != b"FRID":
        raise ValueError(f"{path} is not a FRID/DPK file")
    count = struct.unpack_from("<I", data, 0x0C)[0]
    rec_size = struct.unpack_from("<I", data, 0x14)[0]
    records: list[dict[str, Any]] = []
    for i in range(count):
        rec_off = 0x20 + i * rec_size
        rec = data[rec_off : rec_off + rec_size]
        name = rec[:12].split(b"\0", 1)[0].decode("ascii", errors="ignore")
        data_off = struct.unpack_from("<I", rec, 12)[0]
        data_size = struct.unpack_from("<I", rec, 16)[0]
        checksum = struct.unpack_from("<I", rec, 20)[0]
        records.append(
            {
                "index": i,
                "name": name,
                "record_offset": rec_off,
                "data_offset": data_off,
                "data_size": data_size,
                "checksum": checksum,
            }
        )
    return count, rec_size, records


def _iso_records(fp, extent: int, size: int) -> dict[str, tuple[int, int]]:
    data = read_user_bytes_from_raw(fp, extent * SECTOR_USER, size)
    out: dict[str, tuple[int, int]] = {}
    pos = 0
    while pos < len(data):
        length = data[pos]
        if not length:
            pos = ((pos // SECTOR_USER) + 1) * SECTOR_USER
            continue
        rec = data[pos : pos + length]
        if len(rec) < 34:
            raise ValueError("Invalid ISO directory record")
        name = rec[33 : 33 + rec[32]].decode("ascii", errors="ignore").split(";")[0]
        if name and name not in ("\x00", "\x01"):
            out[name] = (struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0])
        pos += length
    return out


def iso_nauts_files(fp) -> dict[str, tuple[int, int]]:
    pvd = read_user_bytes_from_raw(fp, 16 * SECTOR_USER, SECTOR_USER)
    if pvd[1:6] != b"CD001":
        raise ValueError("Source image has no ISO9660 primary volume descriptor")
    root = pvd[156:190]
    root_files = _iso_records(fp, struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0])
    if "NAUTS" not in root_files:
        raise ValueError("NAUTS directory not found")
    return _iso_records(fp, *root_files["NAUTS"])


def dpk_records_from_image(fp, base_user: int) -> list[dict[str, Any]]:
    header = read_user_bytes_from_raw(fp, base_user, 0x20)
    if header[:4] != b"FRID":
        raise ValueError("FRID/DPK header not found")
    count = struct.unpack_from("<I", header, 0x0C)[0]
    record_size = struct.unpack_from("<I", header, 0x14)[0]
    if count > 10000 or record_size < 24 or record_size > 256:
        raise ValueError("Invalid FRID/DPK table size")
    table = read_user_bytes_from_raw(fp, base_user + 0x20, count * record_size)
    records = []
    for index in range(count):
        offset = index * record_size
        rec = table[offset : offset + record_size]
        records.append({
            "index": index,
            "name": rec[:12].split(b"\0", 1)[0].decode("ascii", errors="strict"),
            "record_offset": 0x20 + offset,
            "data_offset": struct.unpack_from("<I", rec, 12)[0],
            "data_size": struct.unpack_from("<I", rec, 16)[0],
            "checksum": struct.unpack_from("<I", rec, 20)[0],
        })
    return records


def update_dpk_checksums_for_patched_entries(fp, results: list[dict[str, Any]]) -> tuple[int, set[int]]:
    """Update checksums using DPK tables read directly from the current image."""
    files = iso_nauts_files(fp)
    touched: dict[str, set[int]] = {}
    for item in results:
        name = str(item["container"])
        if name not in files:
            raise ValueError(f"DPK container not present in ISO: {name}")
        touched.setdefault(name, set()).add(int(item["container_entry_index"]))

    updated = 0
    sectors: set[int] = set()
    for name, indices in touched.items():
        extent, file_size = files[name]
        base_user = extent * SECTOR_USER
        records = dpk_records_from_image(fp, base_user)
        for index in sorted(indices):
            if index >= len(records):
                raise ValueError(f"Invalid {name} entry index: {index}")
            rec = records[index]
            if rec["data_offset"] + rec["data_size"] > file_size:
                raise ValueError(f"Invalid {name} entry bounds: {index}")
            data_user = base_user + rec["data_offset"]
            checksum_user = base_user + rec["record_offset"] + 20
            actual = compute_crc32_bzip2(read_user_bytes_from_raw(fp, data_user, rec["data_size"]))
            if actual != rec["checksum"]:
                sectors.update(write_user_bytes_to_raw(fp, checksum_user, struct.pack("<I", actual)))
                updated += 1
    return updated, sectors
