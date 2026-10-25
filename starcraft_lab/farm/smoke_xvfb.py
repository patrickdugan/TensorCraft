"""True Xvfb/x11grab/FFmpeg smoke with moving SYNTHETIC video content.

This is NOT gameplay footage. It proves the exact 640x480 virtual-display
capture path used by the StarCraft replay renderer can encode a 1080p MP4.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from .schema import FarmError
from .xdisplay import leased_xvfb, _stop
from .video import ffmpeg_argv, validate_mp4


def run_smoke(output_dir:Path, *, display=":173") -> dict:
    root=Path(output_dir).resolve()
    if root.exists():
        raise FarmError("synthetic X11 smoke directory already exists")
    root.mkdir(parents=True,exist_ok=False)
    ffplay=shutil.which("ffplay")
    ffmpeg=shutil.which("ffmpeg")
    ffprobe=shutil.which("ffprobe")
    if not all((ffplay,ffmpeg,ffprobe)):
        raise FarmError("FFplay/FFmpeg/FFprobe are required")
    movie=root/"X11_SYNTHETIC_TEST_NOT_STARCRAFT.mp4"
    player=None
    with leased_xvfb(display,root/"xvfb") as lease:
        env=os.environ.copy()
        env.update({
            "DISPLAY":lease["display"],
            "XAUTHORITY":lease["xauthority"],
            "SDL_VIDEODRIVER":"x11",
            "SDL_AUDIODRIVER":"dummy",
        })
        try:
            with (root/"ffplay.log").open("xb") as log:
                player=subprocess.Popen([
                    ffplay,"-hide_banner","-loglevel","error",
                    "-autoexit","-noborder",
                    "-f","lavfi","testsrc2=size=640x480:rate=30:duration=9"
                ],env=env,stdin=subprocess.DEVNULL,
                  stdout=log,stderr=subprocess.STDOUT,
                  start_new_session=True)
            time.sleep(1.3)
            if player.poll() is not None:
                raise FarmError("synthetic X11 test window exited before screen capture")
            with (root/"ffmpeg.log").open("xb") as output:
                p=subprocess.run(ffmpeg_argv(ffmpeg,display,movie,5),
                    env=env,stdin=subprocess.DEVNULL,stdout=output,
                    stderr=subprocess.STDOUT,timeout=100,check=False)
            if p.returncode!=0:
                raise FarmError("Xvfb x11grab encoder failed, inspect ffmpeg.log")
        finally:
            _stop(player) if player is not None else None
    metadata=validate_mp4(movie,ffprobe)
    metadata.update({
        "producer":"Xvfb_ffplay_synthetic_testsrc2_x11grab",
        "x11_capture_path_verified":True,
        "actual_starcraft_gameplay":False,
        "native_bwapi_replay_playback_tested":False,
        "not_youtube_ready_gameplay":"test pattern only; MPQ and game still need local smoke",
    })
    with (root/"receipt.json").open("x",encoding="utf-8") as receipt:
        json.dump(metadata,receipt,indent=2,sort_keys=True)
        receipt.write("\n")
    return metadata


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--display",default=":173")
    args=p.parse_args()
    print(json.dumps(run_smoke(args.output_dir,display=args.display),
                     indent=2,sort_keys=True))


if __name__=="__main__":
    main()
