"""Validate replay-core order reports against experiment manifests.

A replay captures issued commands. The first-leaver outcome is a heuristic;
neither identifies the bot executable nor reconstructs battlefield state.
"""
from __future__ import annotations
from statistics import median

EXPECTED_PRODUCER = "starcraft-replay-probe/replay-core"
SUPPORTED_UNITS = ("devourer", "guardian", "mutalisk", "greater_spire",
                   "scout", "observer", "siege_tank_tank_mode",
                   "siege_tank_siege_mode", "goliath")


def report_observations(report: dict, manifest: dict) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if not isinstance(report, dict) or report.get("schema") != 1 or report.get("producer") != EXPECTED_PRODUCER:
        return None, ["replay_report_schema_or_producer_invalid"]
    if report.get("replay_sha256") != manifest.get("result_evidence_sha256"):
        errors.append("replay_report_hash_mismatch")
    warnings = report.get("command_warnings")
    if not isinstance(warnings, dict) or warnings.get("clean") is not True:
        errors.append("replay_commands_incomplete")
    if report.get("engine_type") != "BroodWar":
        errors.append("replay_not_broodwar")
    players = report.get("players")
    if not isinstance(players, list) or len(players) != 2:
        return None, errors + ["replay_not_two_players"]
    ids = [p.get("player_id") for p in players if isinstance(p, dict)]
    if len(ids) != 2 or any(type(pid) is not int or not 0 <= pid <= 11 for pid in ids) or len(set(ids)) != 2:
        return None, errors + ["replay_player_ids_invalid"]
    player_id = manifest.get("agent_player_id")
    if type(player_id) is not int or player_id not in ids:
        return None, errors + ["agent_player_id_missing_or_invalid"]
    player = next(p for p in players if p["player_id"] == player_id)
    opponent = next(p for p in players if p["player_id"] != player_id)
    if player.get("race") != manifest.get("player_race") or opponent.get("race") != manifest.get("opponent_race"):
        errors.append("replay_race_mismatch")
    winner = report.get("winner_player_id")
    if type(winner) is not int or winner not in ids:
        errors.append("replay_winner_unknown")
        outcome = None
    else:
        outcome = "win" if winner == player_id else "loss"
        if outcome != manifest.get("result"):
            errors.append("replay_reported_outcome_mismatch")
    units = player.get("unit_orders")
    if not isinstance(units, dict):
        return None, errors + ["replay_unit_orders_missing"]
    observations = {}
    for unit in SUPPORTED_UNITS:
        item = units.get(unit)
        if not isinstance(item, dict):
            errors.append(f"missing_{unit}_orders")
            continue
        try:
            ordered, predicted = item["issued_orders"], item["model_estimated_starts"]
            first_order, first_start = item["first_order_frame"], item["first_model_estimated_start_frame"]
        except KeyError:
            errors.append(f"malformed_{unit}_orders")
            continue
        if (type(ordered) is not int or type(predicted) is not int
                or ordered < 0 or predicted < 0
                or any(f is not None and (type(f) is not int or f < 0) for f in (first_order, first_start))
                or (ordered == 0) != (first_order is None)
                or (predicted == 0) != (first_start is None)):
            errors.append(f"malformed_{unit}_orders")
            continue
        observations[unit] = {
            "issued_orders": ordered, "first_order_frame": first_order,
            "model_estimated_starts": predicted, "first_model_estimated_start_frame": first_start,
        }
    extracted = {
        "agent_player_id": player_id, "agent_replay_name": player.get("name"),
        "opponent_replay_name": opponent.get("name"),
        "map_name": report.get("map_name"), "chk_sha256": report.get("chk_sha256"),
        "frame_count": report.get("frame_count"), "first_leaver_outcome": outcome,
        "unit_orders": observations, "outcome_basis": "first_leaver_heuristic",
    }
    return extracted, errors


def aggregate_orders(records: list[dict]) -> dict:
    """Observational order statistics, grouped by mod variant."""
    per_variant: dict[str, dict] = {}
    for r in records:
        if not r.get("replay_probe_consistent", False):
            continue
        obs = r.get("replay_observations")
        if not isinstance(obs, dict):
            continue
        variant = r["manifest"]["variant"]
        v = per_variant.setdefault(variant, {u: {"observed_games": 0, "games_with_order": 0,
            "total_issued_orders": 0, "first_order_frames": []} for u in SUPPORTED_UNITS})
        for unit in SUPPORTED_UNITS:
            x = obs.get("unit_orders", {}).get(unit)
            if not isinstance(x, dict):
                continue
            row = v[unit]
            row["observed_games"] += 1
            row["total_issued_orders"] += x["issued_orders"]
            if x["issued_orders"]:
                row["games_with_order"] += 1
                row["first_order_frames"].append(x["first_order_frame"])
    return {variant: {unit: {
        "observed_games": d["observed_games"],
        "games_with_order": d["games_with_order"],
        "adoption_fraction": (d["games_with_order"] / d["observed_games"] if d["observed_games"] else None),
        "total_issued_orders": d["total_issued_orders"],
        "median_first_order_frame_when_used": median(d["first_order_frames"]) if d["first_order_frames"] else None,
    } for unit, d in items.items()} for variant, items in per_variant.items()}
