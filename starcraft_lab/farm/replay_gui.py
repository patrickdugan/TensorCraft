"""Automated real graphical replay playback through BWAPI auto_menu and MPQDraft.

Uses the modernized sjoblomj/MPQDraft patch command, not the older -launch
syntax. Requires BWAPI v4.4.0 and a locally linked TensorCraftTrace.dll
exporting no-op replay AI and native replay start/end markers. No game assets
or third-party binaries are supplied by this module.
"""
from __future__ import annotations

import argparse
import configparser
from contextlib import contextmanager
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
WIN_ABS = re.compile(r"[A-Za-z]:[\\/].+")
MARKER_PRODUCER = "bwapi_tournament_replay_marker"


class ReplayLaunchError(RuntimeError):
    pass


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_file(value, label: str) -> Path:
    p = Path(value).resolve()
    if not p.is_file():
        raise ReplayLaunchError(f"{label} is missing: {p}")
    return p


def configure_ini(source: str, replay_relative: str, replay_ai_win: str) -> str:
    if not re.fullmatch(r"maps/replays/TC_[A-Za-z0-9_.-]{1,80}\.rep", replay_relative):
        raise ReplayLaunchError("unsafe replay filename")
    if not WIN_ABS.fullmatch(replay_ai_win):
        raise ReplayLaunchError("observer must have absolute Windows path")
    c = configparser.ConfigParser(interpolation=None,strict=False,
                                 comment_prefixes=(";","#"))
    try:
        c.read_string(source)
    except configparser.Error as e:
        raise ReplayLaunchError("private bwapi.ini is malformed") from e
    if not c.has_section("ai") or not c.has_section("auto_menu"):
        raise ReplayLaunchError("private bwapi.ini lacks [ai] or [auto_menu]")
    c.set("ai","ai",replay_ai_win)
    c.set("ai","tournament",replay_ai_win)
    c.set("auto_menu","auto_menu","SINGLE_PLAYER")
    c.set("auto_menu","map",replay_relative)
    c.set("auto_menu","auto_restart","OFF")
    c.set("auto_menu","pause_dbg","OFF")
    out = StringIO()
    c.write(out)
    return out.getvalue()


def marker(path: Path, kind: str, run_id: str, after: int = -1):
    if not path.is_file():
        return None
    if path.stat().st_size > 1024:
        raise ReplayLaunchError(f"oversized {kind} marker")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as e:
        raise ReplayLaunchError(f"invalid {kind} marker") from e
    if (not isinstance(obj,dict) or obj.get("schema") != 1 or
        obj.get("producer") != MARKER_PRODUCER or
        obj.get("event") != kind or obj.get("run_id") != run_id or
        obj.get("replay_mode") is not True or
        type(obj.get("frame")) is not int or obj["frame"] < max(1, after)):
        raise ReplayLaunchError(f"unverified {kind} marker")
    return obj


def winepath(path: Path, converter: str, env: dict) -> str:
    try:
        proc = subprocess.run(
            [converter,"-w",str(path.resolve())],env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=25)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise ReplayLaunchError("cannot invoke Wine path conversion") from e
    vals = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or len(vals) != 1 or not WIN_ABS.fullmatch(vals[0]):
        raise ReplayLaunchError("Wine did not resolve absolute Windows file path")
    return vals[0]


def command(wine_exe, draft_win, exe_win, mod_win, qdp_win):
    for arg in (draft_win,exe_win,mod_win,qdp_win):
        if not WIN_ABS.fullmatch(arg):
            raise ReplayLaunchError("MPQDraft paths must be absolute Windows paths")
    return [wine_exe,draft_win,"patch","--target",exe_win,
            "--mpq",mod_win,"--plugin",qdp_win]


@contextmanager
def lock(path):
    try:
        fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError as e:
        raise ReplayLaunchError(f"game installation already has an active renderer: {path}") from e
    try:
        os.write(fd,f"pid={os.getpid()}\n".encode())
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def terminate(proc):
    if proc is None or proc.poll() is not None:
        return
    if os.name=="posix":
        try:os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    else:
        proc.terminate()
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name=="posix":
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        else:proc.kill()
        proc.wait(timeout=5)


