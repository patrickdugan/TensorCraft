"""Asset-free encoder certification: FFmpeg test pattern, never StarCraft footage.

Produces a short synthetic H.264/AAC 1080p MP4 and JSON receipt for CI.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from .video import VIDEO_FILTER, validate_mp4
from .schema import FarmError


def build_sample(destination:Path) -> dict:
    target=Path(destination).resolve()
    if target.exists() or not target.parent.is_dir():
        raise FarmError("target must not exist and its parent must exist")
    ffmpeg=shutil.which("ffmpeg")
    ffprobe=shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FarmError("FFmpeg and FFprobe are required")
    proc=subprocess.run([
        ffmpeg,"-hide_banner","-loglevel","error","-y",
        "-f","lavfi","-i","testsrc2=size=640x480:rate=30:duration=6",
        "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t","5.0","-vf",VIDEO_FILTER,"-r","30",
        "-c:v","libx264","-preset","ultrafast","-crf","22",
        "-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","160k","-ar","48000","-ac","2",
        "-movflags","+faststart","-shortest",str(target),
    ],capture_output=True,text=True,timeout=120,check=False)
    if proc.returncode!=0:
        raise FarmError(f"synthetic video encoder failed: {proc.stderr[-1200:]}")
    report=validate_mp4(target,ffprobe)
    report.update({
        "producer":"synthetic_ffmpeg_test_pattern",
        "actual_starcraft_footage":False,
        "bwapi_replay_playback_verified":False,
        "gameplay_visual_content_verified":False,
        "purpose":"codec, duration, picture geometry and faststart verification only",
    })
    with target.with_suffix(".receipt.json").open("x",encoding="utf-8") as out:
        json.dump(report,out,indent=2,sort_keys=True)
        out.write("\n")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(build_sample(args.output),indent=2,sort_keys=True))


if __name__=="__main__":
    main()
