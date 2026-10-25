import json
import struct
import tempfile
import unittest
from pathlib import Path
from starcraft_lab import mod, preflight


def pe(machine):
    hdr=bytearray(128)
    hdr[0:2]=b"MZ"
    struct.pack_into("<I",hdr,0x3C,128)
    return bytes(hdr)+b"PE\0\0"+struct.pack("<H",machine)+b"\0"*32


def fixture(root):
    game=root/"game"
    (game/"bwapi-data"/"AI"/"pluto").mkdir(parents=True)
    (game/"StarCraft.exe").write_bytes(pe(0x14c))
    (game/"bwapi-data"/"AI"/"pluto.dll").write_bytes(pe(0x14c))
    (game/"bwapi-data"/"AI"/"pluto"/"pluto_infer.exe").write_bytes(pe(0x8664))
    (game/"bwapi-data"/"AI"/"pluto"/"pluto_weights.bin").write_bytes(b"weights")
    (game/"bwapi-data"/"bwapi.ini").write_text("[ai]\nai = bwapi-data/AI/pluto.dll\n")
    for name in ("StarDat.mpq","BrooDat.mpq","Patch_rt.mpq"):
        (game/name).write_bytes(b"dummy MPQ")
    units=bytearray(20000)
    units[mod.DEVOURER_UNIT_ID]=147
    flingy=bytearray(mod.FLINGY_SIZE)
    struct.pack_into("<I",flingy,mod.SPEED_OFFSET+4*147,mod.BASE_SPEED_RAW)
    uf,orig,staged=root/"units.dat",root/"original_flingy.dat",root/"staged_flingy.dat"
    uf.write_bytes(units)
    orig.write_bytes(flingy)
    patch,receipt=mod.patch(bytes(units),bytes(flingy),variant="devourer_speed_550",
                            expected_units_sha256=mod.digest(units),
                            expected_flingy_sha256=mod.digest(flingy))
    staged.write_bytes(patch)
    receipt_file=root/"receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    return game,uf,orig,staged,receipt_file


class PreflightTests(unittest.TestCase):
    def test_static_checks_do_not_claim_gameplay(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=preflight.inspect_install(*fixture(Path(tmp)))
            self.assertEqual(result["failures"],[])
            self.assertEqual(result["status"],"static_checks_only")
            self.assertFalse(result["engine_mod_execution_proven"])
            self.assertIn("mpqdraft_bwapi_coexistence_unconfirmed",result["warnings"])
    def test_architecture_and_ini_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=fixture(Path(tmp))
            game=paths[0]
            (game/"bwapi-data"/"AI"/"pluto.dll").write_bytes(pe(0x8664))
            (game/"bwapi-data"/"bwapi.ini").write_text("ai = suspicious.dll\n")
            result=preflight.inspect_install(*paths)
            self.assertIn("wrong_pe_machine_pluto_dll",result["failures"])
            self.assertIn("bwapi_ini_ai_path_ambiguous_or_wrong",result["failures"])
    def test_packaged_bytes_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=fixture(Path(tmp))
            mpq=Path(tmp)/"packed_flingy.dat"
            mpq.write_bytes(b"wrong")
            result=preflight.inspect_install(*paths,mpq)
            self.assertIn("mpq_extracted_mod_hash_mismatch",result["failures"])
            paths[3].write_bytes(b"corrupt")
            result=preflight.inspect_install(*paths)
            self.assertIn("staged_data_not_expected_patch",result["failures"])
    def test_mpqdraft_bwapi_plugin_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            paths=fixture(root)
            plugin=root/"BWAPI_PluginInjector.qdp"
            plugin.write_bytes(pe(0x14c))
            result=preflight.inspect_install(*paths, mpqdraft_bwapi_plugin=plugin)
            self.assertEqual(result["failures"], [])
            self.assertEqual(result["checks"]["bwapi_mpqdraft_plugin"]["pe_machine"], "0x014c")
            plugin.write_bytes(pe(0x8664))
            result=preflight.inspect_install(*paths, mpqdraft_bwapi_plugin=plugin)
            self.assertIn("bwapi_mpqdraft_plugin_not_x86",result["failures"])
            plugin.unlink()
            result=preflight.inspect_install(*paths, mpqdraft_bwapi_plugin=plugin)
            self.assertIn("bwapi_mpqdraft_plugin_missing",result["failures"])
    def test_missing_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=fixture(Path(tmp))
            (paths[0]/"StarCraft.exe").unlink()
            self.assertIn("missing_starcraft_exe",preflight.inspect_install(*paths)["failures"])


if __name__=="__main__":
    unittest.main()
