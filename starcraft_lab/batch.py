"""Offline A/B design for Pluto on owned Brood War 1.16.1 copies.

Creates plans and immutable frozen bandit snapshots. This module never runs,
patches, or copies StarCraft executables and never changes game directories.
Actual start position and RNG seed need live confirmation by the launcher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from math import comb
from pathlib import Path
import random
from statistics import mean

CONTROL = "control"
TREATMENT = "devourer_speed_550"
VARIANTS = (CONTROL, TREATMENT)
PINNED_FILES = {
    "engine_sha256": "StarCraft.exe",
    "agent_binary_sha256": "bwapi-data/AI/pluto.dll",
    "infer_sha256": "bwapi-data/AI/pluto/pluto_infer.exe",
    "weights_sha256": "bwapi-data/AI/pluto/pluto_weights.bin",
    "bwapi_ini_sha256": "bwapi-data/bwapi.ini",
}


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _required_path(value: str | Path) -> Path:
    p = Path(value).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"required file absent: {p}")
    return p


def _valid_id(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 40
            and value[0].isalnum()
            and all(c.isascii() and (c.isalnum() or c in '_-.') for c in value))


def conditions(spec: dict) -> tuple[dict, dict]:
    """Read all input file hashes; require strictly identical engine/model files."""
    for key in ("experiment_id", "pairs", "schedule_seed", "map_file",
                "initial_bandit_file", "conditions", "agent_model", "opponent",
                "player_race", "opponent_race"):
        if key not in spec:
            raise ValueError(f"experiment spec missing: {key}")
    if not _valid_id(spec["experiment_id"]):
        raise ValueError("invalid experiment_id")
    if type(spec["pairs"]) is not int or not (2 <= spec["pairs"] <= 2000) or spec["pairs"] % 2:
        raise ValueError("pairs must be even, between 2 and 2000, for balanced crossovers")
    if type(spec["schedule_seed"]) is not int or not (0 <= spec["schedule_seed"] < 2**64):
        raise ValueError("invalid schedule_seed")
    if spec["player_race"] not in ("Zerg", "Protoss", "Terran") or spec["opponent_race"] not in ("Zerg", "Protoss", "Terran"):
        raise ValueError("invalid races")
    if any(not isinstance(spec[k], str) or not spec[k].strip() for k in ("agent_model", "opponent")):
        raise ValueError("invalid model/opponent")
    provided = spec["conditions"]
    if not isinstance(provided, dict) or set(provided) != set(VARIANTS):
        raise ValueError("conditions must contain control and devourer_speed_550 only")
    fingerprints: dict[str, dict] = {}
    locations: dict[str, dict] = {}
    for variant in VARIANTS:
        entry = provided[variant]
        if not isinstance(entry, dict) or "game_dir" not in entry or "mod_file" not in entry:
            raise ValueError(f"{variant}: provide game_dir and mod_file")
        root = Path(entry["game_dir"]).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"{variant} game_dir absent: {root}")
        source = _required_path(entry["mod_file"])
        pins = {label: checksum(_required_path(root / rel)) for label, rel in PINNED_FILES.items()}
        pins["mod_sha256"] = checksum(source)
        fingerprints[variant] = pins
        locations[variant] = {"game_dir": str(root), "mod_file": str(source)}
    for k in PINNED_FILES:
        if fingerprints[CONTROL][k] != fingerprints[TREATMENT][k]:
            raise ValueError(f"A/B game installations differ in {k}")
    if fingerprints[CONTROL]["mod_sha256"] == fingerprints[TREATMENT]["mod_sha256"]:
        raise ValueError("control and treatment have identical mod bytes")
    return fingerprints, locations


def _assignments(pairs: int, seed: int) -> list[tuple[str, str]]:
    """Balance both spawn A/B and order C/T vs T/C, including 30 pairs."""
    rng = random.Random(seed)
    base = [("A", CONTROL), ("A", TREATMENT), ("B", CONTROL), ("B", TREATMENT)]
    schedule: list[tuple[str, str]] = []
    for _ in range(pairs // 4):
        block = list(base)
        rng.shuffle(block)
        schedule.extend(block)
    if pairs % 4 == 2:
        complement = rng.choice((("A", CONTROL), ("B", TREATMENT)))
        if complement[0] == "A":
            extra = [("A", CONTROL), ("B", TREATMENT)]
        else:
            extra = [("A", TREATMENT), ("B", CONTROL)]
        rng.shuffle(extra)
        schedule.extend(extra)
    return schedule


def build_plan(spec: dict) -> tuple[dict, bytes]:
    fingerprints, locations = conditions(spec)
    bandit = _required_path(spec["initial_bandit_file"])
    map_file = _required_path(spec["map_file"])
    bandit_bytes = bandit.read_bytes()
    if not bandit_bytes:
        raise ValueError("empty Pluto bandit snapshot; capture a valid initial stats file")
    bandit_sha = hashlib.sha256(bandit_bytes).hexdigest()
    map_sha = checksum(map_file)
    experiment_id = spec["experiment_id"]
    seed = spec["schedule_seed"]
    rng = random.Random(seed + 1)
    games: list[dict] = []
    schedule = _assignments(spec["pairs"], seed)
    for idx, (spawn, first) in enumerate(schedule, 1):
        pair_id = f"{experiment_id}-p{idx:04d}"
        requested_seed = rng.getrandbits(32)
        order = [first, TREATMENT if first == CONTROL else CONTROL]
        for position, variant in enumerate(order, 1):
            game_id = f"{pair_id}-{variant}"
            pin = fingerprints[variant]
            m = {
                "schema": 1, "experiment_id": experiment_id,
                "run_id": game_id, "pair_id": pair_id,
                "variant": variant, "engine": "starcraft_1161", "agent": "pluto",
                "agent_model": spec["agent_model"],
                "agent_binary_sha256": pin["agent_binary_sha256"],
                "engine_sha256": pin["engine_sha256"],
                "infer_sha256": pin["infer_sha256"],
                "weights_sha256": pin["weights_sha256"],
                "bwapi_ini_sha256": pin["bwapi_ini_sha256"],
                "map_sha256": map_sha, "mod_sha256": pin["mod_sha256"],
                "opponent": spec["opponent"], "player_race": spec["player_race"],
                "opponent_race": spec["opponent_race"],
                "spawn": spawn, "seed": requested_seed,
                "agent_player_id": None, "bandit_initial_sha256": bandit_sha,
                "result": "unknown", "result_evidence": "none", "result_evidence_sha256": None,
                "assignment_index": 2 * idx - (2 - position),
                "seed_applied_to_engine": False, "spawn_verified_in_replay": False,
            }
            games.append({"manifest": m, "game_dir": locations[variant]["game_dir"],
                          "mod_file": locations[variant]["mod_file"],
                          "map_file": str(map_file)})
    return {
        "schema": 1, "experiment_id": experiment_id,
        "pairs": spec["pairs"], "schedule_seed": seed,
        "source_bandit_sha256": bandit_sha, "source_map_sha256": map_sha,
        "frozen_bandit_filename": "bandit_baseline.txt",
        "seed_controlled": False, "spawn_controlled": False,
        "games": games,
    }, bandit_bytes


def save_plan(spec: dict, dest: Path) -> dict:
    plan, seed = build_plan(spec)
    dest = Path(dest).expanduser().resolve()
    for game in plan["games"]:
        game_dir = Path(game["game_dir"])
        if dest == game_dir or game_dir in dest.parents:
            raise ValueError("plan output must be outside original game directories")
    dest.mkdir(parents=True, exist_ok=False)
    (dest / "manifests").mkdir()
    (dest / "bandit_baseline.txt").write_bytes(seed)
    for g in plan["games"]:
        m = g["manifest"]
        (dest / "manifests" / (m["run_id"] + ".json")).write_text(
            json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (dest / "plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def _binomial_sign_p(plus: int, minus: int) -> float:
    n = plus + minus
    if n == 0:
        return 1.0
    small = min(plus, minus)
    return min(1.0, 2 * sum(comb(n, k) for k in range(small + 1)) / (2**n))


def _paired_stat(deltas: list[int]) -> dict:
    if not deltas:
        return {"pairs": 0, "treatment_advantage": None, "improved": 0,
                "worsened": 0, "unchanged": 0, "sign_test_p": None,
                "bootstrap_95pct_exploratory": None}
    plus, minus = deltas.count(1), deltas.count(-1)
    rng = random.Random(3010)
    n = len(deltas)
    sample_means = sorted(mean(deltas[rng.randrange(n)] for _ in range(n)) for _ in range(4000))
    return {"pairs": n, "treatment_advantage": mean(deltas), "improved": plus,
            "worsened": minus, "unchanged": deltas.count(0),
            "sign_test_p": _binomial_sign_p(plus, minus),
            "bootstrap_95pct_exploratory": [sample_means[100], sample_means[3899]]}


def analyze_plan(plan: dict, records: list[dict]) -> dict:
    """Evaluate paired wins/orders only for matching run manifests with evidence.

    Any incomplete/mismatched pair is excluded, with reasons for audit.
    """
    from . import telemetry
    expected = {x["manifest"]["run_id"]: x["manifest"] for x in plan["games"]}
    by_run = {}
    exclusions: list[dict] = []
    for r in records:
        m = r.get("manifest")
        if not isinstance(m, dict):
            exclusions.append({"run": None, "reason": "invalid_manifest"})
            continue
        run_id = m.get("run_id")
        if run_id not in expected or run_id in by_run:
            exclusions.append({"run": run_id, "reason": "unknown_or_duplicate_run"})
            continue
        p = expected[run_id]
        immutable = [k for k in p if k not in ("result", "result_evidence", "result_evidence_sha256", "agent_player_id", "spawn_verified_in_replay")]
        if any(m.get(k) != p[k] for k in immutable):
            exclusions.append({"run": run_id, "reason": "planned_condition_mismatch"})
            continue
        if not telemetry.artifact_backed(r):
            exclusions.append({"run": run_id, "reason": "missing_artifact_backing"})
            continue
        by_run[run_id] = r
    outcome_deltas, adoption_deltas = [], []
    paired = []
    for i in range(0, len(plan["games"]), 2):
        ga, gb = plan["games"][i:i+2]
        ma, mb = ga["manifest"], gb["manifest"]
        if ma["pair_id"] != mb["pair_id"]:
            raise ValueError("malformed plan: neighboring games do not share pair")
        a, b = by_run.get(ma["run_id"]), by_run.get(mb["run_id"])
        if a is None or b is None:
            exclusions.append({"pair": ma["pair_id"], "reason": "incomplete_pair"})
            continue
        r = {a["manifest"]["variant"]: a, b["manifest"]["variant"]: b}
        if set(r) != set(VARIANTS):
            raise ValueError("malformed plan: expected control and treatment")
        c, t = r[CONTROL], r[TREATMENT]
        cr, tr = c["manifest"]["result"], t["manifest"]["result"]
        pair = {"pair_id": ma["pair_id"], "control_result": cr, "treatment_result": tr}
        if cr in ("win", "loss") and tr in ("win", "loss"):
            delta = int(tr == "win") - int(cr == "win")
            outcome_deltas.append(delta)
            pair["win_delta"] = delta
        else:
            pair["win_delta"] = None
        if c.get("replay_probe_consistent") and t.get("replay_probe_consistent"):
            obs_c, obs_t = c.get("replay_observations"), t.get("replay_observations")
            try:
                ca = int(obs_c["unit_orders"]["devourer"]["issued_orders"] > 0)
                ta = int(obs_t["unit_orders"]["devourer"]["issued_orders"] > 0)
            except (TypeError, KeyError):
                pair["adoption_delta"] = None
            else:
                pair["adoption_delta"] = ta - ca
                adoption_deltas.append(ta - ca)
        else:
            pair["adoption_delta"] = None
        paired.append(pair)
    return {"schema": 1, "experiment_id": plan["experiment_id"],
            "expected_pairs": plan["pairs"], "artifact_backed_pairs": len(paired),
            "win_result_heuristic": _paired_stat(outcome_deltas),
            "devourer_order_adoption": _paired_stat(adoption_deltas),
            "excluded": exclusions, "pairs": paired,
            "cautions": ["replay_first_leaver_outcome_is_heuristic",
                         "game_RNG_seed_and_spawn_are_not_enforced_by_plan",
                         "observations_are_orders_not_completed_units",
                         "exploratory_bootstrap_and_no_multiple_testing_adjustment"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--spec", type=Path, required=True)
    plan_cmd.add_argument("--output", type=Path, required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--plan", type=Path, required=True)
    analyze.add_argument("--records", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.command == "plan":
        payload = save_plan(json.loads(args.spec.read_text(encoding="utf-8")), args.output)
        print(json.dumps({"experiment_id": payload["experiment_id"],
                          "pairs": payload["pairs"], "games": len(payload["games"]),
                          "output": str(args.output)}, indent=2))
    else:
        payload = json.loads(args.plan.read_text(encoding="utf-8"))
        records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.records.glob("*.json"))]
        print(json.dumps(analyze_plan(payload, records), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
