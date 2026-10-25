import struct
import unittest
from starcraft_lab import mod, mod_factorial as f


def fixtures(cooldown=50, alias=False):
    units = bytearray(25000)
    units[62] = 147
    units[f.AIR_WEAPON_OFFSET + 62] = 76
    if alias:
        units[f.AIR_WEAPON_OFFSET + 60] = 76
    flingy = bytearray(mod.FLINGY_SIZE)
    struct.pack_into("<I", flingy, mod.SPEED_OFFSET + 147 * 4, mod.BASE_SPEED_RAW)
    weapons = bytearray(f.WEAPON_SIZE)
    weapons[f.COOLDOWN_OFFSET + 76] = cooldown
    return bytes(units), bytes(flingy), bytes(weapons)


def run(variant, data=None):
    u, g, w = data or fixtures()
    return f.patch(u, g, w, variant=variant,
                   expected_units_sha256=mod.digest(u),
                   expected_flingy_sha256=mod.digest(g),
                   expected_weapons_sha256=mod.digest(w))


class FactorialTests(unittest.TestCase):
    def test_all_four_variants_are_separable(self):
        u, g, w = fixtures()
        c_g, c_w, _ = run("control")
        s_g, s_w, _ = run("devourer_speed_550")
        f_g, f_w, _ = run("devourer_cooldown_90")
        both_g, both_w, receipt = run("devourer_speed_550_cooldown_90")
        self.assertEqual((c_g, c_w), (g, w))
        self.assertEqual((s_w, f_g), (w, g))
        self.assertEqual((both_g, both_w), (s_g, f_w))
        self.assertEqual(receipt["new_speed_raw"], 1408)
        self.assertEqual(receipt["new_cooldown_frames"], 45)
        self.assertEqual(receipt["actual_cooldown_fraction"], "45/50")
        self.assertEqual(len(receipt["weapons_changed_byte_offsets"]), 1)
    def test_round_half_up(self):
        _, _, receipt = run("devourer_cooldown_90", fixtures(cooldown=55))
        self.assertEqual(receipt["new_cooldown_frames"], 50)
        self.assertEqual(receipt["actual_cooldown_fraction"], "50/55")
    def test_does_not_accept_shared_weapon(self):
        with self.assertRaisesRegex(ValueError, "shared Devourer air weapon"):
            run("devourer_cooldown_90", fixtures(alias=True))
    def test_does_not_accept_corrupt_original(self):
        u, g, w = fixtures()
        with self.assertRaisesRegex(ValueError, "weapons_sha256 mismatch"):
            f.patch(u, g, w, variant="devourer_cooldown_90",
                    expected_units_sha256=mod.digest(u),
                    expected_flingy_sha256=mod.digest(g),
                    expected_weapons_sha256="0"*64)
        with self.assertRaisesRegex(ValueError, "weapons.dat must have"):
            f.inspect(u, g, w[:-1])
    def test_refuse_zero_cooldown(self):
        with self.assertRaisesRegex(ValueError, "invalid Devourer weapon cooldown"):
            run("devourer_cooldown_90", fixtures(cooldown=0))


if __name__ == "__main__":
    unittest.main()
