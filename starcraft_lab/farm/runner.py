"""Concurrent experimental games, isolated by slot and by paired assignment.

This runner provides a safe subprocess orchestration boundary. It does not
pretend that bwheadless, MPQDraft or Pluto work merely because a process
returns 0. Replay bytes must pass independent replay-core parsing.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

from .schema import (FarmError, canonical_dir, canonical_file, hash_file,
                     safe_id, substitute_command, validate_runtime, HEX)

REPLAY_MAGICS = {b"reRS", b"seRS"}
RESULT_STATES = frozenset({
    "launcher_failed", "launcher_timeout", "missing_replay",
    "invalid_replay_envelope", "parser_failed", "parsed_replay",
    "unparsed_replay", "artifact_mismatch", "worker_error"
})


def json_at(path: Path) -> dict:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise FarmError(f"failed to load JSON: {path}") from ex
    if not isinstance(content, dict):
        raise FarmError(f"{path}: JSON object required")
    return content


def atomic_create(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, sort_keys=True)
        out.write("\n")


def _reserved_lock(path: Path):
    """Open an exclusive lock. Stale locks are not broken automatically."""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as ex:
        raise FarmError(f"worker/control lock exists: {path}") from ex
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode())
    finally:
        os.close(fd)


def _replay_envelope(path: Path) -> bool:
    if not path.is_file() or not 30 <= path.stat().st_size <= 64*1024*1024:
        return False
    with path.open("rb") as f:
        f.seek(12)
        return f.read(4) in REPLAY_MAGICS


def _freeze_bandit(slot: dict, seed: bytes, job_dir: Path) -> list[dict]:
    copies = []
    if not seed:
        raise FarmError("initial bandit snapshot is empty")
    for n, raw in enumerate(slot["bandit_paths"]):
        path = Path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        # An existing per-worker file is private, but its previous contents
        # remain preserved as part of the experiment's audit trail.
        previous_hash = hash_file(path) if path.is_file() else None
        if previous_hash is not None:
            backup = job_dir / f"bandit_before_{n}.txt"
            backup.write_bytes(path.read_bytes())
        pending = path.with_name(path.name + f".tensorcraft_{os.getpid()}.tmp")
        if pending.exists():
            raise FarmError(f"pending bandit temp exists: {pending}")
        with pending.open("xb") as out:
            out.write(seed)
        os.replace(pending, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(seed).hexdigest():
            raise FarmError("bandit reset verification failed")
        copies.append({"location":str(path),"previous_sha256":previous_hash,
                       "reset_sha256":hashlib.sha256(seed).hexdigest()})
    return copies


def _parse_replay(replay:Path, outdir:Path, program:str|None) -> tuple[str,dict|None,str|None]:
    if not replay.is_file():
        return "missing_replay", None, "game launcher did not produce a replay"
    if not _replay_envelope(replay):
        return "invalid_replay_envelope", None, "not a plausible bounded .rep envelope"
    if not program:
        return "unparsed_replay", None, "replay-core binary was not configured"
    probe_file=outdir/"replay_probe.json"
    try:
        p=subprocess.run([str(program),str(replay),str(probe_file)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         check=False,timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "parser_failed",None,f"replay-core invocation failed: {type(e).__name__}"
    if p.returncode != 0 or not probe_file.is_file():
        return "parser_failed",None,"replay-core returned failure"
    try:
        probe = json_at(probe_file)
    except FarmError:
        return "parser_failed",None,"invalid replay parser output"
    checks=probe.get("command_warnings")
    if (probe.get("schema")!=1 or
        probe.get("producer")!="starcraft-replay-probe/replay-core" or
        probe.get("replay_sha256")!=hash_file(replay) or
        probe.get("engine_type")!="BroodWar" or
        not isinstance(checks,dict) or checks.get("clean") is not True or
        not isinstance(probe.get("frame_count"),int) or
        probe["frame_count"]<=0):
        return "parser_failed",probe,"replay probe data or command integrity mismatch"
    return "parsed_replay",probe,None


def _stop_tree(proc:subprocess.Popen) -> None:
    """Terminate the owned launcher process tree on timeout (best effort).

    Native Wine processes that deliberately daemonize outside the process
    group require manual operator cleanup before restarting a worker.
    """
    if os.name=="nt":
        try:
            subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        except (OSError,subprocess.TimeoutExpired):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name=="nt":
            proc.kill()
        else:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        proc.wait(timeout=5)


def _launch(argv:list[str],slot:dict,env:dict,stdout,stderr,timeout:int):
    """Run an explicit no-shell command and return (code, timeout)."""
    flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0) if os.name=="nt" else 0
    proc=subprocess.Popen(argv,cwd=slot["game_dir"],env=env,
                          stdout=stdout,stderr=stderr,
                          creationflags=flags,start_new_session=os.name!="nt")
    try:
        return proc.wait(timeout=timeout),False
    except subprocess.TimeoutExpired:
        _stop_tree(proc)
        return proc.returncode,True


def _single_game(game:dict, slot:dict, settings:dict,
                 work_root:Path, baseline:bytes) -> dict:
    m=game["manifest"]
    run_id=safe_id(m["run_id"],"run_id")
    variant=m["variant"]
    job_dir=work_root/"jobs"/run_id
    job_dir.mkdir(parents=True,exist_ok=False)
    result_path=job_dir/"execution.json"
    started=time.monotonic()
    state="worker_error"
    message=None
    exit_code=None
    replay_sha=None
    probe=None
    reset=[]
    launch_sha=None
    mod=settings["mods"][variant]
    replay=job_dir/(run_id+".rep")
    capture=job_dir/"stdout.log"
    errors=job_dir/"stderr.log"
    try:
        if hash_file(Path(mod["mpq"])) != mod["sha256"]:
            raise FarmError("MPQ archive changed after planning")
        if hash_file(Path(game["map_file"])) != m["map_sha256"]:
            raise FarmError("map changed after planning")
        if hashlib.sha256(baseline).hexdigest()!=m["bandit_initial_sha256"]:
            raise FarmError("bandit snapshot changed")
        # Recheck game binaries for the current worker. This catches writes
        # accidentally made to a supposedly immutable experimental install.
        for key,expected_hash in slot["pins"].items():
            from starcraft_lab.batch import PINNED_FILES
            src=Path(slot["game_dir"])/PINNED_FILES[key]
            if hash_file(src)!=expected_hash:
                raise FarmError(f"worker game file changed: {key}")
        reset=_freeze_bandit(slot,baseline,job_dir)
        values={
            "run_id":run_id,"pair_id":m["pair_id"],"variant":variant,
            "map_path":str(Path(game["map_file"]).resolve()),
            "mod_mpq":mod["mpq"],
            "replay_path":str(replay),
            "job_dir":str(job_dir),
            "game_dir":slot["game_dir"],
            "wineprefix":slot["wineprefix"],
            "display":slot["display"],
            "slot_id":slot["id"],
            "seed_label":str(m["seed"]),
            "port_base":str(slot["port_base"]),
        }
        argv=substitute_command(settings["launcher"]["argv"], values)
        launch_sha=hashlib.sha256(json.dumps(argv,sort_keys=False).encode()).hexdigest()
        env=os.environ.copy()
        source_root=str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"]=source_root+(
            os.pathsep+env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.update({
            "WINEPREFIX":slot["wineprefix"],
            "DISPLAY":slot["display"],
            "TC_RUN_ID":run_id,
            "TC_VARIANT":variant,
            "TC_GAME_DIR":slot["game_dir"],
            "TC_REPLAY_PATH":str(replay),
            "TC_MOD_MPQ":mod["mpq"],
            "TC_MAP_PATH":values["map_path"],
            "TC_PORT_BASE":str(slot["port_base"]),
            "TC_BANDIT_SNAPSHOT_SHA256":m["bandit_initial_sha256"],
        })
        with capture.open("xb") as stdout, errors.open("xb") as stderr:
            try:
                exit_code,timed_out=_launch(
                    argv,slot,env,stdout,stderr,
                    settings["launcher"]["timeout_seconds"])
            except OSError as ex:
                state="launcher_failed"
                message=f"launcher failed to execute: {type(ex).__name__}"
            else:
                if timed_out:
                    state="launcher_timeout"
                    message="launcher process tree terminated on timeout; inspect Wine children"
                elif exit_code != 0:
                    state="launcher_failed"
                    message=f"launcher exited {exit_code}"
                else:
                    state,probe,message=_parse_replay(
                        replay,job_dir,settings.get("replay_probe_exe"))
                    if replay.is_file():
                        replay_sha=hash_file(replay)
    except (FarmError, OSError, ValueError) as ex:
        state="artifact_mismatch"
        message=str(ex)
    result={
        "schema":1,"run_id":run_id,"pair_id":m["pair_id"],
        "variant":variant,"slot_id":slot["id"],
        "status":state,"exit_code":exit_code,
        "elapsed_seconds":round(time.monotonic()-started,3),
        "mod_mpq_sha256":mod["sha256"],
        "map_sha256":m["map_sha256"],
        "initial_bandit_sha256":m["bandit_initial_sha256"],
        "bandit_resets":reset,
        "replay":str(replay) if replay.is_file() else None,
        "replay_sha256":replay_sha,
        "replay_parser_status":("confirmed_legacy_parser" if state=="parsed_replay" else "not_confirmed"),
        "native_game_execution_authenticated":False,
        "video_captured":False,
        "game_outcome_verified":False,
        "launcher_argv_sha256":launch_sha,
        "diagnostic":message,
    }
    if state not in RESULT_STATES:
        raise AssertionError(f"invalid result state {state}")
    atomic_create(result_path,result)
    return result


def prepare(plan_file:Path, runtime_file:Path, dest:Path) -> dict:
    plan_file=Path(plan_file).resolve()
    runtime_file=Path(runtime_file).resolve()
    dest=Path(dest).resolve()
    plan=json_at(plan_file)
    runtime=json_at(runtime_file)
    spec=validate_runtime(runtime,plan,dest)
    seed_file=plan_file.with_name("bandit_baseline.txt")
    seed=seed_file.read_bytes()
    if hashlib.sha256(seed).hexdigest()!=plan["source_bandit_sha256"]:
        raise FarmError("plan bandit snapshot mismatches SHA-256")
    # Prevent private run output from being written into source installs.
    if dest.exists():
        raise FarmError(f"refusing to overwrite workspace: {dest}")
    dest.mkdir(parents=True,exist_ok=False)
    (dest/"jobs").mkdir()
    (dest/"bandit_baseline.txt").write_bytes(seed)
    atomic_create(dest/"plan.json",plan)
    atomic_create(dest/"runtime.json",runtime)
    from .schema import validate_plan
    pairs,_=validate_plan(plan)
    descriptor={
        "schema":1,"status":"prepared_not_executed",
        "pairs":len(pairs),"games":sum(len(p) for p in pairs),
        "slots":len(spec["slots"]),
        "max_parallel":spec["capacity"]["max_parallel"],
        "runtime_sha256":hash_file(runtime_file),
        "plan_sha256":hash_file(plan_file),
        "game_result_verification":False,
        "native_headless_backend_verified":False,
    }
    atomic_create(dest/"prepared.json",descriptor)
    return descriptor


def execute(workspace:Path, *, allow_native:bool=False) -> dict:
    work_root=Path(workspace).resolve()
    plan=json_at(work_root/"plan.json")
    runtime=json_at(work_root/"runtime.json")
    settings=validate_runtime(runtime,plan,work_root)
    if not allow_native:
        raise FarmError("native launcher is disabled by default; pass --allow-native explicitly")
    seed=(work_root/"bandit_baseline.txt").read_bytes()
    if hashlib.sha256(seed).hexdigest()!=plan["source_bandit_sha256"]:
        raise FarmError("frozen bandit hash changed")
    if runtime.get("replay_probe_exe") is not None:
        program=canonical_file(runtime["replay_probe_exe"],"replay_probe_exe")
        settings["replay_probe_exe"]=str(program)
    else:
        settings["replay_probe_exe"]=None
    from .schema import validate_plan
    pairs,_=validate_plan(plan)
    # Stable slot assignment makes runs reproducible even when workers finish
    # at different speeds. Pair arms always execute sequentially on one slot.
    n=settings["capacity"]["max_parallel"]
    assignments=[[] for _ in range(n)]
    for index,pair in enumerate(pairs):
        assignments[index % n].append(pair)
    lock_paths=[work_root/".farm.lock"] + [Path(s["game_dir"])/".tensorcraft_worker.lock"
                                             for s in settings["slots"][:settings["capacity"]["max_parallel"]]]
    done=[]
    with ExitStack() as stack:
        for path in lock_paths:
            _reserved_lock(path)
            stack.callback(path.unlink)
        def worker(args):
            slot,assigned_pairs=args
            records=[]
            for pair in assigned_pairs:
                for game in pair:
                    records.append(_single_game(game,slot,settings,work_root,seed))
            return records
        with ThreadPoolExecutor(max_workers=n) as executor:
            results=list(executor.map(worker,
                       zip(settings["slots"][:n],assignments)))
        for result in results:
            done.extend(result)
        aggregate={
            "schema":1,"status":"execution_attempted",
            "jobs":len(done),"counts":dict(__import__("collections").Counter(x["status"] for x in done)),
            "max_parallel_configured":settings["capacity"]["max_parallel"],
            "native_headless_backend_verified":False,
            "games_with_parser_clean_replays":sum(x["status"]=="parsed_replay" for x in done),
            "video_jobs_emitted":0,
            "game_outcomes_verified":0,
            "records":[str(work_root/"jobs"/r["run_id"]/"execution.json") for r in done],
        }
        atomic_create(work_root/"execution_summary.json",aggregate)
    return aggregate


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    prep=sub.add_parser("prepare")
    prep.add_argument("--plan",type=Path,required=True)
    prep.add_argument("--runtime",type=Path,required=True)
    prep.add_argument("--output",type=Path,required=True)
    run=sub.add_parser("run")
    run.add_argument("--workspace",type=Path,required=True)
    run.add_argument("--allow-native",action="store_true")
    args=p.parse_args(argv)
    outcome=prepare(args.plan,args.runtime,args.output) if args.command=="prepare" else execute(
        args.workspace,allow_native=args.allow_native)
    print(json.dumps(outcome,indent=2,sort_keys=True))
    return 0 if outcome.get("status") != "execution_attempted" or (
        outcome["counts"]=={"parsed_replay":outcome["jobs"]}) else 2


if __name__=="__main__":
    raise SystemExit(main())
