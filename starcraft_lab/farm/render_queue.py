"""Select genuinely parsed match replays for a separate GUI/YouTube render pool.

Scoring uses *issued orders and replay duration*, not actual observed kills,
micro quality or any proof that the intended mod was loaded in-game.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from . import video
from .runner import json_at, atomic_create
from .schema import (FarmError, canonical_dir, canonical_file,
                     hash_file, safe_id, HEX)


def _orders(probe: dict, unit_key: str) -> int:
    try:
        return sum(max(0,int(p["unit_orders"][unit_key]["issued_orders"]))
                   for p in probe["players"])
    except (KeyError,TypeError,ValueError):
        return 0


def candidates(match_workspace:Path) -> list[dict]:
    root=Path(match_workspace).resolve()
    output=json_at(root/"execution_summary.json")
    plan=json_at(root/"plan.json")
    runtime=json_at(root/"runtime.json")
    original={g["manifest"]["run_id"]:g["manifest"] for g in plan["games"]}
    if output.get("schema")!=1 or output.get("status")!="execution_attempted":
        raise FarmError("no completed attempted batch")
    entries=[]
    for path in output.get("records",[]):
        receipt=json_at(Path(path))
        if receipt.get("status")!="parsed_replay":
            continue
        rid=safe_id(receipt.get("run_id"),"run_id")
        if rid not in original or receipt.get("variant")!=original[rid]["variant"]:
            raise FarmError("execution record does not belong to the frozen plan")
        replay=canonical_file(receipt.get("replay"),"saved game replay")
        if hash_file(replay)!=receipt.get("replay_sha256"):
            raise FarmError(f"{rid}: replay SHA-256 changed")
        probe=json_at(replay.with_name("replay_probe.json"))
        if probe.get("replay_sha256")!=receipt["replay_sha256"]:
            raise FarmError(f"{rid}: replay parser hashes disagree")
        mods=runtime["mods"]
        mod=mods[receipt["variant"]]
        mpq=canonical_file(mod["mpq"],"mod package")
        if hash_file(mpq)!=mod["sha256"]:
            raise FarmError(f"{rid}: mod MPQ changed")
        frames=probe.get("frame_count",0)
        if type(frames) is not int or frames<=0:
            raise FarmError("missing valid replay duration in frames")
        devs=_orders(probe,"devourer")
        mutas=_orders(probe,"mutalisk")
        guardians=_orders(probe,"guardian")
        scouts=_orders(probe,"scout")
        corsairs=_orders(probe,"corsair")
        # A reproducible editorial triage heuristic, not a balance score.
        score=(20 if devs>0 else 0) + min(devs,6)*2 + min(mutas,24)*0.2
        score+=(10 if scouts>=4 else 0)+(6 if guardians else 0)
        score+=(6 if corsairs else 0)
        score+= min(frames/8000,4)
        entries.append({
            "run_id":rid,"pair_id":receipt["pair_id"],
            "variant":receipt["variant"],
            "replay":str(replay),"replay_sha256":receipt["replay_sha256"],
            "mod_mpq":str(mpq),"mod_mpq_sha256":mod["sha256"],
            "replay_frames":frames,
            "issued_orders":{"devourer":devs,"mutalisk":mutas,
                             "guardian":guardians,"scout":scouts,"corsair":corsairs},
            "editorial_score":round(score,3),
            "editorial_basis":"parsed-replay issued orders, not observed battlefield events",
            "gameplay_video_verified":False,
        })
    return sorted(entries,key=lambda x:(-x["editorial_score"],x["run_id"]))


def choose(workspace:Path,output_dir:Path,*,count:int=3)->dict:
    if type(count) is not int or not 1<=count<=50:
        raise FarmError("selection count outside 1..50")
    output_dir=Path(output_dir).resolve()
    if output_dir.exists():
        raise FarmError("selection output directory already exists")
    ranked=candidates(workspace)
    selected=ranked[:count]
    output_dir.mkdir(parents=True,exist_ok=False)
    result={
        "schema":1,"status":"replay_candidates_not_yet_rendered",
        "requested_max_videos":count,"eligible_replays":len(ranked),
        "selected":selected,
        "rendered_videos":0,
        "gameplay_authenticity_proven":False,
        "notes":["requires actual replay-capable GUI player with matching MPQ",
                 "editorial ranking reflects build commands, not exciting combat",
                 "YouTube visual review and soundtrack licensing remain manual"],
    }
    atomic_create(output_dir/"selection.json",result)
    return result


def _validate_pool(pool:dict, jobs:list[dict]):
    if not isinstance(pool,dict) or pool.get("schema")!=1:
        raise FarmError("invalid render worker pool")
    n=pool.get("max_parallel")
    slots=pool.get("slots")
    if type(n) is not int or not 1<=n<=4 or not isinstance(slots,list) or not n<=len(slots)<=4:
        raise FarmError("render max_parallel must use 1..4 isolated GUI workers")
    capacity=pool.get("capacity",{})
    for name in ("cpu_budget_threads","threads_per_render","ram_budget_mb","ram_per_render_mb"):
        if type(capacity.get(name)) is not int or capacity[name]<=0:
            raise FarmError(f"invalid {name}")
    if n*capacity["threads_per_render"]>capacity["cpu_budget_threads"]:
        raise FarmError("render CPU oversubscribed")
    if n*capacity["ram_per_render_mb"]>capacity["ram_budget_mb"]:
        raise FarmError("render RAM oversubscribed")
    launcher=pool.get("replay_launcher")
    video._validate_render_script(launcher)
    seen=set()
    results=[]
    for s in slots[:n]:
        if not isinstance(s,dict):
            raise FarmError("invalid video slot")
        ident=safe_id(s.get("id"),"render_slot.id")
        game_dir=canonical_dir(s.get("game_dir"),"render game install")
        wineprefix=canonical_dir(s.get("wineprefix"),"render Wine prefix")
        display=s.get("display")
        if not isinstance(display,str) or not display.startswith(":") or not display[1:].isdigit():
            raise FarmError("invalid private X display")
        for token in (ident,str(game_dir),str(wineprefix),display):
            if token in seen:
                raise FarmError("duplicate render ID, install, Wine prefix or display")
            seen.add(token)
        results.append({"id":ident,"game_dir":str(game_dir),
                        "wineprefix":str(wineprefix),"display":display})
    return n,results


def run_selected(selection_dir:Path, pool_file:Path, output_dir:Path,
                 *,execute:bool=False)->dict:
    if not execute:
        raise FarmError("native GUI rendering is disabled without --execute")
    selection=json_at(Path(selection_dir)/"selection.json")
    jobs=selection.get("selected",[])
    pool=json_at(pool_file)
    n,slots=_validate_pool(pool,jobs)
    dest=Path(output_dir).resolve()
    if dest.exists():
        raise FarmError("render batch destination already exists")
    for s in slots:
        if dest==Path(s["game_dir"]) or Path(s["game_dir"]) in dest.parents:
            raise FarmError("render output overlaps a private game installation")
    dest.mkdir(parents=True,exist_ok=False)
    assignments=[[] for _ in range(n)]
    for index,item in enumerate(jobs):
        assignments[index%n].append(item)
    def worker(arg):
        slot,assigned=arg
        outcomes=[]
        for job in assigned:
            source={
                "schema":1,"run_id":job["run_id"],
                "replay":job["replay"],"replay_sha256":job["replay_sha256"],
                "mod_mpq":job["mod_mpq"],"mod_mpq_sha256":job["mod_mpq_sha256"],
                "display":slot["display"],"game_dir":slot["game_dir"],
                "wineprefix":slot["wineprefix"],"replay_launcher":pool["replay_launcher"],
                "capture_limit_seconds":pool.get("capture_limit_seconds",3600),
                "ready_timeout_seconds":pool.get("ready_timeout_seconds",90),
            }
            folder=dest/job["run_id"]
            try:
                artifact=video.capture(source,folder,execute=True)
                outcomes.append({"run_id":job["run_id"],"status":artifact["status"],
                                 "video":artifact.get("video"),"slot_id":slot["id"]})
            except Exception as exc:
                # This is only an external-renderer failure. It never
                # converts into an experimental match outcome.
                folder.mkdir(parents=True,exist_ok=True)
                outcome={"run_id":job["run_id"],"slot_id":slot["id"],
                         "status":"render_failed","exception_type":type(exc).__name__,
                         "message":str(exc)}
                atomic_create(folder/"failure.json",outcome)
                outcomes.append(outcome)
        return outcomes
    with ThreadPoolExecutor(max_workers=n) as executor:
        finished=list(executor.map(worker,zip(slots,assignments)))
    flat=[x for group in finished for x in group]
    result={
        "schema":1,"status":"render_attempted",
        "jobs":len(flat),"max_parallel":n,
        "counts":dict(Counter(x["status"] for x in flat)),
        "videos_codec_checked":sum(x["status"]=="encoded_full_playback_candidate" for x in flat),
        "actual_gameplay_content_reviewed":0,
        "records":flat,
    }
    atomic_create(dest/"render_summary.json",result)
    return result


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest="command",required=True)
    pick=subs.add_parser("select")
    pick.add_argument("--workspace",type=Path,required=True)
    pick.add_argument("--output",type=Path,required=True)
    pick.add_argument("--max-videos",type=int,default=3)
    render=subs.add_parser("render")
    render.add_argument("--selection",type=Path,required=True)
    render.add_argument("--pool",type=Path,required=True)
    render.add_argument("--output",type=Path,required=True)
    render.add_argument("--execute",action="store_true")
    args=p.parse_args(argv)
    result=(choose(args.workspace,args.output,count=args.max_videos)
            if args.command=="select" else
            run_selected(args.selection,args.pool,args.output,execute=args.execute))
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
