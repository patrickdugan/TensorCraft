"""Counterfactual A-vs-B matrix, factorial effects, and experiment selection.

Preserves three separations:
  official/reference inputs ≠ estimated dynamical effects ≠ observed replays.
For every scenario, apply common-random-number trials to four interventions.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .combat import VARIANTS, simulate_batch, load_catalog

def load_scenarios(path:Path|None=None)->list[dict]:
    path=path or Path(__file__).with_name("scenarios.json")
    doc=json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema")!=1 or not isinstance(doc.get("scenarios"),list):
        raise ValueError("invalid scenario file")
    ids=set()
    for s in doc["scenarios"]:
        if s.get("id") in ids or not all(k in s for k in ("id","A","B")):
            raise ValueError("duplicated or incomplete scenario")
        ids.add(s["id"])
    return doc["scenarios"]


def _factorial(rows:dict) -> dict:
    y={k:v["a_favorable_fraction"] for k,v in rows.items()}
    c=y["control"]
    s=y["devourer_speed_550"]
    d=y["devourer_cooldown_90"]
    b=y["devourer_speed_550_cooldown_90"]
    return {
        "speed_effect_at_baseline":round(s-c,4),
        "cooldown_effect_at_baseline":round(d-c,4),
        "combined_effect":round(b-c,4),
        "speed_main_effect":round(((s-c)+(b-d))/2,4),
        "cooldown_main_effect":round(((d-c)+(b-s))/2,4),
        "interaction":round(b-s-d+c,4),
        "effect_units":"fraction of surrogate trials favorable to A; NOT empirical win-rate"
    }


def _uncertainty_cases(scenario:dict) -> list[tuple[str,dict]]:
    return [
        ("sparse_poor_focus",{"hit_chance":0.75,"focus":0.35,
                              "kite":0.77,"spore_decay_frames":100}),
        ("nominal",{}),
        ("clustered_high_focus",{"hit_chance":0.99,"focus":0.88,
                                 "kite":0.12,"spore_decay_frames":520}),
    ]


def estimate_all(scenarios:list[dict]|None=None,trials:int=40,seed:int=20261025,
                 sensitivity_trials:int=8,catalog_path:Path|None=None)->dict:
    if scenarios is None:
        scenarios=load_scenarios()
    if type(sensitivity_trials) is not int or not 1<=sensitivity_trials<=1000:
        raise ValueError("sensitivity_trials 1..1000 required")
    catalogue=load_catalog(catalog_path)
    rows=[]
    for index,scenario in enumerate(scenarios):
        scid=scenario["id"]
        first_seed=seed+index*1000003
        predictions={
            variant:simulate_batch(scenario,variant,trials,first_seed,catalog=catalogue["units"])
            for variant in VARIANTS
        }
        effects=_factorial(predictions)
        uncertainty={
            label:simulate_batch(scenario,"devourer_speed_550_cooldown_90",
                                 sensitivity_trials,first_seed+202,
                                 overrides=override,catalog=catalogue["units"])["a_favorable_fraction"]
            for label,override in _uncertainty_cases(scenario)
        }
        with_spores=predictions["devourer_speed_550_cooldown_90"]
        no_spores=simulate_batch(scenario,"devourer_speed_550_cooldown_90",
                                 trials,first_seed,
                                 ablate_spores=True,catalog=catalogue["units"])
        spore_uplift=round(with_spores["a_favorable_fraction"]-no_spores["a_favorable_fraction"],4)
        p=with_spores["a_favorable_fraction"]
        disagreement=max(uncertainty.values())-min(uncertainty.values())
        interaction=abs(effects["interaction"])
        boundary=max(0,1-2*abs(p-0.5))
        # Heuristic acquisition function: prioritize scenarios near the
        # modeled decision boundary, that flip across micro priors, and
        # that might expose synergy (parameter interaction/spore ablation).
        priority=round(0.35*boundary+0.30*disagreement+
                       0.25*interaction+0.10*abs(spore_uplift),4)
        rows.append({
            "scenario_id":scid,"question":scenario.get("question"),
            "A":scenario["A"],"B":scenario["B"],
            "assumptions":scenario.get("assumptions",[]),
            "predictions":predictions,"factorial":effects,
            "sensitivity_surrogate_favorable_frequency":uncertainty,
            "spore_ablation":{
                "with_spore_model":with_spores["a_favorable_fraction"],
                "without_spores":no_spores["a_favorable_fraction"],
                "surrogate_uplift":spore_uplift,
                "note":"Same initial Monte Carlo seeds with dynamics diverging after intervention; uncalibrated mechanistic hypothesis"
            },
            "experiment_priority":priority,
            "priority_components":{"boundary":round(boundary,4),
                "micro_prior_disagreement":round(disagreement,4),
                "factorial_interaction_abs":round(interaction,4)},
            "claims_status":"guestimate_from_uncalibrated_stochastic_surrogate",
        })
    return {
        "schema":1,"model":"TensorCraft tactical surrogate v0",
        "seed":seed,"trials_per_variant":trials,
        "sensitivity_trials":sensitivity_trials,
        "fidelity":"low / uncalibrated",
        "sampled_matches_are_not_starcraft_matches":True,
        "scenario_results":rows,
        "active_learning_queue":sorted([
            {"scenario_id":r["scenario_id"],"priority":r["experiment_priority"],
             "reason":"discriminates coupled speed, cadence and acid-spore synergy"}
            for r in rows],key=lambda x:(-x["priority"],x["scenario_id"])),
        "calibration_targets":[
            "measured_devourer_attack_frames",
            "observed_devourer_speed_after_turn",
            "acid_spore_target_and_neighbor_application",
            "spore_decay_timing",
            "mutalisk_bounce_damage_by_target",
            "Corsair_and_Valkyrie_splash_geometry",
            "actual_retreat_micro_policy",
            "army_cost_and_tech_timing_in_replays",
            "replay_referee_confirmed_outcome"
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenarios",type=Path)
    p.add_argument("--trials",type=int,default=40)
    p.add_argument("--seed",type=int,default=20261025)
    p.add_argument("--sensitivity-trials",type=int,default=8)
    p.add_argument("--output",type=Path)
    p.add_argument("--catalog",type=Path,help="optional static model catalog overlaid from owned DAT")
    args=p.parse_args(argv)
    rows=load_scenarios(args.scenarios)
    result=estimate_all(rows,args.trials,args.seed,args.sensitivity_trials,args.catalog)
    encoded=json.dumps(result,indent=2,sort_keys=True)+"\n"
    if args.output:
        with args.output.open("x",encoding="utf-8") as out:
            out.write(encoded)
    else:
        print(encoded)


if __name__=="__main__":
    main()
