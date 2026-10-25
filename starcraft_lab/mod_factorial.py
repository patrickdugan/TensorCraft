"""Independent factorial Devourer mods for *owned* legacy Brood War DAT files.

Uses the OpenBW data_loading.h column layout. Reuses v1 movement patch
unchanged; adds the Devourer air-weapon cooldown in weapons.dat.
A 90% cooldown is rounded to the nearest integral frame (half up).
Outputs staged files, never touches originals or game installations.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from . import mod

N_UNITS = 228
N_WEAPONS = 130
# units.dat is columnar, with a 96-entry infestation field.
BEFORE_AIR_WEAPON = (
    (228, 1), (228, 2), (228, 2), (96, 2), (228, 4),
    (228, 1), (228, 1), (228, 2), (228, 4),
    *[(228, 1)] * 10,  # elevation, unknown, sublabel, 5 orders, ground weapon and hits
)
AIR_WEAPON_OFFSET = sum(count * width for count, width in BEFORE_AIR_WEAPON)
# weapons.dat columns up through damage_bonus, before one-byte cooldown.
WEAPON_PRE_COOLDOWN_WIDTHS = (
    2, 4, 1, 2, 4, 4, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2
)
COOLDOWN_OFFSET = N_WEAPONS * sum(WEAPON_PRE_COOLDOWN_WIDTHS)
WEAPON_SIZE = N_WEAPONS * (sum(WEAPON_PRE_COOLDOWN_WIDTHS) + 1 + 1 + 1 + 1 + 1 + 1 + 2 + 2)
# Last widths: cooldown, bullet_count, attack_angle, heading, forward,
# upward, target_error_message, icon.
SPEED_RAW = mod.BASE_SPEED_RAW
FAST_SPEED_RAW = int(5.50 * 256)
VARIANTS = {
    "control": (SPEED_RAW, False),
    "devourer_speed_550": (FAST_SPEED_RAW, False),
    "devourer_cooldown_90": (SPEED_RAW, True),
    "devourer_speed_550_cooldown_90": (FAST_SPEED_RAW, True),
}


def inspect(units: bytes, flingy: bytes, weapons: bytes) -> dict:
    base = mod.inspect(units, flingy)
    if len(weapons) != WEAPON_SIZE:
        raise ValueError(f"weapons.dat must have {WEAPON_SIZE} bytes; got {len(weapons)}")
    if len(units) <= AIR_WEAPON_OFFSET + mod.DEVOURER_UNIT_ID:
        raise ValueError("units.dat lacks air weapon column")
    weapon_id = units[AIR_WEAPON_OFFSET + mod.DEVOURER_UNIT_ID]
    if weapon_id >= N_WEAPONS:
        raise ValueError("Devourer air weapon out of bounds")
    # Confirm no other unit refers to the same weapon. Sharing would silently
    # buff a unit outside the experimental treatment.
    aliases = [i for i in range(N_UNITS)
               if i != mod.DEVOURER_UNIT_ID and units[AIR_WEAPON_OFFSET + i] == weapon_id]
    offset = COOLDOWN_OFFSET + weapon_id
    return {**base, "weapons_sha256": mod.digest(weapons),
            "air_weapon_id": weapon_id, "air_weapon_alias_unit_ids": aliases,
            "cooldown_field_offset": offset, "cooldown_frames": weapons[offset]}


def patch(units: bytes, flingy: bytes, weapons: bytes, *, variant: str,
          expected_units_sha256: str, expected_flingy_sha256: str,
          expected_weapons_sha256: str) -> tuple[bytes, bytes, dict]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    data = inspect(units, flingy, weapons)
    for key, value in (("units_sha256", expected_units_sha256),
                       ("flingy_sha256", expected_flingy_sha256),
                       ("weapons_sha256", expected_weapons_sha256)):
        if data[key] != mod.require_hash(value, key):
            raise ValueError(f"{key} mismatch")
    if data["air_weapon_alias_unit_ids"]:
        raise ValueError(f"shared Devourer air weapon: {data['air_weapon_alias_unit_ids']}")
    baseline = data["cooldown_frames"]
    if not 1 <= baseline <= 255:
        raise ValueError("invalid Devourer weapon cooldown")
    new_cooldown = (9 * baseline + 5) // 10
    if new_cooldown < 1:
        raise ValueError("zero cooldown")
    speed, accelerated = VARIANTS[variant]
    # Reuse the established single-field speed patch and its fail-closed
    # checks for original top speed and shared Flingy entries.
    speed_variant = "devourer_speed_550" if speed == FAST_SPEED_RAW else "control"
    new_flingy, speed_receipt = mod.patch(
        units, flingy, variant=speed_variant,
        expected_units_sha256=expected_units_sha256,
        expected_flingy_sha256=expected_flingy_sha256)
    new_weapons = bytearray(weapons)
    if accelerated:
        new_weapons[data["cooldown_field_offset"]] = new_cooldown
    modified_weapons = bytes(new_weapons)
    differences = [i for i, (a, b) in enumerate(zip(weapons, modified_weapons)) if a != b]
    if any(i != data["cooldown_field_offset"] for i in differences):
        raise AssertionError("unrelated weapons.dat byte changed")
    receipt = {
        **data, "schema": 2, "variant": variant,
        "new_speed_raw": speed, "new_cooldown_frames": new_cooldown if accelerated else baseline,
        "requested_cooldown_fraction": "9/10" if accelerated else "1/1",
        "actual_cooldown_fraction": (f"{new_cooldown}/{baseline}" if accelerated else "1/1"),
        "flingy_changed_byte_offsets": speed_receipt["changed_byte_offsets"],
        "weapons_changed_byte_offsets": differences,
        "output_flingy_sha256": mod.digest(new_flingy),
        "output_weapons_sha256": mod.digest(modified_weapons),
        "game_execution_verified": False,
    }
    return new_flingy, modified_weapons, receipt


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--units", type=Path, required=True)
    p.add_argument("--flingy", type=Path, required=True)
    p.add_argument("--weapons", type=Path, required=True)
    p.add_argument("--variant", choices=sorted(VARIANTS), required=True)
    p.add_argument("--expect-units-sha256", required=True)
    p.add_argument("--expect-flingy-sha256", required=True)
    p.add_argument("--expect-weapons-sha256", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args(argv)
    inputs = [args.units.resolve(), args.flingy.resolve(), args.weapons.resolve()]
    outdir = args.output_dir.resolve()
    if any(outdir == src.parent or outdir in src.parents for src in inputs):
        p.error("output directory would contain or overwrite a source asset")
    files = [path.read_bytes() for path in (args.units, args.flingy, args.weapons)]
    result_flingy, result_weapons, receipt = patch(
        *files, variant=args.variant,
        expected_units_sha256=args.expect_units_sha256,
        expected_flingy_sha256=args.expect_flingy_sha256,
        expected_weapons_sha256=args.expect_weapons_sha256)
    outdir.mkdir(parents=True, exist_ok=False)
    (outdir / "flingy.dat").write_bytes(result_flingy)
    (outdir / "weapons.dat").write_bytes(result_weapons)
    (outdir / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
