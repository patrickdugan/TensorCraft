"""Asset-free tests for documented MPQDraft + BWAPI automatic replay flow."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starcraft_lab.farm import replay_gui as gui, video


def marker_record(kind, run_id="trial01", frame=12, replay_mode=True):
    return {
        "schema":1,"producer":gui.MARKER_PRODUCER,"event":kind,
        "run_id":run_id,"replay_mode":replay_mode,"frame":frame,
    }


class GuiReplayTests(unittest.TestCase):
    def test_config_preserves_required_replay_mode(self):
        original=(
            "[ai]\nai = bwapi-data/AI/pluto.dll\n"
            "tournament = \n"
            "[auto_menu]\nauto_menu = OFF\nmap = maps/old.scx\n"
            "auto_restart = ON\nrace = Zerg\n"
        )
        updated=gui.configure_ini(
            original,"maps/replays/TC_trial01.rep","Z:\\lab\\TensorCraftTrace.dll")
        self.assertIn("ai = Z:\\lab\\TensorCraftTrace.dll",updated)
        self.assertIn("tournament = Z:\\lab\\TensorCraftTrace.dll",updated)
        self.assertIn("auto_menu = SINGLE_PLAYER",updated)
        self.assertIn("map = maps/replays/TC_trial01.rep",updated)
        self.assertIn("auto_restart = OFF",updated)
        self.assertIn("race = Zerg",updated)
        self.assertNotIn("ai = bwapi-data/AI/pluto.dll",updated)
        with self.assertRaises(gui.ReplayLaunchError):
            gui.configure_ini(original,"../../bad.rep","Z:\\lab\\trace.dll")
        with self.assertRaises(gui.ReplayLaunchError):
            gui.configure_ini(original,"maps/replays/TC_trial01.rep","/etc/trace.dll")

    def test_mpqdraft_mod_and_bwapi_plugin_are_both_required(self):
        cmd=gui.command("wine","Z:\\MPQDraft.exe","Z:\\StarCraft.exe",
                        "Z:\\speed.mpq","Z:\\BWAPI_PluginInjector.qdp")
        self.assertEqual(cmd[1:4],["Z:\\MPQDraft.exe","patch","--target"])
        self.assertIn("--mpq",cmd)
        self.assertIn("--plugin",cmd)
        with self.assertRaises(gui.ReplayLaunchError):
            gui.command("wine","Z:\\MPQDraft.exe","Z:\\StarCraft.exe",
                        "relative.mpq","Z:\\BWAPI_PluginInjector.qdp")

    def test_native_markers_are_structured_not_empty_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"replay.ready"
            self.assertIsNone(gui.marker(p,"ready","trial01"))
            p.write_text(json.dumps(marker_record("ready")))
            self.assertEqual(gui.marker(p,"ready","trial01")["frame"],12)
            with self.assertRaisesRegex(gui.ReplayLaunchError,"unverified"):
                gui.marker(p,"ready","different_id")
            p.write_text(json.dumps(marker_record("ready",replay_mode=False)))
            with self.assertRaisesRegex(gui.ReplayLaunchError,"unverified"):
                gui.marker(p,"ready","trial01")
            p.write_text(json.dumps(marker_record("done",frame=12)))
            with self.assertRaisesRegex(gui.ReplayLaunchError,"unverified"):
                gui.marker(p,"done","trial01",after=13)
            p.write_text("")
            with self.assertRaisesRegex(gui.ReplayLaunchError,"invalid"):
                gui.marker(p,"ready","trial01")

    def test_private_auto_menu_restored_after_fake_native_callbacks(self):
        """Mocks native Wine; success means contract works, NOT real gameplay."""
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            game=root/"private_game"
            (game/"bwapi-data").mkdir(parents=True)
            ini=game/"bwapi-data"/"bwapi.ini"
            before=(
                "[ai]\nai = bwapi-data/AI/pluto.dll\ntournament = \n"
                "[auto_menu]\nauto_menu = OFF\nmap = maps/other.scx\n"
            ).encode("utf-8")
            ini.write_bytes(before)
            (game/"StarCraft.exe").write_bytes(b"synthetic exe")
            rep=root/"real_source.rep"
            rep.write_bytes(bytes(12)+b"reRS"+bytes(120))
            mpq=root/"private_mod.mpq";mpq.write_bytes(b"dummy mpq")
            exe=root/"MPQDraft.exe";exe.write_bytes(b"dummy launch")
            qdp=root/"BWAPI_PluginInjector.qdp";qdp.write_bytes(b"dummy plugin")
            observer=root/"TensorCraftTrace.dll";observer.write_bytes(b"dummy x86 dll")
            prefix=root/"wine";prefix.mkdir()
            output=root/"output";output.mkdir()
            ready=output/"playback.ready"
            done=output/"playback.done"

            class FakeProcess:
                returncode=None
                calls=0
                def poll(self):
                    self.calls+=1
                    if self.calls==1:
                        ready.write_text(json.dumps(marker_record("ready",frame=2)))
                        done.write_text(json.dumps(marker_record("done",frame=400)))
                    if self.calls >= 2:
                        self.returncode=0
                        return 0
                    return None
                def wait(self,timeout=None):
                    self.returncode=0
                    return 0
            fake=FakeProcess()
            fake_windows=lambda p,conv,env: "Z:\\"+p.name
            with patch.dict(os.environ,{"DISPLAY":":121","WINEPREFIX":str(prefix)}), \
                 patch.object(gui.shutil,"which",return_value="/usr/bin/wine"), \
                 patch.object(gui,"winepath",side_effect=fake_windows), \
                 patch.object(gui.subprocess,"Popen",return_value=fake), \
                 patch.object(gui.time,"sleep"):
                result=gui.playback("trial01",rep,mpq,ready,done,game,exe,qdp,observer)
            self.assertEqual(result["status"],"bwapi_replay_frame_and_end_callbacks")
            self.assertFalse(result["actual_mod_effect_ever_verified"])
            self.assertEqual(result["ready_frame"],2)
            self.assertEqual(result["done_frame"],400)
            self.assertEqual(ini.read_bytes(),before)
            self.assertFalse((game/"maps"/"replays"/"TC_trial01.rep").exists())
            self.assertFalse((game/".tensorcraft_replay.lock").exists())
            receipt=json.loads((output/"replay_gui_receipt.json").read_text())
            self.assertEqual(receipt["replay_sha256"],gui.checksum(rep))


if __name__=="__main__":
    unittest.main()
