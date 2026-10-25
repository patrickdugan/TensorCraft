import copy
import unittest
from starcraft_lab.world_model import calibration_gate as gate
from starcraft_lab.world_model.trace_analysis import VARIANTS, TraceError


def reports(count=2):
    output=[]
    for name,(speed,cd) in VARIANTS.items():
        for i in range(count):
            output.append({
                "schema":1,"run_id":f"{name}_{i}","variant":name,
                "trace_sha256":f"{len(output)+1:064x}",
                "strict_calibration_eligible":True,
                "observed_frames":700,
                "devourer_moving_velocity_samples":650,
                "devourer_attack_interval_count":7,
                "devourer_moving_speed_p90_px_per_frame":speed+(i-0.5)*0.02,
                "devourer_attack_interval_median_frames":cd,
            })
    return output


def receipts():
    return {name:{
        "schema":2,"variant":name,"new_speed_raw":int(speed*256),
        "new_cooldown_frames":cd,"output_flingy_sha256":f"{i+1:064x}",
        "output_weapons_sha256":f"{i+5:064x}"
    } for i,(name,(speed,cd)) in enumerate(VARIANTS.items())}


class CalibrationGateTests(unittest.TestCase):
    def test_four_arm_gate_is_diagnostic_not_model_update(self):
        proposed=gate.propose(reports(),receipts())
        self.assertEqual(proposed["status"],"measurement_consistent_pending_native_identity")
        self.assertIs(proposed["model_parameters_changed"],False)
        self.assertAlmostEqual(proposed["observed_deltas"]["speed_only_delta"],0.5)
        self.assertAlmostEqual(proposed["observed_deltas"]["cooldown_only_delta"],-10.0)
        self.assertAlmostEqual(proposed["observed_deltas"]["combined_speed_delta"],0.5)
        self.assertAlmostEqual(proposed["observed_deltas"]["combined_cadence_delta"],-10)
        self.assertEqual(proposed["arms"]["control"]["independent_traces"],2)
        self.assertEqual(len(proposed["further_evidence_needed"]),5)
    def test_fails_when_single_or_missing_sources(self):
        self.assertEqual(gate.propose(reports(1),receipts())["status"],
                         "insufficient_or_discordant_measurement")
        missing=gate.propose(reports())
        self.assertEqual(missing["status"],"insufficient_or_discordant_measurement")
        self.assertIn("mod_receipts_not_supplied",missing["exclusions"])
    def test_mismatched_receipt_fails_closed(self):
        r=receipts()
        r["devourer_cooldown_90"]["new_cooldown_frames"]=80
        with self.assertRaisesRegex(TraceError,"receipt disagrees"):
            gate.propose(reports(),r)
        r=receipts()
        r["control"]["output_weapons_sha256"]=None
        with self.assertRaisesRegex(TraceError,"missing staged"):
            gate.propose(reports(),r)
    def test_replay_corruption_and_trace_duplication_excluded(self):
        sample=reports()
        sample[0]["trace_sha256"]=sample[1]["trace_sha256"]
        sample[2]["strict_calibration_eligible"]=False
        sample[3]["devourer_attack_interval_count"]=3
        outcome=gate.propose(sample,receipts())
        self.assertNotEqual(outcome["status"],"measurement_consistent_pending_native_identity")
        self.assertTrue(any("duplicate_trace_hash" in e for e in outcome["exclusions"]))
        self.assertTrue(any("ineligible_trace" in e for e in outcome["exclusions"]))
        self.assertTrue(any("insufficient_attacks" in e for e in outcome["exclusions"]))
    def test_conflicting_trajectory_fails_readiness(self):
        sample=reports()
        for r in sample:
            if r["variant"]=="devourer_speed_550":
                r["devourer_moving_speed_p90_px_per_frame"]=4.5
        outcome=gate.propose(sample,receipts())
        self.assertFalse(outcome["arms"]["devourer_speed_550"]["speed_diagnostic_pass"])
        self.assertIsNone(outcome["observed_deltas"])


if __name__=="__main__":
    unittest.main()
