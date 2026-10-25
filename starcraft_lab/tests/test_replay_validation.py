import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from starcraft_lab import replay_validation, telemetry


def manifest(**changes):
    m = dict(schema=1, run_id="r1", pair_id="p1", variant="control",
             engine="starcraft_1161", agent="pluto", agent_model="md09b_1331000",
             agent_binary_sha256="a"*64, engine_sha256="b"*64,
             map_sha256="c"*64, mod_sha256="d"*64, opponent="Opponent",
             player_race="Zerg", opponent_race="Zerg", agent_player_id=0,
             spawn="A", seed=7, bandit_initial_sha256="e"*64,
             result="win", result_evidence="replay",
             result_evidence_sha256=hashlib.sha256(b"repdata").hexdigest())
    m.update(changes)
    return m


def report(m, **overrides):
    def units(devourer=0):
        return {name: {
            "issued_orders": devourer if name == "devourer" else 0,
            "model_estimated_starts": devourer if name == "devourer" else 0,
            "first_order_frame": 4000 if devourer and name == "devourer" else None,
            "first_model_estimated_start_frame": 4020 if devourer and name == "devourer" else None,
        } for name in replay_validation.SUPPORTED_UNITS}
    r = dict(schema=1, producer=replay_validation.EXPECTED_PRODUCER,
             replay_sha256=m["result_evidence_sha256"], chk_sha256="f"*64,
             map_name="Fighting Spirit", frame_count=9000, engine_type="BroodWar",
             command_warnings={"clean": True, "unknown_commands": 0, "truncated_commands": 0},
             winner_player_id=0,
             players=[{"player_id":0,"race":"Zerg","name":"Pluto","unit_orders":units(2)},
                      {"player_id":1,"race":"Zerg","name":"Opponent","unit_orders":units()}])
    r.update(overrides)
    return r


class ReplayValidationTests(unittest.TestCase):
    def test_valid_report_orders_and_stats(self):
        m=manifest()
        observation, errors = replay_validation.report_observations(report(m),m)
        self.assertEqual(errors,[])
        self.assertEqual(observation["unit_orders"]["devourer"]["issued_orders"],2)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"manifest.json").write_text(json.dumps(m))
            (root/"replay.rep").write_bytes(b"repdata")
            (root/"parsed.json").write_text(json.dumps(report(m)))
            (root/"bandit.txt").write_text('{"type":"start"}\n')
            (root/"records").mkdir()
            r=telemetry.collect(root/"manifest.json",root/"bandit.txt",root/"records"/"game.json",
                                root/"replay.rep",root/"parsed.json")
            self.assertTrue(r["replay_probe_consistent"])
            summary=telemetry.analyze(root/"records")
            d=summary["order_observations"]["control"]["devourer"]
            self.assertEqual(d["adoption_fraction"],1.0)
            self.assertEqual(d["median_first_order_frame_when_used"],4000)
    def test_mismatch_sha(self):
        m=manifest()
        _,e=replay_validation.report_observations(report(m,replay_sha256="0"*64),m)
        self.assertIn("replay_report_hash_mismatch",e)
    def test_mismatch_winner(self):
        m=manifest()
        _,e=replay_validation.report_observations(report(m,winner_player_id=1),m)
        self.assertIn("replay_reported_outcome_mismatch",e)
    def test_partial_decode_and_race_mismatch(self):
        m=manifest()
        r=report(m,command_warnings={"clean":False})
        r["players"][0]["race"]="Terran"
        _,e=replay_validation.report_observations(r,m)
        self.assertIn("replay_commands_incomplete",e)
        self.assertIn("replay_race_mismatch",e)
    def test_missing_player_and_unknown_winner(self):
        m=manifest()
        _,e=replay_validation.report_observations(report(m,winner_player_id=None),m)
        self.assertIn("replay_winner_unknown",e)
        _,e=replay_validation.report_observations(report(m),manifest(agent_player_id=7))
        self.assertIn("agent_player_id_missing_or_invalid",e)
    def test_builtin_computer_opponent_player_id_255(self):
        m = manifest()
        r = report(m)
        r["players"][1]["player_id"] = 255
        obs, errors = replay_validation.report_observations(r, m)
        self.assertEqual(errors, [])
        self.assertEqual(obs["agent_player_id"], 0)
        bad = manifest(agent_player_id=255)
        _, errors = replay_validation.report_observations(r, bad)
        self.assertIn("agent_player_id_missing_or_invalid", errors)
    def test_reject_boolean_count(self):
        m=manifest()
        r=report(m)
        r["players"][0]["unit_orders"]["devourer"]["issued_orders"]=True
        _,e=replay_validation.report_observations(r,m)
        self.assertIn("malformed_devourer_orders",e)
    def test_bad_report_excluded(self):
        m=manifest()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"manifest.json").write_text(json.dumps(m))
            (root/"replay.rep").write_bytes(b"repdata")
            (root/"parsed.json").write_text(json.dumps(report(m,winner_player_id=1)))
            (root/"bandit.txt").write_text('{"type":"start"}\n')
            (root/"records").mkdir()
            r=telemetry.collect(root/"manifest.json",root/"bandit.txt",root/"records"/"game.json",
                                root/"replay.rep",root/"parsed.json")
            self.assertFalse(r["replay_probe_consistent"])
            self.assertFalse(telemetry.artifact_backed(r))
            summary=telemetry.analyze(root/"records")
            self.assertIsNone(summary["variants"]["control"]["reported_win_rate_decisive"])
            self.assertEqual(summary["order_observations"],{})


if __name__ == "__main__":
    unittest.main()
