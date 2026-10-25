"""Fail-closed collection and summary for paired StarCraft design experiments."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

HEX = re.compile(r"[0-9a-f]{64}\Z")
RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
RESULTS = {"win", "loss", "draw", "crash", "unknown"}
REQUIRED = (
    "schema", "run_id", "pair_id", "variant", "engine", "agent",
    "agent_model", "agent_binary_sha256", "engine_sha256", "map_sha256",
    "mod_sha256", "opponent", "player_race", "opponent_race", "spawn",
    "seed", "bandit_initial_sha256", "result", "result_evidence", "result_evidence_sha256",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate(m: dict) -> None:
    if not isinstance(m, dict) or any(k not in m for k in REQUIRED):
        raise ValueError("manifest missing required fields")
    if m["schema"] != 1 or not isinstance(m["seed"], int) or isinstance(m["seed"], bool) or m["seed"] < 0:
        raise ValueError("unsupported schema or seed")
    for k in ("run_id", "pair_id", "variant"):
        if not isinstance(m[k], str) or not RUN.fullmatch(m[k]):
            raise ValueError(f"invalid {k}")
    for k in ("agent_binary_sha256", "engine_sha256", "map_sha256", "mod_sha256", "bandit_initial_sha256"):
        if not isinstance(m[k], str) or not HEX.fullmatch(m[k]):
            raise ValueError(f"missing/invalid {k}")
    for k in ("engine", "agent", "agent_model", "opponent", "spawn"):
        if not isinstance(m[k], str) or not m[k].strip():
            raise ValueError(f"missing/invalid {k}")
    for k in ("player_race", "opponent_race"):
        if m[k] not in ("Zerg", "Protoss", "Terran"):
            raise ValueError(f"invalid {k}")
    if m["engine"] not in ("starcraft_1161", "openbw"):
        raise ValueError("engine must be starcraft_1161 or openbw")
    if m["agent"] == "pluto" and m["engine"] != "starcraft_1161":
        raise ValueError("Pluto on OpenBW has not been validated: do not label such runs as supported")
    if m["result"] not in RESULTS:
        raise ValueError("invalid result")
    if m["result_evidence"] not in ("replay", "tournament", "manual", "none"):
        raise ValueError("invalid result_evidence")
    if m["result"] in ("win", "loss", "draw") and m["result_evidence"] == "none":
        raise ValueError("scored results require explicit evidence")
    if m["result"] in ("crash", "unknown") and m["result_evidence"] in ("replay", "tournament"):
        raise ValueError("crash/unknown must not be recorded as verified scored results")
    evidence_hash = m["result_evidence_sha256"]
    if m["result_evidence"] in ("replay", "tournament"):
        if not isinstance(evidence_hash, str) or not HEX.fullmatch(evidence_hash):
            raise ValueError("a replay/tournament result requires result_evidence_sha256")
    elif evidence_hash is not None:
        raise ValueError("only replay/tournament results may declare a result_evidence_sha256")


def collect(manifest_file: Path, pluto_log: Path | None, dest: Path, evidence_file: Path | None = None) -> dict:
    m = json.loads(manifest_file.read_text(encoding="utf-8"))
    validate(m)
    events = []
    errors = []
    raw_sha = None
    evidence_verified = False
    if m["result_evidence"] in ("replay", "tournament"):
        if evidence_file is None:
            errors.append("result_evidence_file_missing")
        elif sha(evidence_file.read_bytes()) != m["result_evidence_sha256"]:
            errors.append("result_evidence_sha256_mismatch")
        else:
            evidence_verified = True
    elif evidence_file is not None:
        raise ValueError("evidence file supplied for an outcome without replay/tournament evidence")
    if pluto_log is not None:
        raw = pluto_log.read_bytes()
        raw_sha = sha(raw)
        for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            line = line.strip()
            if line.startswith("{"):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"malformed_json_line_{line_number}")
                else:
                    if isinstance(event, dict):
                        events.append(event)
                    else:
                        errors.append(f"nonobject_json_line_{line_number}")
    elif m["agent"] == "pluto":
        errors.append("pluto_log_not_provided")
    record = {"schema": 1, "manifest": m, "pluto_log_sha256": raw_sha,
              "pluto_json_events": events, "telemetry_errors": errors,
              "result_evidence_verified": evidence_verified}
    if dest.exists():
        raise FileExistsError(f"refusing to overwrite {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")
    return record


def verified(r: dict) -> bool:
    m = r["manifest"]
    return (not r["telemetry_errors"] and r.get("result_evidence_verified") is True
            and m["result"] in ("win", "loss", "draw")
            and m["result_evidence"] in ("replay", "tournament"))


def analyze(directory: Path) -> dict:
    records = []
    ids = set()
    for p in sorted(directory.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if r.get("schema") != 1 or not isinstance(r.get("telemetry_errors"), list):
            raise ValueError(f"invalid record schema: {p.name}")
        validate(r["manifest"])
        run_id = r["manifest"]["run_id"]
        if run_id in ids:
            raise ValueError(f"duplicate run_id {run_id}")
        ids.add(run_id)
        records.append(r)
    groups = defaultdict(lambda: {"win": 0, "loss": 0, "draw": 0,
                                  "crash": 0, "unknown": 0, "excluded": 0})
    pairs = defaultdict(list)
    for r in records:
        m = r["manifest"]
        v = groups[m["variant"]]
        if verified(r):
            v[m["result"]] += 1
        else:
            v["excluded"] += 1
            v[m["result"] if m["result"] in ("crash", "unknown") else "unknown"] += 1
        pairs[m["pair_id"]].append(r)
    summaries = {}
    for key, v in sorted(groups.items()):
        denominator = v["win"] + v["loss"]
        summaries[key] = {**v, "decisive_games": denominator,
                          "win_rate_decisive": v["win"] / denominator if denominator else None}
    paired = []
    mismatched = []
    common = ("engine", "agent", "agent_model", "agent_binary_sha256",
              "engine_sha256", "map_sha256", "opponent", "player_race",
              "opponent_race", "spawn", "seed", "bandit_initial_sha256")
    for pair_id, rows in sorted(pairs.items()):
        if len(rows) != 2 or len({r["manifest"]["variant"] for r in rows}) != 2:
            mismatched.append({"pair_id": pair_id, "reason": "incomplete_or_repeated_variant"})
            continue
        left, right = rows
        a, b = left["manifest"], right["manifest"]
        if any(a[k] != b[k] for k in common):
            mismatched.append({"pair_id": pair_id, "reason": "different_conditions"})
        elif a["mod_sha256"] == b["mod_sha256"]:
            mismatched.append({"pair_id": pair_id, "reason": "same_mod_hash"})
        elif not (verified(left) and verified(right)):
            mismatched.append({"pair_id": pair_id, "reason": "unverified_outcome"})
        else:
            paired.append({"pair_id": pair_id, "results": {a["variant"]: a["result"],
                                                              b["variant"]: b["result"]}})
    return {"schema": 1, "record_count": len(records), "variants": summaries,
            "verified_pairs": paired, "excluded_pairs": mismatched}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest="command", required=True)
    record = subs.add_parser("record")
    record.add_argument("--manifest", type=Path, required=True)
    record.add_argument("--pluto-log", type=Path)
    record.add_argument("--result-evidence-file", type=Path)
    record.add_argument("--output", type=Path, required=True)
    summary = subs.add_parser("analyze")
    summary.add_argument("--records", type=Path, required=True)
    args = ap.parse_args(argv)
    outcome = (collect(args.manifest, args.pluto_log, args.output, args.result_evidence_file) if args.command == "record"
               else analyze(args.records))
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
