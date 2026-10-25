"""Propose, never silently apply, evidence-based Devourer combat parameters.

Inputs are JSON reports from trace_analysis and optional game-side receipts.
This is a measurement gate, not engine verification or a probabilistic model.
In particular, a generated mod receipt does NOT prove an MPQ was loaded.
"""
from __future__ import annotations
from collections import defaultdict
import argparse
import json
from pathlib import Path
from statistics import median
from .trace_analysis import VARIANTS, TraceError

MIN_INDEPENDENT_RUNS = 2
MIN_SPEED_SAMPLES = 60
MIN_ATTACK_INTERVALS = 5
MIN_FRAMES = 450


def _finite_number(x) -> bool:
    import math
    return type(x) in (int,float) and math.isfinite(x)


def propose(reports: list[dict], receipts: dict | None = None) -> dict:
    """Gate independently measured speed/cadence before calibration.

    A failed gate asks for a better native test and leaves the original model
    assumptions unchanged. Soundness of recorded client identity, actual MPQ
    loading, and replay outcome needs independent local verification.
    """
    by_arm = defaultdict(list)
    exclusions = []
    seen_hashes = set()
    for report in reports:
        if not isinstance(report, dict) or report.get("variant") not in VARIANTS:
            exclusions.append("unknown_or_bad_arm")
            continue
        variant = report["variant"]
        fingerprint = report.get("trace_sha256")
        if not isinstance(fingerprint,str) or len(fingerprint)!=64 or fingerprint in seen_hashes:
            exclusions.append(f"{variant}:missing_or_duplicate_trace_hash")
            continue
        seen_hashes.add(fingerprint)
        if report.get("strict_calibration_eligible") is not True:
            exclusions.append(f"{variant}:ineligible_trace")
            continue
        if type(report.get("observed_frames")) is not int or report["observed_frames"] < MIN_FRAMES:
            exclusions.append(f"{variant}:insufficient_frames")
            continue
        if (type(report.get("devourer_moving_velocity_samples")) is not int or
                report["devourer_moving_velocity_samples"] < MIN_SPEED_SAMPLES):
            exclusions.append(f"{variant}:insufficient_movement")
            continue
        if (type(report.get("devourer_attack_interval_count")) is not int or
                report["devourer_attack_interval_count"] < MIN_ATTACK_INTERVALS):
            exclusions.append(f"{variant}:insufficient_attacks")
            continue
        speed = report.get("devourer_moving_speed_p90_px_per_frame")
        cadence = report.get("devourer_attack_interval_median_frames")
        if not _finite_number(speed) or not _finite_number(cadence):
            exclusions.append(f"{variant}:nonnumeric_measurements")
            continue
        if not (0.1 < speed < 15 and 5 < cadence < 2000):
            exclusions.append(f"{variant}:out_of_range_measurements")
            continue
        by_arm[variant].append(report)

    arm = {}
    for variant, (expected_speed, expected_cd) in VARIANTS.items():
        rows = by_arm[variant]
        speed = median(r["devourer_moving_speed_p90_px_per_frame"] for r in rows) if rows else None
        cadence = median(r["devourer_attack_interval_median_frames"] for r in rows) if rows else None
        ok_count = len(rows) >= MIN_INDEPENDENT_RUNS
        # This is a deliberately loose diagnostic: observations may contain
        # weapon jitter and time lost to target acquisition.
        speed_near_expected = bool(ok_count and abs(speed-expected_speed) <= 0.35)
        cadence_near_expected = bool(ok_count and abs(cadence-expected_cd) <= 6)
        arm[variant] = {
            "expected_speed":expected_speed, "expected_cooldown":expected_cd,
            "independent_traces":len(rows),
            "observed_median_run_p90_speed":speed,
            "observed_median_run_attack_interval":cadence,
            "speed_diagnostic_pass":speed_near_expected,
            "cadence_diagnostic_pass":cadence_near_expected,
            "diagnostic_ready":speed_near_expected and cadence_near_expected,
            "trace_hashes":[r["trace_sha256"] for r in rows],
        }

    source_receipts = {}
    if receipts is not None:
        if set(receipts) != set(VARIANTS):
            raise TraceError("expected one receipt per intervention arm")
        for name, r in receipts.items():
            speed = r.get("new_speed_raw")
            cadence = r.get("new_cooldown_frames")
            expected_speed, expected_cd = VARIANTS[name]
            if r.get("schema") != 2 or speed != round(expected_speed*256) or cadence != expected_cd:
                raise TraceError(f"{name}: receipt disagrees with intended static intervention")
            hashes = (r.get("output_flingy_sha256"), r.get("output_weapons_sha256"))
            if any(not isinstance(h,str) or len(h)!=64 for h in hashes):
                raise TraceError(f"{name}: missing staged-file checksums")
            source_receipts[name] = {"flingy_sha256": hashes[0], "weapons_sha256": hashes[1]}
    else:
        exclusions.append("mod_receipts_not_supplied")

    all_ready = bool(source_receipts and all(v["diagnostic_ready"] for v in arm.values()))
    if all_ready:
        c,s,d,b=(arm[k] for k in VARIANTS)
        speed_change = s["observed_median_run_p90_speed"]-c["observed_median_run_p90_speed"]
        cooldown_change = d["observed_median_run_attack_interval"]-c["observed_median_run_attack_interval"]
        combined_speed = b["observed_median_run_p90_speed"]-c["observed_median_run_p90_speed"]
        combined_cadence = b["observed_median_run_attack_interval"]-c["observed_median_run_attack_interval"]
        observed = {
            "speed_only_delta":round(speed_change,4),
            "cooldown_only_delta":round(cooldown_change,4),
            "combined_speed_delta":round(combined_speed,4),
            "combined_cadence_delta":round(combined_cadence,4),
        }
    else:
        observed=None
    return {
        "schema": 1, "status": ("measurement_consistent_pending_native_identity"
                                if all_ready else "insufficient_or_discordant_measurement"),
        "model_parameters_changed": False,
        "arms": arm, "observed_deltas": observed,
        "receipts": source_receipts, "exclusions":exclusions,
        "further_evidence_needed": [
            "match engine and Pluto executable hashes against preflight",
            "prove that MPQDraft actually loaded each intended DAT",
            "pair game-replay hashes with tournament trace run IDs",
            "confirm sidecar did not disrupt Pluto's 6-frame inference cadence",
            "measure spore status and actual hits with controlled targets",
        ],
        "meaning": "diagnostic measurement proposal only, never a measured win rate",
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports",type=Path,required=True)
    parser.add_argument("--receipts",type=Path,help="JSON map of 4 generated mod receipts")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    rows=[json.loads(p.read_text(encoding="utf-8"))
          for p in sorted(args.reports.glob("*.json"))]
    receipts=json.loads(args.receipts.read_text(encoding="utf-8")) if args.receipts else None
    result=propose(rows,receipts)
    with args.output.open("x",encoding="utf-8") as file:
        json.dump(result,file,indent=2,sort_keys=True)
        file.write("\n")
    print(json.dumps({"status":result["status"],"exclusions":result["exclusions"]},indent=2))


if __name__=="__main__":
    main()
