import hashlib
import tempfile
import unittest
from pathlib import Path
from starcraft_lab.world_model import provenance


def data(raw):
    h1=hashlib.sha1(raw).hexdigest()
    h2=hashlib.sha256(raw).hexdigest()
    manifest={
        "run_id":"match_001","variant":"control","agent_player_id":0,
        "seed":1234,"spawn":"A","map_sha256":h2,
        "result":"win","result_evidence_sha256":"f"*64,
    }
    trace={
        "schema":1,"run_id":"match_001","variant":"control",
        "trace_sha256":"a"*64,"strict_calibration_eligible":True,
        "replay_mode":False, "self_player_id":0, "last_frame":1000,
        "observed_rng_seed":1234, "start_tile":[4,7], "map_sha1":h1,
    }
    replay={
        "schema":1,"producer":"starcraft-replay-probe/replay-core",
        "replay_sha256":"f"*64,"frame_count":1012,
        "players":[{"player_id":0},{"player_id":1}],
        "winner_player_id":0,
    }
    return manifest,trace,replay


class NativeProvenanceTests(unittest.TestCase):
    def test_trace_seed_and_map_join(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"map.scx"
            p.write_bytes(b"synthetic map payload")
            manifest,trace,replay=data(p.read_bytes())
            outcome=provenance.bind(manifest,trace,replay,p,{"A":[4,7],"B":[18,8]})
            self.assertEqual(outcome["problems"],[])
            self.assertEqual(outcome["warnings"],[])
            self.assertTrue(outcome["trace_replay_map_conditions_consistent"])
            self.assertFalse(outcome["mod_loaded_in_game_proven"])
            self.assertFalse(outcome["pluto_binary_identity_proven"])
            self.assertEqual(outcome["observed_rng_seed"],1234)
            self.assertEqual(outcome["observed_spawn_tile"],[4,7])
    def test_seed_spawn_map_mismatches(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"map.scx"
            p.write_bytes(b"original")
            m,t,r=data(p.read_bytes())
            t["observed_rng_seed"]=77
            t["start_tile"]=[3,7]
            p.write_bytes(b"tampered map")
            out=provenance.bind(m,t,r,p,{"A":[4,7]})
            self.assertIn("actual_rng_seed_differs_from_requested",out["problems"])
            self.assertIn("observed_start_tile_differs_from_planned_spawn",out["problems"])
            self.assertIn("map_file_sha256_mismatch",out["problems"])
            self.assertIn("bwapi_loaded_map_sha1_mismatch",out["problems"])
            self.assertFalse(out["trace_replay_map_conditions_consistent"])
    def test_missing_map_and_spawn_remains_unattested(self):
        m,t,r=data(b"input")
        out=provenance.bind(m,t,r)
        self.assertEqual(out["problems"],[])
        self.assertFalse(out["trace_replay_map_conditions_consistent"])
        self.assertIn("private_map_file_not_supplied_for_hash_comparison",out["warnings"])
        self.assertIn("spawn_tile_mapping_not_supplied",out["warnings"])
    def test_id_and_replay_binds_reject_forged_run(self):
        m,t,r=data(b"input")
        t["run_id"]="other_run"
        with self.assertRaises(provenance.TraceError):
            provenance.bind(m,t,r)
        t["run_id"]="match_001"
        r["replay_sha256"]="0"*64
        r["winner_player_id"]=1
        r["frame_count"]=4000
        out=provenance.bind(m,t,r)
        self.assertIn("replay_hash_does_not_match_manifest",out["problems"])
        self.assertIn("replay_first_leaver_vs_manifest_result_mismatch",out["problems"])
        self.assertIn("replay_trace_frame_extent_mismatch",out["problems"])
    def test_bad_spawn_mapping_is_rejected(self):
        m,t,r=data(b"input")
        with self.assertRaisesRegex(provenance.TraceError,"malformed expected spawn"):
            provenance.bind(m,t,r,spawn_tiles={"A":["four",7]})


if __name__=="__main__":
    unittest.main()
