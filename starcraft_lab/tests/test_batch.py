import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from starcraft_lab import batch, telemetry


def fixture(root: Path, pairs=30):
    control = root / "game-control"
    treatment = root / "game-treated"
    for game in (control, treatment):
        for path in batch.PINNED_FILES.values():
            f = game / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(("fake " + path).encode())
    c, t = root / "control.dat", root / "treatment.dat"
    c.write_bytes(b"original")
    t.write_bytes(b"original + speed")
    map_file, bandit = root / "map.scx", root / "snapshot.txt"
    map_file.write_bytes(b"private map")
    bandit.write_bytes(b"seq 100\nend\n")
    return dict(experiment_id="devourer_01", pairs=pairs, schedule_seed=77,
        map_file=str(map_file), initial_bandit_file=str(bandit),
        conditions={"control": {"game_dir": str(control), "mod_file": str(c)},
                    "devourer_speed_550": {"game_dir": str(treatment), "mod_file": str(t)}},
        agent_model="md09b_1331000", opponent="Steamhammer",
        player_race="Zerg", opponent_race="Zerg")


def artifact(game, result, has_devourer=False):
    m = copy.deepcopy(game["manifest"])
    m.update(result=result, result_evidence="replay", result_evidence_sha256="e"*64,
             agent_player_id=0)
    obs = {"unit_orders": {"devourer": {"issued_orders": int(has_devourer)}}}
    return {"schema":1, "manifest":m, "telemetry_errors": [],
            "evidence_hash_matches": True, "replay_probe_consistent":True,
            "replay_observations":obs}


class BatchTests(unittest.TestCase):
    def test_balances_spawn_and_order_even_at_30_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = fixture(root)
            plan, baseline = batch.build_plan(p)
            self.assertEqual(len(plan["games"]), 60)
            self.assertEqual(baseline, b"seq 100\nend\n")
            self.assertFalse(plan["seed_controlled"])
            self.assertFalse(plan["spawn_controlled"])
            self.assertEqual(plan, batch.build_plan(p)[0])
            for game in plan["games"]:
                telemetry.validate(game["manifest"])
                self.assertFalse(game["manifest"]["seed_applied_to_engine"])
            pairs = [plan["games"][n:n+2] for n in range(0, 60, 2)]
            self.assertEqual(sum(x[0]["manifest"]["spawn"] == "A" for x in pairs), 15)
            self.assertEqual(sum(x[0]["manifest"]["variant"] == "control" for x in pairs), 15)
            for a,b in pairs:
                self.assertEqual(a["manifest"]["pair_id"],b["manifest"]["pair_id"])
                self.assertNotEqual(a["manifest"]["variant"],b["manifest"]["variant"])
                self.assertEqual(a["manifest"]["seed"],b["manifest"]["seed"])
                self.assertEqual(a["manifest"]["bandit_initial_sha256"],b["manifest"]["bandit_initial_sha256"])
    def test_frozen_snapshot_and_private_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = fixture(root, 2)
            out = root / "experiment"
            plan = batch.save_plan(p, out)
            self.assertEqual((out / "bandit_baseline.txt").read_bytes(),b"seq 100\nend\n")
            self.assertEqual(len(list((out / "manifests").glob("*.json"))), 4)
            self.assertEqual(plan["source_map_sha256"],batch.checksum(root / "map.scx"))
            with self.assertRaises(FileExistsError):
                batch.save_plan(p, out)
            with self.assertRaises(ValueError):
                batch.save_plan(p, root/"game-control"/"bad")
    def test_rejects_mismatch_and_missing_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            p=fixture(root, 2)
            (root/"game-treated"/"StarCraft.exe").write_bytes(b"bad exe")
            with self.assertRaisesRegex(ValueError,"engine_sha256"):
                batch.build_plan(p)
            (root/"game-treated"/"StarCraft.exe").write_bytes(b"fake StarCraft.exe")
            (root/"treatment.dat").write_bytes(b"original")
            with self.assertRaisesRegex(ValueError,"identical mod"):
                batch.build_plan(p)
            p["pairs"]=3
            with self.assertRaisesRegex(ValueError,"pairs must be even"):
                batch.build_plan(p)
    def test_paired_sign_test(self):
        self.assertEqual(batch._binomial_sign_p(0, 0),1)
        self.assertEqual(batch._binomial_sign_p(5,0),0.0625)
        self.assertEqual(batch._binomial_sign_p(3,2),1)
        result=batch._paired_stat([1]*5)
        self.assertEqual(result["treatment_advantage"], 1)
        self.assertEqual(result["bootstrap_95pct_exploratory"],[1,1])
        self.assertIsNone(batch._paired_stat([])["treatment_advantage"])
    def test_pair_report_win_and_adoption(self):
        with tempfile.TemporaryDirectory() as temp:
            plan,_=batch.build_plan(fixture(Path(temp),4))
            records=[]
            for i,g in enumerate(plan["games"]):
                treat=g["manifest"]["variant"]=="devourer_speed_550"
                rec=artifact(g,"win" if treat else "loss",has_devourer=treat)
                records.append(rec)
            report=batch.analyze_plan(plan,records)
            self.assertEqual(report["win_result_heuristic"]["pairs"],4)
            self.assertEqual(report["win_result_heuristic"]["treatment_advantage"],1)
            self.assertEqual(report["devourer_order_adoption"]["treatment_advantage"],1)
            self.assertEqual(report["artifact_backed_pairs"],4)
            self.assertEqual(report["win_result_heuristic"]["sign_test_p"],0.125)
    def test_reject_confounded_and_incomplete_records(self):
        with tempfile.TemporaryDirectory() as temp:
            plan,_=batch.build_plan(fixture(Path(temp),2))
            a,b,c,d=plan["games"]
            x=artifact(a,"win")
            y=artifact(b,"loss")
            z=artifact(c,"win")
            y["manifest"]["weights_sha256"]="0"*64
            z["telemetry_errors"]=["unverified"]
            report=batch.analyze_plan(plan,[x,y,z])
            self.assertEqual(report["artifact_backed_pairs"],0)
            self.assertIn("planned_condition_mismatch",[v["reason"] for v in report["excluded"]])
            self.assertIn("incomplete_pair",[v["reason"] for v in report["excluded"]])
    def test_duplicates_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            plan,_=batch.build_plan(fixture(Path(temp),2))
            first=artifact(plan["games"][0],"win")
            report=batch.analyze_plan(plan,[first,first])
            self.assertEqual(report["artifact_backed_pairs"],0)
            self.assertIn("unknown_or_duplicate_run",[v["reason"] for v in report["excluded"]])


if __name__=='__main__':
    unittest.main()
