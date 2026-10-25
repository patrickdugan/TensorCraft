"""Private Xvfb displays for modded StarCraft workers on Linux/Wine.

Each slot gets its own DISPLAY and Xauthority; X11 TCP is disabled. A
managed display still renders StarCraft inside a virtual framebuffer,
so this is not equivalent to an accelerated rendering-free engine.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import time

from .schema import FarmError


def _stop(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try: os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:pass
        else:
            proc.kill()
        proc.wait(timeout=5)


@contextmanager
def leased_xvfb(display: str, directory: Path, *, timeout: float = 12.0):
    if os.name!="posix":
        raise FarmError("managed Xvfb is supported only on Linux/WSL/Wine")
    if not isinstance(display,str) or not re.fullmatch(r":[1-9][0-9]{0,3}",display):
        raise FarmError("invalid private X11 display number")
    root=Path(directory).resolve()
    if root.exists():
        raise FarmError("managed display output directory must be new")
    paths={k:shutil.which(k) for k in ("Xvfb","xauth","xdpyinfo")}
    if not all(paths.values()):
        raise FarmError("install Xvfb, xauth and xdpyinfo")
    root.mkdir(parents=True,exist_ok=False)
    auth=root/".Xauthority"
    cookie=secrets.token_hex(16)
    gen=subprocess.run([paths["xauth"],"-f",str(auth),"add",display,".",cookie],
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12)
    if gen.returncode!=0:
        raise FarmError("could not provision private Xauthority")
    auth.chmod(0o600)
    env=os.environ.copy()
    env.update({"DISPLAY":display,"XAUTHORITY":str(auth)})
    process=None
    with (root/"xvfb.log").open("xb") as log:
        process=subprocess.Popen(
            [paths["Xvfb"],display,"-screen","0","640x480x24",
             "-nolisten","tcp","-auth",str(auth)],
            start_new_session=True,env=env,stdout=log,
            stderr=subprocess.STDOUT)
    try:
        deadline=time.monotonic()+timeout
        verified=False
        while time.monotonic()<deadline:
            if process.poll() is not None:
                break
            probe=subprocess.run([paths["xdpyinfo"],"-display",display],
                                 env=env,capture_output=True,timeout=5)
            if probe.returncode==0:
                verified=True
                break
            time.sleep(0.2)
        if not verified:
            raise FarmError("managed Xvfb display failed readiness check")
        yield {"display":display,"xauthority":str(auth),"pid":process.pid}
    finally:
        _stop(process)
