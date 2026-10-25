"""Strict offline reader and measurement extractor for passive BWAPI trace sidecars.

A trace is NOT evidence of executable identity or fair bot play until tied to
an independently attested native game run. Snapshot data is visibility-
conditioned. Only contiguous unit samples support movement/attack/spore deltas.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean, median

PRODUCER = "bwapi_tournament_trace_v1"
MAX_BYTES = 128 * 1024 * 1024 + 4 * 1024
MAX_LINE = 128 * 1024
MAX_FRAMES = 1_500_000
MAX_UNITS = 160
DEVO = 62
MUTA = 43
AIR_UNIT_TYPES = frozenset((8,9,11,12,42,43,44,47,58,60,62,69,70,71,72,84))
ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
VARIANTS = {
    "control": {"speed": 5.0, "cooldown": 100},
    "devourer_speed_550": {"speed": 5.5, "cooldown": 100},
    "devourer_cooldown_90": {"speed": 5.0, "cooldown": 90},
    "devourer_speed_550_cooldown_90": {"speed": 5.5, "cooldown": 90},
}


class TraceError(ValueError):
    pass


def _integer(x, name, low=0, high=10**9):
    if type(x) is not int or not low <= x <= high:
        raise TraceError(f"{name}: expected integer in [{low}, {high}]")


def _boolean(x, name):
    if type(x) is not bool:
        raise TraceError(f"{name}: expected boolean")


def _number(x, name, limit=50000):
    if type(x) not in (int, float) or not math.isfinite(x) or abs(x) > limit:
        raise TraceError(f"{name}: invalid numeric value")


def _unit(obj, ids, frame):
    if not isinstance(obj, dict):
        raise TraceError("unit must be an object")
    for k, low, high in (
        ("id", 0, 20000), ("player", -1, 255), ("type", 0, 227),
        ("x", -100, 20000), ("y", -100, 20000),
        ("hp", -100, 50000), ("shields", 0, 50000),
        ("air_cooldown", 0, 65535), ("spores", 0, 9),
        ("target_id", -1, 20000)):
        _integer(obj.get(k), f"unit.{k}", low, high)
    for k in ("vx", "vy"):
        _number(obj.get(k), f"unit.{k}", 128)
    for k in ("attack_start", "attack_frame", "attacking"):
        _boolean(obj.get(k), f"unit.{k}")
    if obj["id"] in ids:
        raise TraceError(f"duplicate unit id {obj['id']} in frame {frame}")
    ids.add(obj["id"])


def read_trace(path: Path) -> dict:
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise TraceError("trace exceeds byte limit")
    digest = hashlib.sha256()
    header, footer = None, None
    frames = []
    previous_frame = -1
    missing_frames = 0
    incomplete_frames = 0
    saw_end = False
    try:
        with p.open("rb") as f:
            for line_number, raw in enumerate(f, 1):
                digest.update(raw)
                if len(raw) > MAX_LINE:
                    raise TraceError(f"line {line_number}: oversized JSON record")
                if not raw.endswith(b"\n"):
                    raise TraceError(f"line {line_number}: incomplete JSON line")
                try:
                    event = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TraceError(f"line {line_number}: invalid JSON") from exc
                if not isinstance(event, dict) or event.get("schema") != 1:
                    raise TraceError(f"line {line_number}: unsupported schema")
                kind = event.get("event")
                if kind == "start":
                    if header is not None or frames or footer is not None:
                        raise TraceError("start must be the first unique event")
                    if event.get("producer") != PRODUCER:
                        raise TraceError("unknown trace producer")
                    for k in ("run_id", "variant"):
                        if not isinstance(event.get(k), str) or not ID_RE.fullmatch(event[k]):
                            raise TraceError(f"missing/invalid {k}")
                    if event["variant"] not in VARIANTS:
                        raise TraceError("unsupported intervention")
                    _integer(event.get("frame_step"), "frame_step", 1, 1)
                    _boolean(event.get("full_information_at_start"), "full_information_at_start")
                    _boolean(event.get("replay_mode"), "replay_mode")
                    _integer(event.get("self_player_id"), "self_player_id", -1, 11)
                    _integer(event.get("start_tile_x"), "start_tile_x", -1, 256)
                    _integer(event.get("start_tile_y"), "start_tile_y", -1, 256)
                    _integer(event.get("observed_rng_seed"), "observed_rng_seed", 0, 2**32-1)
                    map_sha1 = event.get("map_sha1")
                    if not isinstance(map_sha1,str) or (map_sha1 and
                           re.fullmatch(r"[0-9a-fA-F]{40}",map_sha1) is None):
                        raise TraceError("invalid map_sha1")
                    header = event
                elif kind == "frame":
                    if header is None or saw_end:
                        raise TraceError("frame out of sequence")
                    frame = event.get("frame")
                    _integer(frame, "frame", 0, 10_000_000)
                    if frame <= previous_frame:
                        raise TraceError("nonmonotonic frame")
                    if previous_frame >= 0:
                        missing_frames += frame - previous_frame - 1
                    previous_frame = frame
                    _boolean(event.get("complete_information"), "complete_information")
                    _integer(event.get("main_ai_event_ms"), "main_ai_event_ms", 0, 60000)
                    _integer(event.get("logical_fps"), "logical_fps", 0, 2000)
                    _integer(event.get("dropped_units"), "dropped_units", 0, 100000)
                    units = event.get("units")
                    if not isinstance(units, list) or len(units) > MAX_UNITS:
                        raise TraceError("unit count out of bounds")
                    ids = set()
                    for u in units:
                        _unit(u, ids, frame)
                    if event["dropped_units"]:
                        incomplete_frames += 1
                    frames.append(event)
                    if len(frames) > MAX_FRAMES:
                        raise TraceError("too many trace frames")
                elif kind == "end":
                    if header is None or saw_end:
                        raise TraceError("end event out of sequence")
                    _integer(event.get("frame"), "end.frame", 0, 10_000_000)
                    _integer(event.get("observed_frames"), "observed_frames", 0, MAX_FRAMES)
                    _boolean(event.get("truncated"), "truncated")
                    _boolean(event.get("win_reported_for_current_player"), "win_reported_for_current_player")
                    if event["observed_frames"] != len(frames):
                        raise TraceError("end count disagrees with sampled frames")
                    if previous_frame >= 0 and event["frame"] < previous_frame:
                        raise TraceError("end frame precedes observations")
                    footer = event
                    saw_end = True
                else:
                    raise TraceError(f"unknown event type: {kind!r}")
    except OSError as exc:
        raise TraceError(f"cannot read trace: {exc}") from exc
    if header is None or footer is None or footer["truncated"] or not frames:
        raise TraceError("trace is incomplete/truncated")
    return {"header": header, "frames": frames, "end": footer,
            "sha256": digest.hexdigest(), "missing_frames": missing_frames,
            "incomplete_frames": incomplete_frames}


def _quantile(values, q):
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    a = int(pos)
    return s[a] * (1 - (pos - a)) + s[min(a + 1, len(s)-1)] * (pos - a)


def analyze_trace(trace: dict, *, require_complete: bool = False,
                  devourer_range_px: float = 192) -> dict:
    if type(devourer_range_px) not in (int,float) or not math.isfinite(devourer_range_px) or not 1 <= devourer_range_px <= 4000:
        raise TraceError("invalid Devourer attack range")
    header, frames = trace["header"], trace["frames"]
    # In strict calibration mode any missing frame/overflow/global-view access
    # blocks calibrated outputs. Exploratory summaries still report their gaps.
    has_full = any(f["complete_information"] for f in frames)
    compromised = (trace["missing_frames"] > 0 or
                   trace["incomplete_frames"] > 0 or has_full or
                   header["full_information_at_start"])
    if require_complete and compromised:
        raise TraceError("strict calibration requires contiguous fog-limited uncapped frames")
    unit_previous: dict[int, tuple[int, dict]] = {}
    speeds, attacks, cooldown_resets = defaultdict(list), defaultdict(list), defaultdict(list)
    spore_increments = Counter()
    spore_drops = Counter()
    first_decay_lags = []
    spore_last_increase = {}
    windows = Counter()
    devourer_to_muta = []
    attack_ready_moving = 0
    attack_ready = 0
    target_range_readiness = Counter()
    target_range_visible_samples = 0
    target_distances_ready = []
    ai_times = []
    game_fps = []
    for fr in frames:
        if fr["dropped_units"]:
            # Mark this frame unusable for comparisons because the set was
            # truncated at a fixed count, which can bias unit selection.
            unit_previous.clear()
            continue
        frame = fr["frame"]
        ai_times.append(fr["main_ai_event_ms"])
        game_fps.append(fr["logical_fps"])
        by_player = defaultdict(lambda: {"devourers": [], "mutas": []})
        frame_by_unit_id = {u["id"]:u for u in fr["units"]}
        for u in fr["units"]:
            typ = u["type"]
            uid = u["id"]
            player = u["player"]
            if typ == DEVO:
                by_player[player]["devourers"].append(u)
                if u["air_cooldown"] == 0 and u["attacking"]:
                    attack_ready += 1
                    if math.hypot(u["vx"], u["vy"]) > 0.5:
                        attack_ready_moving += 1
                    enemy = frame_by_unit_id.get(u["target_id"])
                    if (enemy is not None and enemy["player"] != player
                            and enemy["player"] >= 0 and enemy["type"] in AIR_UNIT_TYPES):
                        dist = math.hypot(u["x"]-enemy["x"],u["y"]-enemy["y"])
                        target_range_visible_samples += 1
                        target_distances_ready.append(dist)
                        if dist > devourer_range_px + 2:
                            target_range_readiness["outside"] += 1
                        else:
                            target_range_readiness["inside"] += 1
            elif typ == MUTA:
                by_player[player]["mutas"].append(u)
            prior = unit_previous.get(uid)
            if prior is not None:
                pf, prev = prior
                if (frame == pf+1 and prev["type"] == typ
                    and prev["player"] == player):
                    v = math.hypot(u["vx"], u["vy"])
                    if typ == DEVO and 0.4 <= v <= 32:
                        speeds[player].append(v)
                        windows["devourer_motion_samples"] += 1
                    if typ == DEVO:
                        if u["attack_start"] and not prev["attack_start"]:
                            attacks[uid].append(frame)
                            windows["devourer_attack_starts"] += 1
                        if u["air_cooldown"] >= prev["air_cooldown"]+8:
                            cooldown_resets[uid].append((frame,u["air_cooldown"]))
                    if u["spores"] > prev["spores"]:
                        spore_increments[typ] += u["spores"]-prev["spores"]
                        spore_last_increase[uid] = frame
                    elif u["spores"] < prev["spores"]:
                        spore_drops[typ] += prev["spores"]-u["spores"]
                        if uid in spore_last_increase:
                            first_decay_lags.append(frame-spore_last_increase.pop(uid))
                elif frame > pf+1:
                    windows["unobserved_unit_gaps"] += 1
            unit_previous[uid] = (frame, u)
        for pid, fleets in by_player.items():
            mutas = fleets["mutas"]
            devs = fleets["devourers"]
            if mutas and devs:
                cx = mean(x["x"] for x in mutas)
                cy = mean(x["y"] for x in mutas)
                for d in devs:
                    devourer_to_muta.append(math.hypot(d["x"]-cx,d["y"]-cy))
    shot_intervals = []
    for ts in attacks.values():
        shot_intervals.extend(b-a for a,b in zip(ts,ts[1:]) if 5 <= b-a <= 2000)
    # A reset >8 frames is a stronger attack-cadence clue than generic
    # "attacking" state. Still subject to animation, changing targets and
    # BW's documented -1..+2 randomized cooldown jitter.
    reset_values = [cd for seq in cooldown_resets.values() for _,cd in seq]
    sample_speeds = [speed for group in speeds.values() for speed in group]
    first_devourer_frame = next(
        (fr["frame"] for fr in frames if any(u["type"] == DEVO for u in fr["units"])), None)
    return {
        "schema": 1, "run_id": header["run_id"], "variant": header["variant"],
        "self_player_id":header["self_player_id"],
        "start_tile":[header["start_tile_x"],header["start_tile_y"]],
        "observed_rng_seed":header["observed_rng_seed"],
        "map_sha1":header["map_sha1"],
        "replay_mode":header["replay_mode"],
        "main_ai_callback_ms_p50":median(ai_times) if ai_times else None,
        "main_ai_callback_ms_p95":_quantile(ai_times,0.95),
        "logical_fps_median":median(game_fps) if game_fps else None,
        "trace_sha256": trace["sha256"], "observed_frames": len(frames),
        "first_frame": frames[0]["frame"], "last_frame": frames[-1]["frame"],
        "missing_frames": trace["missing_frames"],
        "incomplete_frames": trace["incomplete_frames"],
        "full_information_seen": has_full or header["full_information_at_start"],
        "strict_calibration_eligible": not compromised,
        "first_devourer_frame": first_devourer_frame,
        "devourer_moving_velocity_samples": len(sample_speeds),
        "devourer_moving_speed_p90_px_per_frame": _quantile(sample_speeds,0.90),
        "devourer_moving_speed_p99_px_per_frame": _quantile(sample_speeds,0.99),
        "attack_start_event_count": windows["devourer_attack_starts"],
        "devourer_attack_interval_count": len(shot_intervals),
        "devourer_attack_interval_median_frames":
            median(shot_intervals) if shot_intervals else None,
        "devourer_cooldown_reset_count": len(reset_values),
        "devourer_cooldown_reset_median":
            median(reset_values) if reset_values else None,
        "observed_spore_stack_increments": dict(spore_increments),
        "observed_spore_stack_decrements": dict(spore_drops),
        "spore_increase_to_first_decrease_median_frames":
            median(first_decay_lags) if first_decay_lags else None,
        "devourer_to_mutalisk_centroid_median_px":
            median(devourer_to_muta) if devourer_to_muta else None,
        "devourer_cooldown_ready_while_attacking_samples": attack_ready,
        "devourer_cooldown_ready_and_moving_fraction":
            (attack_ready_moving/attack_ready if attack_ready else None),
        "assumed_devourer_attack_range_px":devourer_range_px,
        "ready_with_visible_target_samples":target_range_visible_samples,
        "ready_with_visible_target_outside_range":target_range_readiness["outside"],
        "ready_with_visible_target_inside_range":target_range_readiness["inside"],
        "ready_target_outside_nominal_range_fraction":
            (target_range_readiness["outside"]/target_range_visible_samples
             if target_range_visible_samples else None),
        "median_target_distance_when_ready_px":
            median(target_distances_ready) if target_distances_ready else None,
        "interpretation": {
            "main_ai_callback_ms": "BWAPI last AI callback duration, not necessarily full Pluto inference wall time",
            "observed_rng_seed": "initial game RNG seed, recorded by BWAPI; planned seed labels alone do not enforce this value",
            "map_sha1": "BWAPI's loaded map archive SHA-1, distinct from map-file SHA-256 or replay CHK hash",
            "speed_p90": "observed velocity in moving Devourers, biased by acceleration/turn and selection",
            "attack_intervals": "distinct attack-start signal transitions; weapon mechanics and target availability can lengthen the interval",
            "cooldown_reset": "post-attack cooldown counter, sometimes changed by status and jitter",
            "spores": "net increases on continuously visible unit IDs, with hidden-unit events excluded",
            "ready_moving": "diagnostic for unavailable firing opportunities, NOT confirmed missed attacks",
            "ready_target_outside_nominal_range": "visible target-distance proxy using assumed attack range; incomplete hidden targets and animation can confound it",
            "formation": "Devourer distance from visible friendly Muta centroid, not true control-group membership",
        }
    }


def analyze_file(path: Path, *, require_complete=False,
                 devourer_range_px:float=192) -> dict:
    return analyze_trace(read_trace(path), require_complete=require_complete,
                         devourer_range_px=devourer_range_px)


def compare_four(records: list[dict], *, minimum_speed_samples=30,
                 minimum_attack_intervals=5) -> dict:
    """Observed medians by arm with explicit missingness; no fake win rates."""
    by_variant = defaultdict(list)
    for r in records:
        if r.get("schema") != 1 or r.get("variant") not in VARIANTS:
            raise TraceError("invalid arm report")
        if r.get("strict_calibration_eligible") is not True:
            continue
        by_variant[r["variant"]].append(r)
    aggregated = {}
    for name in VARIANTS:
        rows = by_variant[name]
        good_speeds = [x["devourer_moving_speed_p90_px_per_frame"] for x in rows
                       if x.get("devourer_moving_velocity_samples",0) >= minimum_speed_samples
                       and x.get("devourer_moving_speed_p90_px_per_frame") is not None]
        good_intervals = [x["devourer_attack_interval_median_frames"] for x in rows
                          if x.get("devourer_attack_interval_count",0) >= minimum_attack_intervals
                          and x.get("devourer_attack_interval_median_frames") is not None]
        aggregated[name] = {
            "eligible_runs":len(rows),"speed_runs":len(good_speeds),
            "attack_interval_runs":len(good_intervals),
            "median_run_p90_speed_px_per_frame":median(good_speeds) if good_speeds else None,
            "median_run_attack_interval_frames":median(good_intervals) if good_intervals else None,
        }
    c=aggregated["control"]
    s=aggregated["devourer_speed_550"]
    d=aggregated["devourer_cooldown_90"]
    b=aggregated["devourer_speed_550_cooldown_90"]
    def diff(x,y,k):
        return round(x[k]-y[k],5) if x[k] is not None and y[k] is not None else None
    return {
        "schema":1, "arms":aggregated,
        "speed_only_minus_baseline":diff(s,c,"median_run_p90_speed_px_per_frame"),
        "cooldown_only_minus_baseline":diff(d,c,"median_run_attack_interval_frames"),
        "combined_speed_minus_baseline":diff(b,c,"median_run_p90_speed_px_per_frame"),
        "combined_interval_minus_baseline":diff(b,c,"median_run_attack_interval_frames"),
        "status":"observational_trace_summary_not_game_balance_effect",
        "caution":"A trace-only contrast is confounded by target availability, micro, map, and game timing",
    }


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    one=sub.add_parser("report")
    one.add_argument("--trace",type=Path,required=True)
    one.add_argument("--strict",action="store_true")
    one.add_argument("--devourer-range-px",type=float,default=192)
    one.add_argument("--output",type=Path,required=True)
    many=sub.add_parser("compare")
    many.add_argument("--reports",type=Path,required=True)
    many.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    if args.command=="report":
        result=analyze_file(args.trace,require_complete=args.strict,
                            devourer_range_px=args.devourer_range_px)
    else:
        reports=[json.loads(f.read_text(encoding="utf-8")) for f in sorted(args.reports.glob("*.json"))]
        result=compare_four(reports)
    with args.output.open("x",encoding="utf-8") as f:
        json.dump(result,f,indent=2,sort_keys=True)
        f.write("\n")
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__ == "__main__":
    main()
