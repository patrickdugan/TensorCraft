"""Set-theoretic Brood War ontology and fillable variable catalogue.

U = unit types, W = weapons, A = abilities, T = tech, R = races, D = domains.
Relations are subsets of Cartesian products, not numerical win-rate claims.
A partial frame state assigns values to some coordinates of the state space.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

RACES = frozenset(("Zerg", "Terran", "Protoss"))
DOMAINS = frozenset(("air", "ground"))
SIZES = frozenset(("small", "medium", "large"))
DAMAGE_TYPES = frozenset(("normal", "explosive", "concussive", "ignore_armor"))
PROVENANCE = frozenset(("official_reference", "extracted_local_DAT", "observed_replay",
                        "observed_live", "inferred", "model_prior", "missing"))

# Fields enumerate what must eventually be captured for faithful simulation.
# A registry entry provides domain, scope, unit and current measurement route.
GROUP_FIELDS = {
  "identity": ("unit_id:int", "unit_name:str", "race:enum", "is_hero:bool",
               "is_building:bool", "domain:enum", "biological:bool",
               "mechanical:bool", "size:enum", "unit_role:enum"),
  "unit_dat": ("hp:hp", "shields:hp", "base_armor:hp", "shield_armor:hp",
               "minerals:resource", "gas:resource", "supply:supply",
               "production_frames:frames", "sight:tiles", "acquisition_range:tiles",
               "supply_provided:supply", "mineral_repair_rate:rate",
               "hp_regen:rate", "shield_regen:rate", "max_energy:energy",
               "starting_energy:energy", "detector_range:tiles",
               "cargo_capacity:int", "cargo_size:int", "energy_regen:rate",
               "morph_source_id:int", "morph_cost_minerals:resource",
               "morph_cost_gas:resource", "build_requirements:ids",
               "research_requirements:ids"),
  "movement_flingy": ("top_speed:px_per_frame", "acceleration:raw",
                      "halt_distance:px", "turn_rate:angle_per_frame",
                      "movement_type:enum", "collision_dimensions:px",
                      "unit_radius:px", "pathability:enum"),
  "weapon_dat": ("weapon_id:int", "damage:hp", "damage_upgrade_delta:hp",
                 "damage_type:enum", "cooldown:frames", "max_range:px",
                 "min_range:px", "target_flags:bitset", "weapon_behavior:enum",
                 "projectile_speed:px_per_frame", "projectile_lifetime:frames",
                 "attack_windup:frames", "shots_per_attack:int",
                 "inner_splash_radius:px", "medium_splash_radius:px",
                 "outer_splash_radius:px", "bullet_count:int",
                 "attack_angle:angle", "heading_offset:angle", "bounce_damage:hp",
                 "bounce_radius:px", "friendly_fire:bool"),
  "ability_status": ("acid_spores:int", "acid_spore_decay:frames",
                     "plague_duration:frames", "ensnare_duration:frames",
                     "dark_swarm_area:area", "stasis_duration:frames",
                     "irradiate_duration:frames", "cloak_active:bool",
                     "detected_by_players:ids", "matrix_hp:hp", "stim_active:bool",
                     "siege_mode_active:bool", "burrowed:bool", "hallucinated:bool"),
  "frame_state": ("frame:int", "owner:int", "unit_type:int", "x:px",
                  "y:px", "vx:px_per_frame", "vy:px_per_frame", "facing:angle",
                  "hp_remaining:hp", "shields_remaining:hp", "energy:energy",
                  "weapon_cooldown_remaining:frames", "orders:ids",
                  "selected_units:ids", "target_unit_id:int", "target_x:px",
                  "target_y:px", "is_visible:bool", "is_detected:bool",
                  "is_moving:bool", "loaded_cargo:ids", "kills:int",
                  "unit_production_queue:ids", "upgrade_levels:map"),
  "map_environment": ("map_sha256:str", "tile_x:int", "tile_y:int",
                      "height_level:enum", "walkable:bool",
                      "air_accessible:bool", "vision_mask:bitset",
                      "creep:bool", "power:bool", "terrain_region:int",
                      "chokepoint_width:px", "ramp_access:bool",
                      "neutral_obstructions:ids", "resource_node:resource",
                      "base_locations:positions", "spawn_locations:positions",
                      "travel_distance:px", "buildable:bool"),
  "economy_tech": ("minerals_bank:resource", "gas_bank:resource",
                   "minerals_income:rate", "gas_income:rate",
                   "worker_allocation:map", "active_larva:int",
                   "larva_spawn_timer:frames", "buildings:ids",
                   "research_state:map", "upgrade_state:map",
                   "supply_used:supply", "supply_cap:supply",
                   "production_capacity:rate", "tech_unlock_time:frames",
                   "morph_opportunity_cost:resource", "opponent_tech_belief:map"),
  "agent_policy": ("observation_window:frames", "agent_step_period:frames",
                   "action_budget:int", "latency:frames", "apm:rate",
                   "effective_apm:rate", "focus_fire_probability:probability",
                   "kite_preference:probability", "retreat_threshold:probability",
                   "formation_spread:px", "scouting_priority:probability",
                   "economy_arm:enum", "bandit_initial_hash:str",
                   "model_hash:str", "observation_fog_mask:bitset",
                   "random_seed:int", "agent_identity:str"),
  "outcomes_evidence": ("variant_id:str", "opponent_id:str",
                        "unit_survival_frames:frames", "damage_dealt:hp",
                        "damage_taken:hp", "spore_applications:int",
                        "overkill:hp", "focus_fire_fraction:probability",
                        "army_value_retained:resource",
                        "trade_efficiency:ratio", "engagement_start:frames",
                        "engagement_duration:frames", "winner:enum",
                        "win_estimate:probability", "replay_sha256:str",
                        "source_confidence:probability", "missingness:enum",
                        "measured_vs_predicted:enum",
                        "selection_censoring:bool", "calibration_error:ratio"),
}
ROUTES = {
 "identity":"units.dat + unit-name registry", "unit_dat":"owned units.dat / BWAPI",
 "movement_flingy":"owned flingy.dat + live positions",
 "weapon_dat":"owned weapons.dat + live attack events",
 "ability_status":"live BWAPI frame observations / engine validation",
 "frame_state":"BWAPI snapshots, visibility-filtered for bot perspective",
 "map_environment":"owned map CHK + engine pathing and height",
 "economy_tech":"owned replay orders + live economy state",
 "agent_policy":"Pluto manifest, logs, actions and opening-bandit snapshots",
 "outcomes_evidence":"refereed replay outcomes and instrumented engagements",
}


@dataclass(frozen=True)
class Field:
    name: str
    group: str
    datatype: str
    observation_route: str
    nullable: bool = True


def fields() -> list[Field]:
    result = []
    for group, names in GROUP_FIELDS.items():
        for spec in names:
            name, dtype = spec.split(":", 1)
            result.append(Field(name, group, dtype, ROUTES[group]))
    return result


def registry() -> dict:
    return {
        "schema": 1,
        "formal": {
            "universes": {"U":"unit types", "W":"weapons", "R":"races",
                          "D":"target domains", "T":"technologies",
                          "A":"abilities", "P":"players", "M":"map cells"},
            "relations": {
                "belongs_to":"U x R", "attacks":"U x D", "uses_weapon":"U x W",
                "morphs_from":"U x U", "requires_tech":"U x T",
                "supports":"U x U", "can_observe":"P x U x Frame",
                "path_reaches":"U x M x M x Frame", "counters":"U x U x Context",
            },
            "state":"S_t in product of frame_state, economy_tech, map_environment, ability_status",
            "partial_observation":"O_{p,t} = projection(S_t, visibility_mask(p,t))",
            "transition":"S_{t+1} = F(S_t, joint_orders_t, random_state_t, mod_parameters)",
            "intervention":"do(speed(Devourer)=5.50, cooldown(Devourer)=90)",
        },
        "total_fields": len(fields()),
        "fields": [{"name":f.name,"group":f.group,"dtype":f.datatype,
                    "nullable":f.nullable,"route":f.observation_route}
                   for f in fields()],
        "provenance_levels": sorted(PROVENANCE),
    }


def load_catalog(path: Path | None = None) -> dict:
    file = path if path is not None else Path(__file__).with_name("catalog.json")
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("units"), dict):
        raise ValueError("invalid world model catalog")
    for name, item in data["units"].items():
        if item["race"] not in RACES or item["domain"] not in DOMAINS or item["size"] not in SIZES:
            raise ValueError(f"{name}: invalid race, domain or size")
        if item["hp"] < 0 or item["shields"] < 0 or item["armor"] < 0 or item["speed_px_per_frame"] < 0:
            raise ValueError(f"{name}: invalid numeric value")
        for weapon in item["weapons"].values():
            if weapon is not None and weapon["damage_type"] not in DAMAGE_TYPES:
                raise ValueError(f"{name}: invalid weapon damage type")
    ids = [u["unit_id"] for u in data["units"].values()]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicated unit IDs")
    return data


def relation_sets(catalog: dict) -> dict[str, frozenset]:
    """Materialize finite predicates; unknown entries stay outside the relation."""
    units = catalog["units"]
    return {
        "U": frozenset(units),
        "R": RACES,
        "D": DOMAINS,
        "belongs_to": frozenset((k,v["race"]) for k,v in units.items()),
        "attacks": frozenset((k,d) for k,v in units.items()
                              for d,w in v["weapons"].items() if w is not None),
        "morphs_from": frozenset((k,"mutalisk") for k,v in units.items()
                                 if "morph_from_mutalisk" in v.get("special", [])),
        "requires_tech": frozenset((k,v["morph_incremental"]["requires"])
                                   for k,v in units.items() if "morph_incremental" in v),
        "detectors": frozenset(k for k,v in units.items() if "detector" in v.get("special", [])),
    }


def attackers(catalog: dict, targets: Iterable[str]) -> frozenset[str]:
    """Set algebra: viable attackers = inventory intersection target-domain attackers."""
    relations = relation_sets(catalog)
    wanted = frozenset(targets)
    unknown = wanted - relations["U"]
    if unknown:
        raise ValueError(f"unknown unit keys: {sorted(unknown)}")
    target_domains = frozenset(catalog["units"][k]["domain"] for k in wanted)
    return frozenset(k for k,d in relations["attacks"] if d in target_domains)


def fill_template() -> dict:
    """Empty structured observations rather than fabricated complete stats."""
    return {"schema": 1, "unit_catalog_universe": 228,
            "weapon_catalog_universe": 130, "flingy_catalog_universe": 209,
            "upgrades_universe": 61, "tech_universe": 44,
            "fields": {f.name: {"value": None, "status":"missing", "source":None,
                                 "confidence":None, "dtype":f.datatype,
                                 "group":f.group} for f in fields()},
            "note": "A template for one observation/parameter bundle; all values initially unknown"}


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("registry","relations","template"))
    p.add_argument("--output", type=Path)
    args=p.parse_args()
    data = (registry() if args.command == "registry" else
            fill_template() if args.command == "template" else
            {k:sorted(map(list,v)) if k in ("attacks","belongs_to","morphs_from","requires_tech") else sorted(v)
             for k,v in relation_sets(load_catalog()).items()})
    output=json.dumps(data, indent=2, sort_keys=True)+"\n"
    if args.output:
        args.output.write_text(output,encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
