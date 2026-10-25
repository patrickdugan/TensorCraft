"""Virtual-display StarCraft replay capture -> auditable YouTube MP4.

One virtual X11 display per rendering worker. Headless match processes DO NOT
render. A separate Wine replay GUI is captured through Xvfb + FFmpeg x11grab.
Full native replay automation is not assumed: the external playback wrapper
must signal actual playback started and finished, and a human must still
review the resulting footage for correctness.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import time

from .schema import FarmError, hash_file, HEX, safe_id, canonical_file

VIDEO_W = 1920
VIDEO_H = 1080
FPS = 30
INPUT_W = 640
INPUT_H = 480
# Preserve native Brood War 4:3 geometry, pillarboxed at 1920x1080.
VIDEO_FILTER = ("scale=1440:1080:flags=lanczos,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=yuv420p")
TEMPLATE_VALUES = frozenset({
    "replay", "mod_mpq", "ready_marker", "done_marker", "display",
    "render_dir", "game_dir", "run_id",
})


def _validate_render_script(argv: object):
    if not isinstance(argv,list) or not 1 <= len(argv) <= 40:
        raise FarmError("replay_launcher argv missing")
    import string
    found=set()
    formatter=string.Formatter()
    for part in argv:
        if not isinstance(part,str) or not part or len(part)>4096:
            raise FarmError("bad replay launcher argument")
        try:
            tokens=list(formatter.parse(part))
        except ValueError as e:
            raise FarmError("invalid replay launcher template") from e
        for _,field,formatting,conversion in tokens:
            if field is not None:
                if field not in TEMPLATE_VALUES or formatting or conversion:
                    raise FarmError(f"unsupported replay launcher template: {field}")
                found.add(field)
    required={"replay","mod_mpq","ready_marker","done_marker","display"}
    if not required <= found:
        raise FarmError(f"replay launcher omitted mandatory placeholders: {sorted(required-found)}")
    return found


def render_spec(spec:dict, out_dir:Path)->dict:
    if not isinstance(spec,dict) or spec.get("schema")!=1:
        raise FarmError("render schema must be 1")
    run_id=safe_id(spec.get("run_id"),"run_id")
    replay=canonical_file(spec.get("replay"),"replay")
    mod=canonical_file(spec.get("mod_mpq"),"mod_mpq")
    expected_replay=spec.get("replay_sha256")
    expected_mod=spec.get("mod_mpq_sha256")
    if not isinstance(expected_replay,str) or HEX.fullmatch(expected_replay) is None:
        raise FarmError("missing pinned replay SHA-256")
    if not isinstance(expected_mod,str) or HEX.fullmatch(expected_mod) is None:
        raise FarmError("missing pinned mod MPQ SHA-256")
    if hash_file(replay)!=expected_replay or hash_file(mod)!=expected_mod:
        raise FarmError("replay or mod MPQ changed after recording")
    if replay.stat().st_size < 30:
        raise FarmError("replay too small")
    launcher=spec.get("replay_launcher")
    _validate_render_script(launcher)
    display=spec.get("display")
    if not isinstance(display,str) or re.fullmatch(r":[1-9][0-9]{0,3}",display) is None:
        raise FarmError("render display must be an explicit private X11 :N")
    for key in ("capture_limit_seconds","ready_timeout_seconds"):
        v=spec.get(key)
        if type(v) is not int or v<3 or v>7200:
            raise FarmError(f"{key} must be integer 3..7200")
    game_dir=spec.get("game_dir")
    if not isinstance(game_dir,str) or not Path(game_dir).is_dir():
        raise FarmError("render game_dir missing")
    game_dir=str(Path(game_dir).resolve())
    if out_dir.resolve()==Path(game_dir) or Path(game_dir) in out_dir.resolve().parents:
        raise FarmError("render output inside game installation")
    if out_dir.exists():
        raise FarmError("render output directory must be new")
    binary=dict()
    for field,default in (("xvfb_exe","Xvfb"),("xdpyinfo_exe","xdpyinfo"),
                          ("xauth_exe","xauth"),("ffmpeg_exe","ffmpeg"),
                          ("ffprobe_exe","ffprobe")):
        value=spec.get(field,default)
        if not isinstance(value,str) or not value:
            raise FarmError(f"invalid {field}")
        candidate=shutil.which(value)
        if candidate is None:
            raise FarmError(f"missing executable {field}: {value}")
        binary[field]=candidate
    return {
        "run_id":run_id,"replay":str(replay),"mod_mpq":str(mod),
        "replay_sha256":expected_replay,"mod_mpq_sha256":expected_mod,
        "display":display,"launcher":launcher,
        "game_dir":game_dir,"capture_limit_seconds":spec["capture_limit_seconds"],
        "ready_timeout_seconds":spec["ready_timeout_seconds"],
        "binary":binary,
    }


def ffmpeg_argv(executable:str, display:str, video_path:Path, seconds:int) -> list[str]:
    """H.264 + silent stereo AAC, 1920x1080, 30p, MP4 faststart."""
    return [
        executable,"-nostdin","-hide_banner","-loglevel","warning","-y",
        "-f","x11grab","-framerate",str(FPS),
        "-video_size",f"{INPUT_W}x{INPUT_H}","-draw_mouse","0",
        "-i",f"{display}.0",
        "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",str(seconds),
        "-vf",VIDEO_FILTER,
        "-r",str(FPS),
        "-c:v","libx264","-preset","medium","-crf","18",
        "-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","160k","-ar","48000","-ac","2",
        "-movflags","+faststart","-shortest",
        "-f","mp4",str(video_path),
    ]


def _faststart(path:Path)->bool:
    """Top-level MP4 boxes must contain moov before mdat."""
    moov_pos=mdat_pos=None
    try:
        with path.open("rb") as stream:
            offset=0
            limit=path.stat().st_size
            while offset+8 <= limit and offset < 32*1024*1024:
                stream.seek(offset)
                head=stream.read(8)
                size,kind=struct.unpack(">I4s",head)
                if size==1:
                    extra=stream.read(8)
                    if len(extra)<8:
                        return False
                    size=struct.unpack(">Q",extra)[0]
                elif size==0:
                    size=limit-offset
                if size<8 or size>limit-offset:
                    return False
                if kind==b"moov":moov_pos=offset
                if kind==b"mdat":
                    mdat_pos=offset
                    break
                offset+=size
    except (OSError,struct.error):
        return False
    return moov_pos is not None and mdat_pos is not None and moov_pos<mdat_pos


def validate_mp4(path:Path, ffprobe_exe:str, *, min_duration:float=3.0)->dict:
    if not path.is_file() or path.stat().st_size<32*1024:
        raise FarmError("video file absent or suspiciously short")
    proc=subprocess.run([
        ffprobe_exe,"-v","error","-show_streams","-show_format",
        "-of","json",str(path)
    ],capture_output=True,text=True,timeout=35,check=False)
    if proc.returncode!=0:
        raise FarmError("ffprobe failed to decode video metadata")
    try:
        data=json.loads(proc.stdout)
        streams=data["streams"]
        fmt=data["format"]
    except (KeyError,TypeError,ValueError) as exc:
        raise FarmError("ffprobe returned invalid JSON") from exc
    video=[x for x in streams if x.get("codec_type")=="video"]
    audio=[x for x in streams if x.get("codec_type")=="audio"]
    if len(video)!=1 or len(audio)!=1:
        raise FarmError("expected exactly one video and stereo audio track")
    v,a=video[0],audio[0]
    if (v.get("codec_name")!="h264" or v.get("width")!=VIDEO_W or
        v.get("height")!=VIDEO_H or v.get("pix_fmt")!="yuv420p" or
        v.get("r_frame_rate")!="30/1" or a.get("codec_name")!="aac" or
        int(a.get("sample_rate","0"))!=48000 or a.get("channels")!=2):
        raise FarmError("video does not match YouTube H264/AAC 1080p30 profile")
    try:
        duration=float(fmt["duration"])
    except (KeyError,ValueError,TypeError) as exc:
        raise FarmError("missing MP4 duration") from exc
    if not min_duration<=duration<=7201:
        raise FarmError("video duration out of range")
    if not _faststart(path):
        raise FarmError("MP4 has no verified faststart index")
    return {
        "schema":1,"video_sha256":hash_file(path),
        "video_codec":v["codec_name"],"width":VIDEO_W,"height":VIDEO_H,
        "fps":FPS,"pixel_format":"yuv420p","audio_codec":"aac",
        "audio_source":"generated_silence_no_game_audio",
        "duration_seconds":round(duration,3),
        "faststart_moov_before_mdat":True,
        "youtube_container_and_codec_compatible":True,
        "gameplay_visual_content_verified":False,
        "source_replay_playback_verified":False,
    }


def _stop_process(proc:subprocess.Popen|None):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def capture(spec:dict, out_dir:Path, *, execute:bool=False)->dict:
    root=Path(out_dir).resolve()
    verified=render_spec(spec,root)
    if not execute:
        raise FarmError("video capture is disabled by default; pass --execute after installing assets")
    root.mkdir(parents=True,exist_ok=False)
    run=verified["run_id"]
    ready=root/"playback.ready"
    done=root/"playback.done"
    partial=root/(run+".partial.mp4")
    finished=root/(run+".mp4")
    binary=verified["binary"]
    auth=root/".Xauthority"
    env=os.environ.copy()
    env["DISPLAY"]=verified["display"]
    env["XAUTHORITY"]=str(auth)
    xcookie=secrets.token_hex(16)
    # Do not use -ac. Restrict the X server to localhost UNIX sockets and a
    # private X cookie so concurrent workers cannot trivially see each other.
    prep=subprocess.run([binary["xauth_exe"],"-f",str(auth),"add",
                         verified["display"],".",xcookie],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
    if prep.returncode!=0:
        raise FarmError("could not create private Xauthority file")
    auth.chmod(0o600)
    playback=None
    recorder=None
    xvfb=None
    ready_seen=False
    playback_finished=False
    ffmpeg_return=None
    errors=[]
    with ExitStack() as stack:
        with (root/"xvfb.log").open("xb") as xvfb_log:
            xvfb=subprocess.Popen([
                binary["xvfb_exe"],verified["display"],"-screen","0",
                f"{INPUT_W}x{INPUT_H}x24","-nolisten","tcp",
                "-auth",str(auth),
            ],env=env,stdout=xvfb_log,stderr=subprocess.STDOUT)
        stack.callback(_stop_process,xvfb)
        usable=False
        for _ in range(30):
            if xvfb.poll() is not None:
                break
            check=subprocess.run([binary["xdpyinfo_exe"],"-display",
                                  verified["display"]],
                                 env=env,capture_output=True,timeout=5)
            if check.returncode==0:
                usable=True
                break
            time.sleep(.2)
        if not usable:
            raise FarmError("private Xvfb display did not become ready")
        mapping={
            "replay":verified["replay"],"mod_mpq":verified["mod_mpq"],
            "ready_marker":str(ready),"done_marker":str(done),
            "display":verified["display"],"render_dir":str(root),
            "game_dir":verified["game_dir"],"run_id":run,
        }
        argv=[v.format_map(mapping) for v in verified["launcher"]]
        env["TC_REPLAY_SHA256"]=verified["replay_sha256"]
        env["TC_MOD_MPQ_SHA256"]=verified["mod_mpq_sha256"]
        # The launcher must handle real 1.16.1 replay GUI loading with the
        # same private mod MPQ; there is no fake "headless replay renderer".
        with (root/"playback.log").open("xb") as log:
            playback=subprocess.Popen(argv,env=env,cwd=verified["game_dir"],
                                      stdout=log,stderr=subprocess.STDOUT)
        stack.callback(_stop_process,playback)
        deadline=time.monotonic()+verified["ready_timeout_seconds"]
        while time.monotonic()<deadline:
            if ready.is_file():
                ready_seen=True
                break
            if playback.poll() is not None:
                errors.append("replay_launcher_exited_before_playback_ready")
                break
            time.sleep(.1)
        if not ready_seen:
            raise FarmError("replay playback was never confirmed ready; see playback.log")
        ffargv=ffmpeg_argv(binary["ffmpeg_exe"],verified["display"],partial,
                          verified["capture_limit_seconds"])
        with (root/"ffmpeg.log").open("xb") as log:
            recorder=subprocess.Popen(ffargv,env=env,
                stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT)
        stack.callback(_stop_process,recorder)
        deadline=time.monotonic()+verified["capture_limit_seconds"]+8
        while time.monotonic()<deadline:
            if done.is_file():
                playback_finished=True
                if recorder.poll() is None:
                    try:
                        recorder.stdin.write(b"q\n")
                        recorder.stdin.flush()
                    except (BrokenPipeError,OSError):
                        pass
                break
            if playback.poll() is not None:
                errors.append("replay_launcher_ended_without_done_marker")
                break
            if recorder.poll() is not None:
                errors.append("encoder_exited_before_replay_finish")
                break
            time.sleep(.2)
        if recorder.poll() is None:
            try:
                recorder.wait(timeout=12)
            except subprocess.TimeoutExpired:
                errors.append("video_encoder_exceeded_capture_deadline")
                _stop_process(recorder)
        ffmpeg_return=recorder.returncode
        if ffmpeg_return!=0:
            errors.append(f"ffmpeg_exit_{ffmpeg_return}")
        if not playback_finished:
            errors.append("replay_completion_not_attested")
    report={
        "schema":1,"run_id":run,"replay_sha256":verified["replay_sha256"],
        "mod_mpq_sha256":verified["mod_mpq_sha256"],
        "playback_ready_marker_seen":ready_seen,
        "playback_done_marker_seen":playback_finished,
        "ffmpeg_exit_code":ffmpeg_return,"errors":sorted(set(errors)),
        "full_game_replay_correctness_confirmed":False,
        "youtube_visual_review_required":True,
    }
    if partial.is_file() and ffmpeg_return==0:
        try:
            info=validate_mp4(partial,binary["ffprobe_exe"])
        except FarmError as exc:
            report["errors"].append(f"mp4_invalid:{exc}")
        else:
            partial.rename(finished)
            report["video"]=str(finished)
            report["video_properties"]=info
            # Captures may be a menu, a wrong replay, or wrong patch.
            report["status"]=("encoded_full_playback_candidate" if playback_finished
                              else "encoded_partial_candidate")
    if "status" not in report:
        report["status"]="capture_failed"
    with (root/"receipt.json").open("x",encoding="utf-8") as f:
        json.dump(report,f,indent=2,sort_keys=True)
        f.write("\n")
    return report


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="command",required=True)
    chk=sub.add_parser("verify")
    chk.add_argument("--video",type=Path,required=True)
    chk.add_argument("--ffprobe-exe",default="ffprobe")
    run=sub.add_parser("capture")
    run.add_argument("--config",type=Path,required=True)
    run.add_argument("--output-dir",type=Path,required=True)
    run.add_argument("--execute",action="store_true")
    args=ap.parse_args(argv)
    if args.command=="verify":
        result=validate_mp4(args.video,args.ffprobe_exe)
    else:
        result=capture(json.loads(args.config.read_text(encoding="utf-8")),
                       args.output_dir,execute=args.execute)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if args.command=="verify" or result["status"]=="encoded_full_playback_candidate" else 2


if __name__=="__main__":
    raise SystemExit(main())
