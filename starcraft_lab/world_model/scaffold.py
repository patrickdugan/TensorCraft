"""Generate empty, provenance-aware research tables for all legacy DAT IDs.

These files are intended for local human/agent annotation or joining against
the owned-game DAT export. Unknown values remain empty, never guessed zeros.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
from .ontology import GROUP_FIELDS, load_catalog, registry, relation_sets
from .experiments import load_scenarios
from .combat import VARIANTS


def _columns(groups:list[str])->list[str]:
    return [f.split(":")[0] for group in groups for f in GROUP_FIELDS[group]]


def generate(destination:Path, catalog:dict|None=None)->dict:
    catalog=catalog or load_catalog()
    dest=Path(destination).expanduser().resolve()
    dest.mkdir(parents=True,exist_ok=False)
    units=catalog["units"]
    by_id={v["unit_id"]:(key,v) for key,v in units.items()}
    unit_cols=["id","known_name","status","stat_source","confidence",
               "race","domain","size","hp","shields","base_armor",
               "minerals","gas","supply","top_speed",
               "air_weapon_id","ground_weapon_id","notes"]
    with (dest/"units.csv").open("x",encoding="utf-8",newline="") as fp:
        w=csv.DictWriter(fp,fieldnames=unit_cols)
        w.writeheader()
        for i in range(228):
            row={"id":i,"status":"missing","confidence":"","notes":""}
            if i in by_id:
                name,item=by_id[i]
                row.update({"known_name":name,"status":"reference_prior",
                            "stat_source":item.get("source_stats",""),
                            "confidence":"secondary_mixed",
                            "race":item["race"],"domain":item["domain"],
                            "size":item["size"],"hp":item["hp"],
                            "shields":item["shields"],"base_armor":item["armor"],
                            "minerals":item["cost"]["minerals"],
                            "gas":item["cost"]["gas"],
                            "supply":item["cost"]["supply"],
                            "top_speed":item["speed_px_per_frame"],
                            "notes":"Named public seed; obtain true weapon IDs from private DAT"})
            w.writerow(row)
    weapon_cols=["weapon_id","owner_unit_ids","status",
                 *(_columns(["weapon_dat"])),
                 "source_sha256","confidence","notes"]
    # Discard any duplicate column name and keep the important relational ID.
    weapon_cols=list(dict.fromkeys(weapon_cols))
    with (dest/"weapons.csv").open("x",encoding="utf-8",newline="") as fp:
        w=csv.DictWriter(fp,fieldnames=weapon_cols)
        w.writeheader()
        for i in range(130):
            w.writerow({"weapon_id":i,"status":"missing",
                        "notes":"Populate via owned weapons.dat"})
    field_doc=registry()
    (dest/"variable_registry.json").write_text(json.dumps(field_doc,indent=2,sort_keys=True)+"\n")
    relation_doc={}
    for key,values in relation_sets(catalog).items():
        relation_doc[key]=[list(v) if isinstance(v,tuple) else v
                           for v in sorted(values)]
    (dest/"seed_relations.json").write_text(json.dumps({
        "schema":1,"known_seed_only":True,"relations":relation_doc,
        "status":"public_seed_relations_not_complete_228_unit_graph"},indent=2)+"\n")
    match_cols=["scenario_id","variant","map_hash","engine_hash",
                "bot_model_hash","policy_id","composition_A","composition_B",
                "requested_spawn","observed_spawn","seed_applied",
                "winner_referee_confirmed","replay_sha256",
                "first_devourer_order_frame","actual_devourer_count",
                "spore_applications","unit_losses_A","unit_losses_B",
                "net_resource_trade","game_frames","observation_source","notes"]
    with (dest/"experiments.csv").open("x",encoding="utf-8",newline="") as fp:
        w=csv.DictWriter(fp,fieldnames=match_cols)
        w.writeheader()
        for scenario in load_scenarios():
            for variant in VARIANTS:
                w.writerow({"scenario_id":scenario["id"],"variant":variant,
                            "composition_A":json.dumps(scenario["A"],sort_keys=True),
                            "composition_B":json.dumps(scenario["B"],sort_keys=True),
                            "observation_source":"missing"})
    manifest={
        "schema":1,"generated_with":"world_model.scaffold",
        "files":{"units.csv":228,"weapons.csv":130,
                 "experiments.csv":len(load_scenarios())*len(VARIANTS),
                 "seed_relations.json":"known_seed_subgraph_only",
                 "variable_registry.json":field_doc["total_fields"]},
        "unfilled":True,"private_game_data_included":False,
        "instruction":"Use owned DAT extraction and actual game traces to fill missing cells; preserve provenance"
    }
    (dest/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    return manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()
    print(json.dumps(generate(args.output_dir),indent=2))


if __name__=="__main__":
    main()