def playback(run_id, replay, mod, ready, done, game_dir, mpqdraft, qdp,
             observer, *, wine_exe="wine", winepath_exe="winepath", timeout=7200):
    if not isinstance(run_id,str) or not IDENT.fullmatch(run_id):
        raise ReplayLaunchError("invalid run_id")
    if type(timeout) is not int or not 30 <= timeout <= 14400:
        raise ReplayLaunchError("timeout outside 30..14400 seconds")
    replay=require_file(replay,"original replay")
    if not 30 <= replay.stat().st_size <= 64*1024*1024:
        raise ReplayLaunchError("replay size invalid")
    mod=require_file(mod,"custom MPQ")
    mpqdraft=require_file(mpqdraft,"modernized MPQDraft CLI")
    qdp=require_file(qdp,"BWAPI MPQDraft QDP injector")
    observer=require_file(observer,"linked Win32 BWAPI replay observer")
    game_dir=Path(game_dir).resolve()
    exe=require_file(game_dir/"StarCraft.exe","private game")
    ini=require_file(game_dir/"bwapi-data"/"bwapi.ini","private BWAPI ini")
    ready,done=Path(ready).resolve(),Path(done).resolve()
    if (ready==done or ready.exists() or done.exists()
        or ready.parent!=done.parent or not ready.parent.is_dir()):
        raise ReplayLaunchError("markers must be distinct new files in the same directory")
    if game_dir in ready.parents:
        raise ReplayLaunchError("marker output may not be inside game assets")
    for x in (replay,mod,mpqdraft,qdp,observer):
        if game_dir in x.parents:
            raise ReplayLaunchError("replay and all tools must live outside game dir")
    if not shutil.which(wine_exe) or not shutil.which(winepath_exe):
        raise ReplayLaunchError("Wine and winepath must exist")
    env=os.environ.copy()
    if not env.get("DISPLAY") or not env.get("WINEPREFIX") or not Path(env["WINEPREFIX"]).is_dir():
        raise ReplayLaunchError("DISPLAY and isolated existing WINEPREFIX are required")
    relative=f"maps/replays/TC_{run_id}.rep"
    staged=game_dir/relative
    source_ini=ini.read_bytes()
    process=None
    result=None
    with lock(game_dir/".tensorcraft_replay.lock"):
        if staged.exists():
            raise ReplayLaunchError("staged replay already exists")
        staged.parent.mkdir(parents=True,exist_ok=True)
        try:
            shutil.copyfile(replay,staged)
            if checksum(staged)!=checksum(replay):
                raise ReplayLaunchError("staged replay hash mismatch")
            ai_win=winepath(observer,winepath_exe,env)
            ini.write_text(configure_ini(source_ini.decode("utf-8-sig"),relative,ai_win),
                           encoding="utf-8")
            paths={key:winepath(src,winepath_exe,env) for key,src in
                   {"exe":exe,"mpqdraft":mpqdraft,"mod":mod,
                    "qdp":qdp,"ready":ready,"done":done}.items()}
            env.update({
                "TC_RENDER_RUN_ID":run_id,
                "TC_RENDER_READY_PATH":paths["ready"],
                "TC_RENDER_DONE_PATH":paths["done"],
                "TC_TRACE_PATH":"","TC_TRACE_RUN_ID":"","TC_TRACE_VARIANT":"",
            })
            argv=command(wine_exe,paths["mpqdraft"],paths["exe"],
                         paths["mod"],paths["qdp"])
            with (ready.parent/"mpqdraft_replay.log").open("xb") as log:
                process=subprocess.Popen(argv,cwd=game_dir,env=env,
                                         stdout=log,stderr=subprocess.STDOUT,
                                         start_new_session=True)
                start_frame=None
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    started=marker(ready,"ready",run_id)
                    if started is not None:
                        start_frame=started["frame"]
                    completed=marker(done,"done",run_id,after=start_frame or -1)
                    if completed is not None:
                        if start_frame is None:
                            raise ReplayLaunchError("end event without start event")
                        result={"schema":1,"run_id":run_id,
                                "status":"bwapi_replay_frame_and_end_callbacks",
                                "ready_frame":start_frame,"done_frame":completed["frame"],
                                "replay_sha256":checksum(replay),
                                "mod_mpq_sha256":checksum(mod),
                                "mpqdraft_sha256":checksum(mpqdraft),
                                "bwapi_qdp_sha256":checksum(qdp),
                                "observer_sha256":checksum(observer),
                                "actual_mod_effect_ever_verified":False,
                                "video_content_reviewed":False}
                        break
                    code=process.poll()
                    if code is not None and code != 0:
                        raise ReplayLaunchError(f"MPQDraft exited {code} before playback completion")
                    time.sleep(0.15)
                if result is None:
                    raise ReplayLaunchError("replay completion timeout (inspect mpqdraft_replay.log)")
            with (ready.parent/"replay_gui_receipt.json").open("x",encoding="utf-8") as handle:
                json.dump(result,handle,indent=2,sort_keys=True)
                handle.write("\n")
        finally:
            terminate(process)
            ini.write_bytes(source_ini)
            staged.unlink(missing_ok=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("replay","mod","ready","done","game-dir","mpqdraft","qdp","observer"):
        p.add_argument("--"+key,type=Path,required=True)
    p.add_argument("--display",required=True)
    p.add_argument("--run-id",required=True)
    p.add_argument("--wine-exe",default="wine")
    p.add_argument("--winepath-exe",default="winepath")
    p.add_argument("--timeout",type=int,default=7200)
    a=p.parse_args(argv)
    if a.display != os.environ.get("DISPLAY"):
        p.error("assigned DISPLAY differs from requested display")
    try:
        result=playback(a.run_id,a.replay,a.mod,a.ready,a.done,a.game_dir,
                        a.mpqdraft,a.qdp,a.observer,wine_exe=a.wine_exe,
                        winepath_exe=a.winepath_exe,timeout=a.timeout)
    except (ReplayLaunchError,UnicodeError,OSError) as e:
        print(f"Replay GUI failed: {e}",file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
