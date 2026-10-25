"""Cross-check native BWAPI traces, parsed replays and planned Pluto runs.

Run IDs / labels come from caller-controlled environment and are not proof
of a loaded mod. Actual engine RNG seed, player spawn tile and map SHA-1
provide independent observations that the earlier batch planner lacked.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .trace_analysis import TraceError


def hex_hash(x: object, digits: int) -> bool:
    import re
    return isinstance(x,str) and re.fullmatch(r"[a-fA-F0-9]{%d}" % digits,x) is not None


def bind(run_manifest:dict, trace_report:dict, replay_report:dict,
         map_file:Path|None=None, spawn_tiles:dict|None=None)->dict:
    if not isinstance(run_manifest,dict) or not isinstance(trace_report,dict) or not isinstance(replay_report,dict):
        raise TraceError("expected manifest and two report objects")
    for k in ("run_id","variant"):
        if trace_report.get(k) != run_manifest.get(k):
            raise TraceError(f"{k} differs between live trace and planned run")
    problems, warnings = [], []
    if trace_report.get("strict_calibration_eligible") is not True:
        problems.append("trace_not_strictly_contiguous")
    if trace_report.get("replay_mode") is True:
        problems.append("trace_is_replay_playback_not_live_game")
    expected_player = run_manifest.get("agent_player_id")
    if type(expected_player) is not int or trace_report.get("self_player_id") != expected_player:
        problems.append("pluto_player_id_not_attested")
    if replay_report.get("schema") != 1 or replay_report.get("producer") != "starcraft-replay-probe/replay-core":
        problems.append("replay_probe_schema_invalid")
    if replay_report.get("replay_sha256") != run_manifest.get("result_evidence_sha256"):
        problems.append("replay_hash_does_not_match_manifest")
    if not isinstance(replay_report.get("players"),list) or expected_player not in [
        p.get("player_id") for p in replay_report.get("players",[]) if isinstance(p,dict)
    ]:
        problems.append("pluto_player_missing_from_replay")
    replay_frames = replay_report.get("frame_count")
    trace_last = trace_report.get("last_frame")
    if (type(replay_frames) is not int or type(trace_last) is not int
        or abs(replay_frames - trace_last) > 48):
        problems.append("replay_trace_frame_extent_mismatch")
    observed_seed = trace_report.get("observed_rng_seed")
    requested_seed = run_manifest.get("seed")
    seed_matches = (type(observed_seed) is int and type(requested_seed) is int
                    and observed_seed == requested_seed)
    if not seed_matches:
        problems.append("actual_rng_seed_differs_from_requested")
    map_sha1 = trace_report.get("map_sha1")
    if not hex_hash(map_sha1,40):
        problems.append("trace_map_sha1_missing")
    map_file_sha256 = None
    map_file_sha1 = None
    if map_file is None:
        warnings.append("private_map_file_not_supplied_for_hash_comparison")
    else:
        contents=Path(map_file).read_bytes()
        map_file_sha1=hashlib.sha1(contents).hexdigest()
        map_file_sha256=hashlib.sha256(contents).hexdigest()
        if map_file_sha256 != run_manifest.get("map_sha256"):
            problems.append("map_file_sha256_mismatch")
        if map_file_sha1 != map_sha1:
            problems.append("bwapi_loaded_map_sha1_mismatch")
    observed_spawn=trace_report.get("start_tile")
    expected_spawn=None
    if spawn_tiles is None:
        warnings.append("spawn_tile_mapping_not_supplied")
    else:
        if not isinstance(spawn_tiles,dict) or run_manifest.get("spawn") not in spawn_tiles:
            raise TraceError("spawn tile dictionary missing this planned spawn")
        expected_spawn=spawn_tiles[run_manifest["spawn"]]
        if (not isinstance(expected_spawn,list) or len(expected_spawn)!=2 or
                any(type(v) is not int or v < 0 for v in expected_spawn)):
            raise TraceError("malformed expected spawn tile")
        if observed_spawn != expected_spawn:
            problems.append("observed_start_tile_differs_from_planned_spawn")
    result=run_manifest.get("result")
    guess=replay_report.get("winner_player_id")
    if result in ("win","loss") and type(guess) is int and type(expected_player) is int:
        derived="win" if guess==expected_player else "loss"
        if derived != result:
            problems.append("replay_first_leaver_vs_manifest_result_mismatch")
    else:
        warnings.append("winner_inference_not_confirmed")
    identity_ready=(not problems and map_file is not None and spawn_tiles is not None)
    return {
        "schema":1, "run_id":run_manifest.get("run_id"),
        "variant":run_manifest.get("variant"),
        "trace_sha256":trace_report.get("trace_sha256"),
        "replay_sha256":replay_report.get("replay_sha256"),
        "map_sha256":map_file_sha256, "map_sha1":map_file_sha1,
        "observed_rng_seed":observed_seed,"requested_rng_seed":requested_seed,
        "observed_spawn_tile":observed_spawn,"expected_spawn_tile":expected_spawn,
        "problems":problems,"warnings":warnings,
        "trace_replay_map_conditions_consistent":identity_ready,
        "mod_loaded_in_game_proven":False,
        "pluto_binary_identity_proven":False,
        "status":("cross_artifact_conditions_consistent_but_execution_unattested"
                  if identity_ready else "missing_or_mismatched_evidence"),
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--trace-report",type=Path,required=True)
    p.add_argument("--replay-report",type=Path,required=True)
    p.add_argument("--map-file",type=Path)
    p.add_argument("--spawn-tiles",type=Path)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    def load(path):return json.loads(path.read_text(encoding="utf-8"))
    tiles=load(args.spawn_tiles) if args.spawn_tiles else None
    report=bind(load(args.manifest),load(args.trace_report),load(args.replay_report),
                args.map_file,tiles)
    with args.output.open("x",encoding="utf-8") as f:
        json.dump(report,f,indent=2,sort_keys=True)
        f.write("\n")
    print(json.dumps({"status":report["status"],"problems":report["problems"],
                      "warnings":report["warnings"]},indent=2))


if __name__=="__main__":
    main()
