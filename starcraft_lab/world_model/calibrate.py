"""Tie public priors to private, checksum-attributed static DAT observations.

This is a parameter fill operation, not statistical model calibration and not
a game-state emulator. User-owned files and source hashes remain private.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
from .ontology import load_catalog

_DAMAGE={0:None,1:"explosive",2:"concussive",3:"normal",4:"ignore_armor"}


def overlay(base:dict, extracted:dict, *, strict_vanilla:bool=True)->dict:
    if extracted.get("schema")!=1 or extracted.get("scope")!="all_vanilla_legacy_DAT_rows":
        raise ValueError("invalid static DAT extraction")
    try:
        units=extracted["tables"]["units.dat"]
        weapons=extracted["tables"]["weapons.dat"]
        flingies=extracted["tables"]["flingy.dat"]
        provenance=extracted["provenance"]
    except (KeyError,TypeError) as ex:
        raise ValueError("missing required extracted data") from ex
    if not (len(units)==228 and len(weapons)==130 and len(flingies)==209):
        raise ValueError("unexpected game DAT universe sizes")
    for required in ("units.dat","weapons.dat","flingy.dat"):
        h=provenance.get(required,{}).get("sha256")
        if not isinstance(h,str) or len(h)!=64:
            raise ValueError(f"missing {required} provenance hash")
    original=base.get("units")
    if not isinstance(original,dict):
        raise ValueError("base catalog missing units")
    populated=deepcopy(base)
    verified=0
    for key,data in populated["units"].items():
        row=units[data["unit_id"]]
        if row["id"]!=data["unit_id"]:
            raise ValueError(f"unit ID mismatch: {key}")
        f_id=row["flingy_id"]
        if not isinstance(f_id,int) or not 0<=f_id<209:
            raise ValueError(f"{key}: invalid flingy ID")
        f=flingies[f_id]
        # Fill scalar stats. The DAT's legacy supply value is in half-supply.
        values={
            "hp":row["hitpoints_raw"]/256,
            "shields":row["shield_points"],
            "armor":row["armor"],
            "speed_px_per_frame":f["top_speed_raw"]/256,
        }
        if any(x<0 for x in values.values()):
            raise ValueError(f"{key}: negative extracted static stat")
        for k,v in values.items():
            data[k]=v
            verified+=1
        for k,target in (("mineral_cost","minerals"),("gas_cost","gas"),("supply_required_raw","supply")):
            data["cost"][target]=(row[k]/2 if k=="supply_required_raw" else row[k])
            verified+=1
        for domain,wname in (("air","air_weapon_id"),("ground","ground_weapon_id")):
            wid=row[wname]
            old=data["weapons"].get(domain)
            if old is None:
                continue
            if not isinstance(wid,int) or not 0<=wid<130:
                raise ValueError(f"{key}: missing required {domain} weapon")
            observed=weapons[wid]
            dtype=_DAMAGE.get(observed["damage_type"])
            if dtype is None:
                raise ValueError(f"{key}: invalid damage type")
            if observed["damage_amount"]<=0 or observed["cooldown"]<=0:
                raise ValueError(f"{key}: invalid {domain} damage/cooldown")
            old["damage"]=observed["damage_amount"]
            old["cooldown_frames"]=observed["cooldown"]
            old["range_tiles"]=observed["max_range"]/32
            old["damage_type"]=dtype
            old["weapon_id"]=wid
            verified+=4
        data["model_status"]="local_DAT_static"
        data["static_source_hashes"]={k:provenance[k]["sha256"]
                                       for k in ("units.dat","weapons.dat","flingy.dat")}
    dev=populated["units"]["devourer"]
    if strict_vanilla and (
        abs(dev["speed_px_per_frame"]-5)>0.02
        or dev["weapons"]["air"]["cooldown_frames"]!=100
    ):
        raise ValueError("expected original 5.00 px/frame and 100-frame Devourer; supplied DAT may be modded")
    populated["overlay_metadata"]={
        "schema":1,"status":"static_parameters_filled_from_owned_DAT",
        "filled_scalar_fields":verified,
        "data_universes":{"units":228,"weapons":130,"flingy":209},
        "source_sha256":{k:provenance[k]["sha256"] for k in ("units.dat","weapons.dat","flingy.dat")},
        "not_measured":["actual attacks","micro actions","projectile collision","ability timers","map physics"],
    }
    return populated


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extracted",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    data=json.loads(args.extracted.read_text(encoding="utf-8"))
    result=overlay(load_catalog(),data)
    with args.output.open("x",encoding="utf-8") as out:
        json.dump(result,out,indent=2,sort_keys=True)
        out.write("\n")
    print(json.dumps(result["overlay_metadata"],indent=2))


if __name__=="__main__":
    main()
