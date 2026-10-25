"""Read-only DAT decoder: fill all legacy Brood War static tables from owned data.

Layouts are transcribed as column schemas from OpenBW/data_loading.h; no
Blizzard assets or OpenBW source code are copied. No remote I/O. Supports
vanilla 1.16.1 table sizes, rejects expansions or malformed input.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct

NU = 228
NW = 130
NF = 209
NUP = 61
NT = 44

# (field, element format, number of rows, starting ID, values per row)
def c(name, fmt, n, start=0, components=1):
    return name, fmt, n, start, components

UNIT_COLUMNS = [
    c("flingy_id","B",NU), c("turret_unit","H",NU),
    c("subunit_2","H",NU), c("infestation_unit","H",96,106),
    c("construction_animation","I",NU),
    c("unit_direction","B",NU), c("has_shield","B",NU),
    c("shield_points","H",NU), c("hitpoints_raw","i",NU),
    c("elevation","B",NU), c("unknown1","B",NU),
    c("sublabel","B",NU), c("computer_ai_idle","B",NU),
    c("human_ai_idle","B",NU), c("return_to_idle","B",NU),
    c("attack_unit","B",NU), c("attack_move","B",NU),
    c("ground_weapon_id","B",NU), c("max_ground_hits","B",NU),
    c("air_weapon_id","B",NU), c("max_air_hits","B",NU),
    c("ai_internal","B",NU), c("flags","I",NU),
    c("target_acquisition_range","B",NU), c("sight_range","B",NU),
    c("armor_upgrade","B",NU), c("unit_size","B",NU),
    c("armor","B",NU), c("right_click_action","B",NU),
    c("ready_sound","H",106),
    c("first_what_sound","H",NU), c("last_what_sound","H",NU),
    c("first_pissed_sound","H",106), c("last_pissed_sound","H",106),
    c("first_yes_sound","H",106), c("last_yes_sound","H",106),
    c("placement_size","h",NU,0,2), c("addon_position","h",96,106,2),
    c("dimensions","h",NU,0,4),
    c("portrait","H",NU), c("mineral_cost","H",NU),
    c("gas_cost","H",NU), c("build_time","H",NU),
    c("unknown2","H",NU), c("group_flags","B",NU),
    c("supply_provided_raw","B",NU), c("supply_required_raw","B",NU),
    c("space_required","B",NU), c("space_provided","B",NU),
    c("build_score","H",NU), c("destroy_score","H",NU),
    c("unit_map_string_index","H",NU), c("is_broodwar","B",NU),
    c("staredit_flags","H",NU),
]
WEAPON_COLUMNS = [
    c("label","H",NW),c("flingy_id","I",NW),c("unused","B",NW),
    c("target_flags","H",NW),c("min_range","I",NW),c("max_range","I",NW),
    c("damage_upgrade","B",NW),c("damage_type","B",NW),
    c("bullet_type","B",NW),c("lifetime","B",NW),c("hit_type","B",NW),
    c("inner_splash_radius","H",NW),c("medium_splash_radius","H",NW),
    c("outer_splash_radius","H",NW),c("damage_amount","H",NW),
    c("damage_bonus","H",NW),c("cooldown","B",NW),c("bullet_count","B",NW),
    c("attack_angle_raw","B",NW),c("bullet_heading_offset","b",NW),
    c("forward_offset","b",NW),c("upward_offset","b",NW),
    c("target_error_message","H",NW),c("icon","H",NW)
]
FLINGY_COLUMNS = [
    c("sprite_id","H",NF),c("top_speed_raw","i",NF),
    c("acceleration_raw","h",NF),c("halt_distance_raw","i",NF),
    c("turn_rate_raw","B",NF),c("unused","B",NF),
    c("movement_type","B",NF)
]
UPGRADE_COLUMNS = [
    c(n,"H",NUP) for n in ("mineral_cost_base","mineral_cost_factor",
    "gas_cost_base","gas_cost_factor","time_cost_base","time_cost_factor",
    "unknown","icon","label")
] + [c(n,"B",NUP) for n in ("race","max_level","is_broodwar")]
TECH_COLUMNS = [
    c(n,"H",NT) for n in ("mineral_cost","gas_cost","research_time","energy_cost")
] + [c("unknown","I",NT),c("icon","H",NT),c("label","H",NT),
     c("race","B",NT),c("flags","H",NT)]
TABLES = {"units.dat":(NU,UNIT_COLUMNS), "weapons.dat":(NW,WEAPON_COLUMNS),
          "flingy.dat":(NF,FLINGY_COLUMNS), "upgrades.dat":(NUP,UPGRADE_COLUMNS),
          "techdata.dat":(NT,TECH_COLUMNS)}


def byte_length(columns) -> int:
    return sum(struct.calcsize("<"+fmt)*count*components
               for _,fmt,count,_,components in columns)


def decode(data: bytes, count: int, columns: list) -> list[dict]:
    if len(data) != byte_length(columns):
        raise ValueError(f"expected {byte_length(columns)} bytes, got {len(data)}")
    rows = [{"id":i} for i in range(count)]
    offset = 0
    for name,fmt,n,start,components in columns:
        size = struct.calcsize("<"+fmt)
        for j in range(n):
            values = []
            for component in range(components):
                value = struct.unpack_from("<"+fmt, data, offset+size*(j*components+component))[0]
                values.append(value)
            rows[j+start][name] = values[0] if components == 1 else values
        offset += size*n*components
    return rows


def extract(paths: dict[str, Path]) -> dict:
    if not set(("units.dat","weapons.dat","flingy.dat")) <= set(paths):
        raise ValueError("units.dat, weapons.dat and flingy.dat are mandatory")
    tables = {}
    provenance = {}
    for name, path in paths.items():
        if name not in TABLES:
            raise ValueError(f"unsupported DAT table: {name}")
        data = Path(path).read_bytes()
        n, layout = TABLES[name]
        tables[name] = decode(data, n, layout)
        provenance[name] = {"sha256":hashlib.sha256(data).hexdigest(),
                            "bytes":len(data), "entries":n,
                            "source":"user_supplied_owned_DAT",
                            "extracted":True}
    # Derived numeric fields; raw columns remain available for auditing.
    for row in tables["flingy.dat"]:
        row["top_speed_px_per_frame"] = row["top_speed_raw"] / 256
    for row in tables["units.dat"]:
        row["hp"] = row["hitpoints_raw"] / 256
        row["supply_required"] = row["supply_required_raw"] / 2
        row["supply_provided"] = row["supply_provided_raw"] / 2
        fid = row["flingy_id"]
        row["movement"] = tables["flingy.dat"][fid] if fid < NF else None
        w = row["air_weapon_id"]
        row["air_weapon"] = tables["weapons.dat"][w] if w < NW else None
        w = row["ground_weapon_id"]
        row["ground_weapon"] = tables["weapons.dat"][w] if w < NW else None
    return {"schema":1,"scope":"all_vanilla_legacy_DAT_rows",
            "provenance":provenance,"tables":tables,
            "important_limitations":["only_numeric_static_dat_fields",
                                    "game_engine_and_animations_can_modify_stats",
                                    "no_live_state_or_matchoutcomes"]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in TABLES:
        p.add_argument("--"+k.replace(".dat",""), type=Path, required=k in ("units.dat","weapons.dat","flingy.dat"))
    p.add_argument("--output", type=Path, required=True)
    args=p.parse_args()
    paths={k:getattr(args,k.split(".")[0]) for k in TABLES if getattr(args,k.split(".")[0]) is not None}
    result=extract(paths)
    with args.output.open("x",encoding="utf-8") as out:
        json.dump(result,out,indent=2,sort_keys=True)
        out.write("\n")
    print(json.dumps({"unit_rows":len(result["tables"]["units.dat"]),
                      "weapon_rows":len(result["tables"]["weapons.dat"]),
                      "flingy_rows":len(result["tables"]["flingy.dat"]),
                      "source_sha256":{k:v["sha256"] for k,v in result["provenance"].items()}},
                     indent=2))


if __name__ == "__main__":
    main()
