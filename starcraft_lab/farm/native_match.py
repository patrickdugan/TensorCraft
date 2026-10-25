"""Virtual-display native StarCraft 1.16.1 match adapter with real MPQ patch.

Uses BWAPI auto_menu=SINGLE_PLAYER for Pluto vs built-in AI. The external
modernized MPQDraft CLI loads the mod and BWAPI QDP injector in the same
launch, on an isolated Xvfb/Wine worker display. Unlike bwheadless this
fallback *runs* the graphical client on a virtual screen, consuming render
CPU. It still needs a live native acceptance test.

The BWAPI tournament DLL emits structured native-match start/end events.
Only after the observed game end and a valid game-generated replay are
recorded does this adapter exit with status 0. Result is not referee verified.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from .replay_gui import (ReplayLaunchError, checksum, configure_ini,
                         require_file, winepath, command, marker, lock, terminate)
from .runner import _replay_envelope

RACES={"zerg":"Zerg","terran":"Terran","protoss":"Protoss"}
GAME_EXT={".scx",".scm"}
GAME_MARKER_PRODUCER="bwapi_tournament_match_marker"


def configure_match_ini(original: str, relative_map: str,
                        relative_save_replay: str, observer_winpath: str,
                        *, race: str, enemy_race: str) -> str:
    if not re.fullmatch(r"maps/TC_[A-Za-z0-9_.-]{1,80}\.sc[mx]",relative_map):
        raise ReplayLaunchError("invalid private map path")
    if not re.fullmatch(r"maps/replays/TC_[A-Za-z0-9_.-]{1,80}\.rep",
                        relative_save_replay):
        raise ReplayLaunchError("invalid output replay path")
    if race.lower() not in RACES or enemy_race.lower() not in RACES:
        raise ReplayLaunchError("invalid 1v1 races")
    c=configparser.ConfigParser(interpolation=None,strict=False,
                                comment_prefixes=(";","#"))
    try:c.read_string(original)
    except configparser.Error as e:
        raise ReplayLaunchError("malformed private BWAPI config") from e
    if not c.has_section("ai") or not c.has_section("auto_menu"):
        raise ReplayLaunchError("private bwapi.ini has no [ai] / [auto_menu]")
    if not re.fullmatch(r"[A-Za-z]:[\\/].+",observer_winpath):
        raise ReplayLaunchError("invalid x86 tournament DLL Windows path")
    # Existing ai= must remain Pluto; no-op Replay AI is for replay rendering.
    current_ai=c.get("ai","ai",fallback="")
    if not current_ai.lower().replace("\\","/").endswith("ai/pluto.dll"):
        raise ReplayLaunchError("normal native match requires ai=pluto.dll")
    c.set("ai","tournament",observer_winpath)
    c.set("auto_menu","auto_menu","SINGLE_PLAYER")
    c.set("auto_menu","map",relative_map)
    c.set("auto_menu","game_type","MELEE")
    c.set("auto_menu","race",RACES[race.lower()])
    c.set("auto_menu","enemy_count","1")
    c.set("auto_menu","enemy_race",RACES[enemy_race.lower()])
    c.set("auto_menu","auto_restart","OFF")
    c.set("auto_menu","save_replay",relative_save_replay)
    b=StringIO()
    c.write(b)
    return b.getvalue()


def wait_for_replay(path:Path, timeout:int=30)->bool:
    deadline=time.monotonic()+timeout
    previous=-1
    stable=0
    while time.monotonic()<deadline:
        if path.is_file() and _replay_envelope(path):
            n=path.stat().st_size
            if n==previous:stable+=1
            else:stable=0
            previous=n
            if stable>=3:return True
        time.sleep(0.25)
    return False


def run_native_match(*, run_id, game_dir, map_file, mod_mpq,
                     output_replay, mpqdraft, qdp, observer,
                     race="Zerg",enemy_race="Zerg",
                     wine_exe="wine",winepath_exe="winepath",
                     timeout=5400,port_base=46000):
    from .schema import safe_id
    run_id=safe_id(run_id,"run_id")
    if type(timeout) is not int or not 30<=timeout<=14400:
        raise ReplayLaunchError("invalid native match timeout")
    if type(port_base) is not int or not 10000<=port_base<=60000:
        raise ReplayLaunchError("invalid port_base marker")
    game_dir=Path(game_dir).resolve()
    game=require_file(game_dir/"StarCraft.exe","private StarCraft 1.16.1")
    ini=require_file(game_dir/"bwapi-data"/"bwapi.ini","BWAPI config")
    map_file=require_file(map_file,"source game map")
    if map_file.suffix.lower() not in GAME_EXT:
        raise ReplayLaunchError("map must be .scx or .scm")
    mod_mpq=require_file(mod_mpq,"private custom MPQ")
    mpqdraft=require_file(mpqdraft,"modernized MPQDraft CLI")
    qdp=require_file(qdp,"BWAPI MPQDraft QDP injector")
    observer=require_file(observer,"x86 BWAPI sidecar")
    output_replay=Path(output_replay).resolve()
    if output_replay.exists() or not output_replay.parent.is_dir():
        raise ReplayLaunchError("output replay must be new and parent must exist")
    if game_dir in output_replay.parents:
        raise ReplayLaunchError("output replay must be outside original game installation")
    if not shutil.which(wine_exe) or not shutil.which(winepath_exe):
        raise ReplayLaunchError("Wine/Winepath are missing")
    env=os.environ.copy()
    if not env.get("DISPLAY") or not env.get("WINEPREFIX") or not Path(env["WINEPREFIX"]).is_dir():
        raise ReplayLaunchError("private WINEPREFIX and DISPLAY are required")

    private_map=game_dir/f"maps/TC_{run_id}{map_file.suffix.lower()}"
    save_path=game_dir/f"maps/replays/TC_{run_id}.rep"
    relative_map="maps/"+private_map.name
    relative_rep="maps/replays/"+save_path.name
    ready=output_replay.parent/"bwapi_match.ready"
    done=output_replay.parent/"bwapi_match.done"
    if ready.exists() or done.exists() or save_path.exists() or private_map.exists():
        raise ReplayLaunchError("run already has native marker/replay files")
    if not (output_replay.parent/"stdout.log").parent.is_dir():
        raise ReplayLaunchError("private output directory unavailable")
    original_ini=ini.read_bytes()
    proc=None
    receipt=None
    with lock(game_dir/".tensorcraft_match.lock"):
        try:
            private_map.parent.mkdir(parents=True,exist_ok=True)
            save_path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(map_file,private_map)
            if checksum(private_map)!=checksum(map_file):
                raise ReplayLaunchError("staged map hash mismatch")
            observer_win=winepath(observer,winepath_exe,env)
            ini.write_text(
                configure_match_ini(original_ini.decode("utf-8-sig"),
                    relative_map,relative_rep,observer_win,
                    race=race,enemy_race=enemy_race),
                encoding="utf-8")
            windows={label:winepath(p,winepath_exe,env) for label,p in
                     {"exe":game,"mpqdraft":mpqdraft,"mod":mod_mpq,
                      "qdp":qdp,"ready":ready,"done":done}.items()}
            env.update({
                "TC_MATCH_RUN_ID":run_id,
                "TC_MATCH_READY_PATH":windows["ready"],
                "TC_MATCH_DONE_PATH":windows["done"],
                "TC_RENDER_RUN_ID":"",
                "TC_RENDER_READY_PATH":"",
                "TC_RENDER_DONE_PATH":"",
                "TC_TRACE_PATH":"","TC_TRACE_RUN_ID":"","TC_TRACE_VARIANT":"",
            })
            argv=command(wine_exe,windows["mpqdraft"],windows["exe"],
                         windows["mod"],windows["qdp"])
            with (output_replay.parent/"mpqdraft_match.log").open("xb") as log:
                proc=subprocess.Popen(argv,cwd=game_dir,env=env,stdout=log,
                                      stderr=subprocess.STDOUT,start_new_session=True)
                start_frame=None
                finished=None
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    started=marker(ready,"ready",run_id,replay_mode=False)
                    if started is not None:
                        start_frame=started["frame"]
                    completed=marker(done,"done",run_id,after=(start_frame or 0)+1,
                                     replay_mode=False)
                    if completed is not None:
                        if start_frame is None:
                            raise ReplayLaunchError("match ended before game start marker")
                        finished=completed["frame"]
                        break
                    code=proc.poll()
                    if code is not None and code!=0:
                        raise ReplayLaunchError(f"MPQDraft exited {code} before game completion")
                    time.sleep(0.15)
                if finished is None:
                    raise ReplayLaunchError("no complete native match callback before deadline")
            if not wait_for_replay(save_path,timeout=30):
                raise ReplayLaunchError("BWAPI did not produce a stable valid saved replay")
            shutil.copyfile(save_path,output_replay)
            if not _replay_envelope(output_replay) or checksum(output_replay)!=checksum(save_path):
                raise ReplayLaunchError("output replay failed post-copy verification")
            receipt={
                "schema":1,"run_id":run_id,
                "status":"native_bwapi_game_finished_with_saved_replay",
                "start_frame":start_frame,"end_frame":finished,
                "player_race":RACES[race.lower()],"enemy_race":RACES[enemy_race.lower()],
                "output_replay_sha256":checksum(output_replay),
                "source_map_sha256":checksum(map_file),
                "mod_mpq_sha256":checksum(mod_mpq),
                "bwapi_qdp_sha256":checksum(qdp),
                "observer_sha256":checksum(observer),
                "mpqdraft_sha256":checksum(mpqdraft),
                "actual_mod_stats_verified_in_game":False,
                "replay_parser_independently_run":False,
                "game_outcome_referee_verified":False,
                "native_game_graphics_run_under_virtual_x11":True,
                "true_bwheadless":False,
                "port_base_reserved_but_unused_for_single_player":port_base,
            }
            with (output_replay.parent/"native_match_receipt.json").open("x",encoding="utf-8") as out:
                json.dump(receipt,out,indent=2,sort_keys=True)
                out.write("\n")
        finally:
            terminate(proc)
            ini.write_bytes(original_ini)
            private_map.unlink(missing_ok=True)
            save_path.unlink(missing_ok=True)
    return receipt


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("game-dir","map","mod","output-replay","mpqdraft","qdp","observer"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--run-id",required=True)
    p.add_argument("--port-base",type=int,default=46000)
    p.add_argument("--race",default="Zerg")
    p.add_argument("--enemy-race",default="Zerg")
    p.add_argument("--wine-exe",default="wine")
    p.add_argument("--winepath-exe",default="winepath")
    p.add_argument("--timeout",type=int,default=5400)
    args=p.parse_args(argv)
    try:
        result=run_native_match(
            run_id=args.run_id,game_dir=args.game_dir,map_file=args.map,
            mod_mpq=args.mod,output_replay=args.output_replay,
            mpqdraft=args.mpqdraft,qdp=args.qdp,observer=args.observer,
            race=args.race,enemy_race=args.enemy_race,
            wine_exe=args.wine_exe,winepath_exe=args.winepath_exe,
            timeout=args.timeout,port_base=args.port_base)
    except (ReplayLaunchError,OSError,UnicodeError) as error:
        print(f"native match failed: {error}",file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
