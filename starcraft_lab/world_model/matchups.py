"""All-pairs 1v1 exchange priors across the public unit seed catalog.

A bounded, inexpensive heuristic: capability and nominal time-to-kill.
It intentionally omits group splash, spore stacks, movement, tech timing,
spells, energy and interceptors. Never treat scores as observed win rates.
"""
from __future__ import annotations
import argparse
import csv
import json
from math import ceil, log
from pathlib import Path
from .ontology import load_catalog
from .combat import effective_hp_damage

UNSUPPORTED = {"interceptor_carrier_unmodeled", "siege_mode_weapon_unmodeled"}
GAS_SHADOW=1.5


def raw_weapon_stats(weapon:dict, target:dict, attacker_name:str)->dict:
    if weapon is None:
        return {"capable":False, "damage_per_volley":0,"ttk_frames":None}
    hits=int(weapon.get("hits_per_attack",1))
    if attacker_name=="goliath":
        each=weapon["damage"]/max(hits,1)
    else:
        each=weapon["damage"]
    hp_hit=sum(effective_hp_damage(each,target["armor"],target["size"],
                                   weapon["damage_type"]) for _ in range(hits))
    shield_hit=sum(each for _ in range(hits))
    if hp_hit<=0 or shield_hit<=0:
        return {"capable":False,"damage_per_volley":0,"ttk_frames":None}
    shield_volley_count=ceil(target["shields"]/shield_hit) if target["shields"]>0 else 0
    hp_volley_count=ceil(target["hp"]/hp_hit) if target["hp"]>0 else 0
    total=max(1,shield_volley_count+hp_volley_count)
    # Half cooldown of the first shot is an explicit setup-time heuristic.
    ttk=(total-0.5)*weapon["cooldown_frames"]
    return {"capable":True,"damage_per_volley":round(hp_hit,3),
            "ttk_frames":round(ttk,2),
            "effective_nominal_dps":round(hp_hit*23.81/weapon["cooldown_frames"],3),
            "range_tiles":weapon["range_tiles"],
            "volley_count":total}


def matchup(a:str,b:str,catalog:dict,gas_shadow:float=GAS_SHADOW)->dict:
    units=catalog["units"]
    if a not in units or b not in units:
        raise ValueError("unknown unit type")
    x,y=units[a],units[b]
    aw=x["weapons"][y["domain"]]
    bw=y["weapons"][x["domain"]]
    ao=raw_weapon_stats(aw,y,a)
    bo=raw_weapon_stats(bw,x,b)
    if UNSUPPORTED.intersection(x.get("special",[])+y.get("special",[])):
        category="unsupported_special_attack_model"
        favored=None
        heuristic=None
    elif ao["capable"] and not bo["capable"]:
        category="one_sided_attack_A"
        favored="A"
        heuristic=None
    elif bo["capable"] and not ao["capable"]:
        category="one_sided_attack_B"
        favored="B"
        heuristic=None
    elif not ao["capable"] and not bo["capable"]:
        category="mutual_inability"
        favored="none"
        heuristic=None
    else:
        ratio=log(bo["ttk_frames"]/ao["ttk_frames"])
        heuristic=max(0.08,min(0.92,0.5+0.18*ratio))
        category="nominal_two_sided_exchange"
        favored=("A" if heuristic>0.55 else "B" if heuristic<0.45 else "close")
    cost_a=x["cost"]["minerals"]+gas_shadow*x["cost"]["gas"]
    cost_b=y["cost"]["minerals"]+gas_shadow*y["cost"]["gas"]
    return {
        "A":a,"B":b,"race_A":x["race"],"race_B":y["race"],
        "capability_class":category,
        "A_can_attack":ao["capable"],"B_can_attack":bo["capable"],
        "A_ttk_B_frames":ao["ttk_frames"],"B_ttk_A_frames":bo["ttk_frames"],
        "A_direct_1v1_dps":ao.get("effective_nominal_dps"),
        "B_direct_1v1_dps":bo.get("effective_nominal_dps"),
        "nominal_favored_side":favored,
        "surrogate_favorable_fraction_A":round(heuristic,4) if heuristic is not None else None,
        "cost_weighted_A":cost_a,"cost_weighted_B":cost_b,
        "unmodeled":["micromanagement","motion_and_range","ability_status",
                     "actual_shield_regeneration","special_attack_mechanics"],
        "evidence_status":"reference_prior_heuristic_only",
    }


def matrix(catalog:dict|None=None)->dict:
    catalog=catalog or load_catalog()
    names=sorted(catalog["units"],key=lambda s:catalog["units"][s]["unit_id"])
    entries=[matchup(a,b,catalog) for a in names for b in names]
    return {
        "schema":1,"named_units":len(names),"entries":len(entries),
        "matrix":entries,
        "labels":{
            "one_sided_attack_A":"A can hit B and B lacks modeled direct counterfire",
            "one_sided_attack_B":"B can hit A and A lacks modeled direct counterfire",
            "mutual_inability":"neither possesses an applicable direct weapon",
            "unsupported_special_attack_model":"special attack not faithfully modeled",
            "nominal_two_sided_exchange":"both can shoot; rough ratio of nominal TTKs",
        },
        "warning":"heuristic is neither a simulated full-game result nor an empirically calibrated probability",
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog",type=Path)
    p.add_argument("--json",type=Path,required=True)
    p.add_argument("--csv",type=Path)
    args=p.parse_args()
    payload=matrix(load_catalog(args.catalog))
    with args.json.open("x",encoding="utf-8") as f:
        json.dump(payload,f,indent=2,sort_keys=True)
        f.write("\n")
    if args.csv:
        cols=["A","B","race_A","race_B","capability_class","A_ttk_B_frames",
              "B_ttk_A_frames","surrogate_favorable_fraction_A",
              "cost_weighted_A","cost_weighted_B","nominal_favored_side"]
        with args.csv.open("x",encoding="utf-8",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore")
            writer.writeheader()
            writer.writerows(payload["matrix"])
    print(json.dumps({"named_units":payload["named_units"],"matchups":payload["entries"],
                      "classes":{name:sum(x["capability_class"]==name for x in payload["matrix"])
                                 for name in payload["labels"]}},indent=2))


if __name__=="__main__":
    main()
