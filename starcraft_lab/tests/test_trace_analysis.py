"""Synthetic trace contracts. A Windows/Pluto game is never claimed here."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest

from starcraft_lab.world_model import trace_analysis as ta


def frames_for(variant="control", num=700, damaged=True):
    speed = ta.VARIANTS[variant]["speed"]
    cooldown = ta.VARIANTS[variant]["cooldown"]
    events = [{
        "schema":1,"event":"start","run_id":"synthetic_case",
        "variant":variant,"producer":ta.PRODUCER,
        "frame_step":1,"full_information_at_start":False,
        "replay_mode":False,
    }]
    for frame in range(num):
        attack = frame in range(10,num,cooldown)
        spores=1 if 24<=frame<50 else 0
        events.append({
            "schema":1,"event":"frame","frame":frame,
            "complete_information":False,"dropped_units":0,
            "units":[{
                "id":10,"player":0,"type":62,
                "x":round(frame*speed),"y":200,
                "vx":speed,"vy":0.0,"hp":250,"shields":0,
                "air_cooldown":cooldown if attack else max(0,cooldown-(frame-10)%cooldown),
                "attack_start":attack,"attack_frame":attack,"attacking":True,
                "spores":0,"target_id":20,
            },{
                "id":11,"player":0,"type":43,
                "x":round(frame*6.67),"y":207,
                "vx":6.67,"vy":0.0,"hp":120,"shields":0,
                "air_cooldown":0,"attack_start":False,"attack_frame":False,
                "attacking":False,"spores":0,"target_id":-1
            },{
                "id":20,"player":1,"type":43,
                "x":800,"y":200,"vx":0.0,"vy":0.0,
                "hp":120,"shields":0,"air_cooldown":0,
                "attack_start":False,"attack_frame":False,"attacking":False,
                "spores":spores,"target_id":-1
            }]
        })
    events.append({"schema":1,"event":"end","frame":num,
                   "observed_frames":num,"truncated":False,
                   "win_reported_for_current_player":True})
    return events


def save(events,path):
    with path.open("wb") as f:
        for item in events:
            f.write((json.dumps(item,separators=(",",":"))+"\n").encode("utf-8"))


class TraceTests(unittest.TestCase):
    def test_valid_trace_and_motion_spore_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"run.jsonl"
            save(frames_for(),p)
            t=ta.read_trace(p)
            result=ta.analyze_trace(t,require_complete=True)
            self.assertTrue(result["strict_calibration_eligible"])
            self.assertEqual(result["observed_frames"],700)
            self.assertEqual(result["missing_frames"],0)
            self.assertEqual(result["first_devourer_frame"],0)
            self.assertAlmostEqual(result["devourer_moving_speed_p90_px_per_frame"],5.0,places=4)
            self.assertEqual(result["devourer_attack_interval_median_frames"],100)
            self.assertEqual(result["observed_spore_stack_increments"][43],1)
            self.assertEqual(result["observed_spore_stack_decrements"][43],1)
            self.assertEqual(result["spore_increase_to_first_decrease_median_frames"],26)
            self.assertIsNotNone(result["devourer_to_mutalisk_centroid_median_px"])
            self.assertEqual(len(result["trace_sha256"]),64)
    def test_invalid_json_and_missing_end_fail(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"run.jsonl"
            a=frames_for(num=4)
            save(a[:-1],p)
            with self.assertRaisesRegex(ta.TraceError,"incomplete/truncated"):
                ta.read_trace(p)
            p.write_bytes(b'{"schema":1,\n')
            with self.assertRaisesRegex(ta.TraceError,"invalid JSON"):
                ta.read_trace(p)
    def test_duplicate_unit_and_frame_reversal_fail(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"run.jsonl"
            a=frames_for(num=5)
            a[2]["units"].append(copy.deepcopy(a[2]["units"][0]))
            save(a,p)
            with self.assertRaisesRegex(ta.TraceError,"duplicate unit"):
                ta.read_trace(p)
            a=frames_for(num=5)
            a[3]["frame"]=a[2]["frame"]
            save(a,p)
            with self.assertRaisesRegex(ta.TraceError,"nonmonotonic"):
                ta.read_trace(p)
    def test_gap_and_visibility_block_strict_calibration(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"run.jsonl"
            a=frames_for(num=7)
            a[3]["frame"]+=1
            # shift all subsequent frames so strictly increasing
            for index in range(4,8):
                a[index]["frame"]+=1
            a[-1]["frame"]+=1
            save(a,p)
            t=ta.read_trace(p)
            self.assertEqual(t["missing_frames"],1)
            with self.assertRaisesRegex(ta.TraceError,"contiguous"):
                ta.analyze_trace(t,require_complete=True)
            a=frames_for(num=6)
            a[2]["complete_information"]=True
            save(a,p)
            t=ta.read_trace(p)
            with self.assertRaisesRegex(ta.TraceError,"fog-limited"):
                ta.analyze_trace(t,require_complete=True)
    def test_invalid_spore_bounds_and_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"run.jsonl"
            a=frames_for(num=4)
            a[2]["units"][0]["spores"]=10
            save(a,p)
            with self.assertRaisesRegex(ta.TraceError,"unit.spores"):
                ta.read_trace(p)
            a=frames_for(num=4)
            a[-1]["truncated"]=True
            save(a,p)
            with self.assertRaisesRegex(ta.TraceError,"incomplete/truncated"):
                ta.read_trace(p)
    def test_arm_contrasts_observed_not_game_result(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            summaries=[]
            for name in ta.VARIANTS:
                p=root/(name+".jsonl")
                save(frames_for(variant=name),p)
                summaries.append(ta.analyze_file(p,require_complete=True))
            report=ta.compare_four(summaries,minimum_attack_intervals=5)
            self.assertAlmostEqual(report["speed_only_minus_baseline"],0.5)
            self.assertAlmostEqual(report["combined_speed_minus_baseline"],0.5)
            self.assertAlmostEqual(report["cooldown_only_minus_baseline"],-10)
            self.assertAlmostEqual(report["combined_interval_minus_baseline"],-10)
            self.assertEqual(report["status"],"observational_trace_summary_not_game_balance_effect")
    def test_incomplete_unit_set_marks_calibration_ineligible(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"r.jsonl"
            a=frames_for(num=10)
            a[3]["dropped_units"]=2
            save(a,p)
            report=ta.analyze_file(p)
            self.assertEqual(report["incomplete_frames"],1)
            self.assertFalse(report["strict_calibration_eligible"])
            self.assertEqual(ta.compare_four([report])["arms"]["control"]["eligible_runs"],0)


if __name__ == "__main__":
    unittest.main()
