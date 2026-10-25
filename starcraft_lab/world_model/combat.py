"""Small, explicit stochastic combat surrogate for Brood War design questions.

NOT the StarCraft engine: omitted animation, pathfinding, projectile physics,
selection limits, spells, cloak/detection, repair, real splash geometry and AI.
Simulated frequencies are *uncalibrated priors*, never measured win rates.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import random
from collections import Counter

from .ontology import load_catalog

FPF = 23.81
SIZES = {
    "normal": {"small":1, "medium":1, "large":1},
    "explosive":{"small":0.5, "medium":0.75, "large":1},
    "concussive":{"small":1, "medium":0.5, "large":0.25},
    "ignore_armor":{"small":1, "medium":1, "large":1},
}
VARIANTS = {
    "control":(5.0, 100),
    "devourer_speed_550":(5.5, 100),
    "devourer_cooldown_90":(5.0, 90),
    "devourer_speed_550_cooldown_90":(5.5, 90),
}


@dataclass
class Soldier:
    kind: str
    side: str
    x: float
    y: float
    hp: float
    shields: float
    cooldown_until: float
    spores: int = 0
    next_spore_decay: int = 10**9
    alive: bool = True
    shots: int = 0
    id: int = 0


def effective_hp_damage(raw: float, armor: float, size: str,
                        damage_type: str, spores: int = 0) -> float:
    """Simplified Brood War HP damage with an explicit spore bonus.
    Brood War applies shield and armor ordering in fixed-point arithmetic;
    the surrogate uses float HP and simplifies minimum-damage edge cases.
    """
    if raw <= 0:
        return 0.0
    base = raw if damage_type == "ignore_armor" else max(0.5, raw - armor)
    return base * SIZES[damage_type][size] + spores


def health_fraction(s: Soldier, d: dict) -> float:
    return max(0.0, min(1.0,(s.hp+s.shields)/(d["hp"]+d["shields"])))


def shadow_cost(spec: dict, gas_shadow: float) -> float:
    c=spec["cost"]
    return c["minerals"] + gas_shadow*c["gas"]


def _distance(a: Soldier,b: Soldier) -> float:
    return math.hypot(a.x-b.x,a.y-b.y)


def _nearest(source: Soldier, enemies: list[Soldier], rng:random.Random,
             focus:float) -> Soldier | None:
    if not enemies:
        return None
    if rng.random() < focus:
        return min(enemies, key=lambda s:(s.hp+s.shields,s.id))
    return min(enemies,key=lambda s:(_distance(source,s),s.id))


def _weapon(attacker: dict, victim: dict) -> dict | None:
    return attacker["weapons"].get(victim["domain"])


def _move(unit: Soldier, target: Soldier, unit_data: dict,
          range_px: float, dt: int, rng:random.Random, kite:float,
          victim_data: dict) -> None:
    dx,dy=target.x-unit.x,target.y-unit.y
    dist=math.hypot(dx,dy)
    if dist < 0.1:
        return
    speed=unit_data["speed_px_per_frame"]
    if dist <= range_px:
        # Tactical retreat heuristic: under longer-range pressure, faster
        # units may kite to preserve spacing. No StarCraft micro claim.
        enemy_weap = _weapon(victim_data,unit_data)
        enemy_range = enemy_weap["range_tiles"]*32 if enemy_weap else 0
        if (speed > victim_data["speed_px_per_frame"] and
                range_px >= enemy_range and rng.random() < kite*0.25):
            step=min(speed*dt, max(0, range_px*0.9-dist)+6)
            unit.x-=dx/dist*step
            unit.y-=dy/dist*step
        return
    travel=min(speed*dt,max(0,dist-max(1,range_px-6)))
    unit.x+=dx/dist*travel
    unit.y+=dy/dist*travel


def _apply_hit(target: Soldier, target_data:dict, raw:float,
               typ:str, rng:random.Random, hit_chance:float,
               spore_enabled:bool, record:Counter) -> bool:
    if not target.alive or rng.random() > hit_chance:
        return False
    incoming=raw
    if target.shields > 0:
        absorbed=min(target.shields,incoming)
        target.shields-=absorbed
        incoming-=absorbed
        record["shield_damage"]+=absorbed
    if incoming > 0:
        spore_bonus=target.spores if spore_enabled else 0
        damage=effective_hp_damage(incoming,target_data["armor"],
                                   target_data["size"],typ,spore_bonus)
        dealt=min(target.hp,damage)
        target.hp-=dealt
        record["hp_damage"]+=dealt
        record["spore_bonus_damage"]+=min(dealt,spore_bonus)
        if target.hp<=0:
            target.hp=0
            target.alive=False
            record["kills"]+=1
    return True


def _attack(actor:Soldier,target:Soldier,soldiers:list[Soldier],
            data:dict, catalog:dict, frame:int, rng:random.Random,
            hit_chance:float, decay:int, spore_enabled:bool,
            record:Counter) -> None:
    attacker=data[actor.kind]
    victim=data[target.kind]
    weapon=_weapon(attacker,victim)
    if weapon is None:
        return
    if _distance(actor,target)>weapon["range_tiles"]*32+2:
        return
    cooldown=weapon["cooldown_frames"]
    if actor.kind=="devourer":
        cooldown = catalog.get("_devourer_cooldown",cooldown)
    extra_delay=actor.spores/8 if spore_enabled else 0
    if extra_delay:
        record["spore_cooldown_added_frames"]+=cooldown*extra_delay
    actor.cooldown_until=frame+cooldown*(1+extra_delay)
    actor.shots+=1
    record["shots_fired"]+=1
    if weapon.get("suicide",False):
        _apply_hit(target,victim,weapon["damage"],weapon["damage_type"],
                   rng,hit_chance,spore_enabled,record)
        actor.alive=False
        actor.hp=0
        return

    hits=int(weapon.get("hits_per_attack",1))
    # Goliath is represented as two projectiles with total stated damage 20.
    # Valkyrie listed 6 per rocket: keep 8 * 6 before hit/area uncertainty.
    if actor.kind=="goliath":
        dmg=weapon["damage"]/max(1,hits)
    else:
        dmg=weapon["damage"]
    if "bounce_damage" in weapon:
        possible=[v for v in soldiers if v.alive and v.side!=actor.side
                  and v.id!=target.id and v.kind!=actor.kind
                  and _distance(target,v)<=weapon.get("bounce_radius_px",96)]
        chosen=[target]
        for _ in range(2):
            if not possible:
                break
            nxt=min(possible,key=lambda v:(_distance(chosen[-1],v),v.id))
            chosen.append(nxt)
            possible.remove(nxt)
        for victim_obj,damage in zip(chosen,weapon["bounce_damage"]):
            _apply_hit(victim_obj,data[victim_obj.kind],damage,
                       weapon["damage_type"],rng,hit_chance,spore_enabled,record)
        return
    for _ in range(hits):
        if not target.alive:
            break
        _apply_hit(target,victim,dmg,weapon["damage_type"],
                   rng,hit_chance,spore_enabled,record)
        if weapon.get("splash_radius_px"):
            for other in soldiers:
                if (other.alive and other.side!=actor.side and other.id!=target.id
                        and other.kind!=actor.kind
                        and _distance(other,target)<=weapon["splash_radius_px"]):
                    # Surrogate fractional splash effect; authoritative
                    # outer/inner splash radii and friendliness are pending.
                    _apply_hit(other,data[other.kind],dmg*0.33,
                               weapon["damage_type"],rng,hit_chance,
                               spore_enabled,record)
    if actor.kind=="devourer" and spore_enabled and target.kind:
        radius=weapon.get("spore_splash_radius_px",64)
        # The initial target is spored even if killed; dead units discarded.
        for other in soldiers:
            if (other.alive and other.side!=actor.side
                    and other.kind in data and data[other.kind]["domain"]=="air"
                    and _distance(other,target)<=radius):
                other.spores=min(9,other.spores+1)
                other.next_spore_decay=frame+decay
                record["spore_applications"]+=1


def make_soldiers(scenario:dict,catalog:dict,rng:random.Random) -> list[Soldier]:
    units=[]
    spread=scenario.get("formation_spread_px",56)
    dist=scenario.get("starting_distance_px",500)
    uid=0
    for side in ("A","B"):
        for kind,n in scenario[side].items():
            if kind not in catalog:
                raise ValueError(f"unknown unit type {kind}")
            if type(n) is not int or not (0<=n<=100):
                raise ValueError("unit count must be integer 0..100")
            spec=catalog[kind]
            for _ in range(n):
                x=(0 if side=="A" else dist)+rng.uniform(-spread/2,spread/2)
                y=rng.gauss(0,spread*0.45)
                weapons=[x for x in spec["weapons"].values() if x]
                init_cd = rng.uniform(0, min(w["cooldown_frames"] for w in weapons)) if weapons else 0
                units.append(Soldier(kind=kind,side=side,x=x,y=y,
                    hp=float(spec["hp"]),shields=float(spec["shields"]),
                    cooldown_until=init_cd,id=uid))
                uid+=1
    return units


def _sum_value(soldiers:list[Soldier],side:str,data:dict,gas_shadow:float,
               weighted_health:bool=False)->float:
    value=0.0
    for s in soldiers:
        if s.side==side and s.alive:
            nominal=shadow_cost(data[s.kind],gas_shadow)
            value+=nominal*(0.2+0.8*health_fraction(s,data[s.kind])) if weighted_health else nominal
    return value


def simulate_once(scenario:dict, variant:str, seed:int,
                  overrides:dict|None=None, ablate_spores:bool=False,
                  catalog:dict|None=None)->dict:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported variant {variant}")
    if catalog is None:
        catalog=load_catalog()["units"]
    if not scenario.get("A") or not scenario.get("B"):
        raise ValueError("both sides need compositions")
    cfg={
        "hit_chance":0.92, "spore_decay_frames":240,
        "focus":0.65, "kite":0.25, "gas_shadow_price":1.5,
        "horizon_frames":500, "step_frames":3,
    }
    cfg.update({k:v for k,v in scenario.items() if k in cfg})
    if overrides:
        cfg.update(overrides)
    if not (0<=cfg["hit_chance"]<=1 and 0<=cfg["focus"]<=1 and 0<=cfg["kite"]<=1):
        raise ValueError("probabilities must be in [0,1]")
    if cfg["spore_decay_frames"]<1 or cfg["step_frames"]<1 or cfg["horizon_frames"]<1:
        raise ValueError("positive time parameters required")
    rng=random.Random(seed)
    data={k:dict(v) for k,v in catalog.items()}
    speed,cooldown=VARIANTS[variant]
    data["devourer"]=dict(data["devourer"],speed_px_per_frame=speed)
    meta={"_devourer_cooldown":cooldown}
    soldiers=make_soldiers(scenario,data,rng)
    initial={side:sum(shadow_cost(data[x.kind],cfg["gas_shadow_price"])
                    for x in soldiers if x.side==side) for side in ("A","B")}
    record=Counter()
    frame=0
    first_contact=None
    for frame in range(0,int(cfg["horizon_frames"])+1,int(cfg["step_frames"])):
        living=[u for u in soldiers if u.alive]
        if not any(u.side=="A" for u in living) or not any(u.side=="B" for u in living):
            break
        for actor in living:
            if actor.spores and frame>=actor.next_spore_decay:
                actor.spores-=1
                actor.next_spore_decay=frame+cfg["spore_decay_frames"]
        # Order movements, then simultaneously nominate attacks. Shuffle
        # volley ordering to avoid a deterministic player-A precedence.
        for actor in living:
            enemies=[v for v in living if v.side!=actor.side and v.alive]
            compatible=[v for v in enemies if _weapon(data[actor.kind],data[v.kind])]
            if not compatible:
                continue
            target=_nearest(actor,compatible,rng,cfg["focus"])
            weapon=_weapon(data[actor.kind],data[target.kind])
            _move(actor,target,data[actor.kind],weapon["range_tiles"]*32,
                  cfg["step_frames"],rng,cfg["kite"],data[target.kind])
        ready=[]
        for actor in living:
            if not actor.alive or frame<actor.cooldown_until:
                continue
            viable=[v for v in living if v.alive and v.side!=actor.side
                    and (weap:=_weapon(data[actor.kind],data[v.kind])) is not None
                    and _distance(actor,v)<=weap["range_tiles"]*32+2]
            if viable:
                ready.append((actor,_nearest(actor,viable,rng,cfg["focus"])))
        if ready and first_contact is None:
            first_contact=frame
        rng.shuffle(ready)
        for actor,target in ready:
            if actor.alive and target.alive:
                _attack(actor,target,soldiers,data,meta,frame,rng,
                        cfg["hit_chance"],cfg["spore_decay_frames"],
                        not ablate_spores,record)
    final={side:_sum_value(soldiers,side,data,cfg["gas_shadow_price"],True)
           for side in ("A","B")}
    survivors={side:sum(u.alive for u in soldiers if u.side==side)
               for side in ("A","B")}
    fraction={side:final[side]/max(1,initial[side]) for side in ("A","B")}
    delta=fraction["A"]-fraction["B"]
    if survivors["A"]==0 and survivors["B"]>0:
        outcome=-1
    elif survivors["B"]==0 and survivors["A"]>0:
        outcome=1
    elif abs(delta)<0.045:
        outcome=0
    else:
        outcome=1 if delta>0 else -1
    return {
        "winner":outcome,"score_difference":round(delta,5),
        "retained_value_fraction":{k:round(v,5) for k,v in fraction.items()},
        "remaining_units":survivors,
        "first_attack_frame":first_contact,"elapsed_frames":frame,
        "spore_applications":record["spore_applications"],
        "spore_bonus_hp_damage":round(record["spore_bonus_damage"],2),
        "spore_cooldown_added_frames":round(record["spore_cooldown_added_frames"],2),
        "shots":record["shots_fired"],
        "kill_count":record["kills"],
        "catalog_type":"public_seed_priors",
        "engine_fidelity":"surrogate_unvalidated"
    }


def simulate_batch(scenario:dict,variant:str, trials:int=64,
                   seed:int=20261025, overrides:dict|None=None,
                   ablate_spores:bool=False, catalog:dict|None=None)->dict:
    if type(trials) is not int or not 1<=trials<=5000:
        raise ValueError("trials must be integer 1..5000")
    if catalog is None:
        catalog=load_catalog()["units"]
    results=[simulate_once(scenario,variant,seed+i*7919,
                           overrides,ablate_spores,catalog) for i in range(trials)]
    wins=sum(x["winner"]==1 for x in results)
    losses=sum(x["winner"]==-1 for x in results)
    draws=trials-wins-losses
    return {
        "scenario_id":scenario["id"],"variant":variant,"trials":trials,
        "a_favorable_fraction":round((wins+0.5*draws)/trials,4),
        "a_wins":wins,"b_wins":losses,"draws":draws,
        "mean_retained_value_fraction_A":round(sum(x["retained_value_fraction"]["A"] for x in results)/trials,4),
        "mean_retained_value_fraction_B":round(sum(x["retained_value_fraction"]["B"] for x in results)/trials,4),
        "mean_spore_applications":round(sum(x["spore_applications"] for x in results)/trials,2),
        "mean_spore_bonus_hp_damage":round(sum(x["spore_bonus_hp_damage"] for x in results)/trials,2),
        "mean_spore_cooldown_added_frames":round(sum(x["spore_cooldown_added_frames"] for x in results)/trials,2),
        "source":"stochastic_surrogate_not_observed_games"
    }
