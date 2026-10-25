"""Checksum-pinned, asset-free StarCraft: Brood War Devourer mobility patch.

Works on user-extracted 1.16.1 arr/units.dat and arr/flingy.dat. Does not
modify a game installation, make an MPQ, or alter OpenBW/Pluto binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

FLINGY_ENTRIES = 209
UNIT_ENTRIES = 228
FLINGY_SIZE = FLINGY_ENTRIES * (2 + 4 + 2 + 4 + 1 + 1 + 1)
SPEED_OFFSET = FLINGY_ENTRIES * 2
DEVOURER_UNIT_ID = 62
BASE_SPEED_RAW = 5 * 256
VARIANTS = {"control": BASE_SPEED_RAW, "devourer_speed_550": int(5.5 * 256)}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_hash(value: str, what: str) -> str:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError(f"{what}: expected 64-character sha256")
    return value.lower()


def inspect(units: bytes, flingy: bytes) -> dict:
    if len(units) < UNIT_ENTRIES:
        raise ValueError("units.dat is shorter than the 228-entry graphics table")
    if len(flingy) != FLINGY_SIZE:
        raise ValueError(f"flingy.dat must be legacy {FLINGY_SIZE} bytes, got {len(flingy)}")
    flingy_id = units[DEVOURER_UNIT_ID]
    if flingy_id >= FLINGY_ENTRIES:
        raise ValueError(f"Devourer graphics index {flingy_id} is outside flingy.dat")
    aliases = [i for i in range(UNIT_ENTRIES) if i != DEVOURER_UNIT_ID and units[i] == flingy_id]
    field_offset = SPEED_OFFSET + 4 * flingy_id
    speed_raw = struct.unpack_from("<I", flingy, field_offset)[0]
    return {
        "units_sha256": digest(units), "flingy_sha256": digest(flingy),
        "devourer_unit_id": DEVOURER_UNIT_ID, "flingy_id": flingy_id,
        "shared_unit_ids": aliases, "speed_field_offset": field_offset,
        "speed_raw": speed_raw, "speed_px_per_frame": speed_raw / 256,
    }


def patch(units: bytes, flingy: bytes, *, variant: str,
          expected_units_sha256: str, expected_flingy_sha256: str) -> tuple[bytes, dict]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; supported: {sorted(VARIANTS)}")
    details = inspect(units, flingy)
    for key, expected in (("units_sha256", expected_units_sha256),
                          ("flingy_sha256", expected_flingy_sha256)):
        if details[key] != require_hash(expected, key):
            raise ValueError(f"{key} mismatch: assets have changed")
    if details["speed_raw"] != BASE_SPEED_RAW:
        raise ValueError(f"expected unmodified Devourer speed {BASE_SPEED_RAW}; found {details['speed_raw']}")
    if details["shared_unit_ids"]:
        raise ValueError(f"Devourer shares Flingy entry with units {details['shared_unit_ids']}; refusing collateral edits")
    out = bytearray(flingy)
    offset = details["speed_field_offset"]
    struct.pack_into("<I", out, offset, VARIANTS[variant])
    changed = [i for i, (old, new) in enumerate(zip(flingy, out)) if old != new]
    if any(not (offset <= i < offset + 4) for i in changed):
        raise AssertionError("patch touched an unrelated field")
    receipt = {**details, "schema": 1, "variant": variant,
               "new_speed_raw": VARIANTS[variant],
               "changed_byte_offsets": changed, "output_sha256": digest(out),
               "game_execution_verified": False}
    return bytes(out), receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    view = sub.add_parser("inspect", help="inspect original, local data and print hashes")
    edit = sub.add_parser("patch", help="write new flingy.dat and JSON receipt; originals untouched")
    for p in (view, edit):
        p.add_argument("--units", type=Path, required=True)
        p.add_argument("--flingy", type=Path, required=True)
    edit.add_argument("--variant", required=True, choices=sorted(VARIANTS))
    edit.add_argument("--expect-units-sha256", required=True)
    edit.add_argument("--expect-flingy-sha256", required=True)
    edit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    units, flingy = args.units.read_bytes(), args.flingy.read_bytes()
    if args.command == "inspect":
        print(json.dumps(inspect(units, flingy), indent=2, sort_keys=True))
        return 0
    if args.output.resolve() in (args.units.resolve(), args.flingy.resolve()):
        parser.error("output must be a new path outside the original inputs")
    data, receipt = patch(units, flingy, variant=args.variant,
                          expected_units_sha256=args.expect_units_sha256,
                          expected_flingy_sha256=args.expect_flingy_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output.with_name(args.output.name + ".receipt.json")
    if args.output.exists() or receipt_path.exists():
        parser.error("output or receipt already exists (refusing overwrite)")
    with args.output.open("xb") as out:
        out.write(data)
    with receipt_path.open("x", encoding="utf-8") as out:
        json.dump(receipt, out, indent=2, sort_keys=True)
        out.write("\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
