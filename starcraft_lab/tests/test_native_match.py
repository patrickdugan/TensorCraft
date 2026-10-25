"""Native match adapter contracts, tested without installing proprietary games."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starcraft_lab.farm import native_match as nm, replay_gui as rg, runner


def event(kind, run_id="trial01", frame=20):
    return {"schema":1,"producer":"bwapi_tournament_match_marker",
            "event":kind,"run_id":run_id,"replay_mode":False,"frame":frame}


class NativeMatchTests(unittest.TestCase):
    def test_pluto_match_menu_config_preserves_main_ai(self):
        source=(
            "[ai]\nai = bwapi-data/AI/pluto.dll\n"
            "tournament = \n[auto_menu]\nauto_menu = OFF\n"
            "map = maps/other.scx\nrace = Protoss\nenemy_race = Terran\n"
        )
        result=nm.configure_match_ini(
            source,"maps/TC_trial01.scx","maps/replays/TC_trial01.rep",
            "Z:\\lab\\TensorCraftTrace.dll",race="Zerg",enemy_race="Zerg")
        self.assertIn("ai = bwapi-data/AI/pluto.dll",result)
        self.assertIn("tournament = Z:\\lab\\TensorCraftTrace.dll",result)
        self.assertIn("auto_menu = SINGLE_PLAYER",result)
        self.assertIn("map = maps/TC_trial01.scx",result)
        self.assertIn("save_replay = maps/replays/TC_trial01.rep",result)
        self.assertIn("enemy_count = 1",result)
        with self.assertRaisesRegex(nm.ReplayLaunchError,"requires ai=pluto.dll"):
            nm.configure_match_ini(source.replace("pluto.dll","OtherAI.dll"),
                "maps/TC_trial01.scx","maps/replays/TC_trial01.rep",
                "Z:\\lab\\Trace.dll",race="Zerg",enemy_race="Zerg")
        with self.assertRaisesRegex(nm.ReplayLaunchError,"invalid 1v1 races"):
            nm.configure_match_ini(source,"maps/TC_trial01.scx",
                "maps/replays/TC_trial01.rep","Z:\\lab\\Trace.dll",
                race="Random",enemy_race="Zerg")

    def test_synthetic_native_callback_contract_and_ini_restoration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            game=root/"private"
            (game/"bwapi-data").mkdir(parents=True)
            ini=game/"bwapi-data"/"bwapi.ini"
            contents=(
                "[ai]\nai = bwapi-data/AI/pluto.dll\n"
                "tournament = \n[auto_menu]\nauto_menu = OFF\n"
                "map = maps/other.scx\n"
            ).encode("utf-8")
            ini.write_bytes(contents)
            (game/"StarCraft.exe").write_bytes(b"not actual StarCraft")
            mapfile=root/"test.scx";mapfile.write_bytes(b"not actual map")
            mpq=root/"test.mpq";mpq.write_bytes(b"fake mpq")
            mpqdraft=root/"MPQDraft.exe";mpqdraft.write_bytes(b"fake cli")
            qdp=root/"BWAPI_PluginInjector.qdp";qdp.write_bytes(b"fake plugin")
            observer=root/"TensorCraftTrace.dll";observer.write_bytes(b"fake observer")
            prefix=root/"wine";prefix.mkdir()
            out=root/"out";out.mkdir()
            replay=out/"trial01.rep"
            ready=out/"bwapi_match.ready"
            done=out/"bwapi_match.done"
            staged_rep=game/"maps"/"replays"/"TC_trial01.rep"
            class FakeProcess:
                code=None
                loops=0
                def poll(self):
                    self.loops+=1
                    if self.loops==1:
                        ready.write_text(json.dumps(event("ready",frame=2)))
                        done.write_text(json.dumps(event("done",frame=1700)))
                        staged_rep.parent.mkdir(parents=True,exist_ok=True)
                        staged_rep.write_bytes(bytes(12)+b"reRS"+bytes(150))
                    if self.loops>=2:
                        self.code=0
                        return 0
                    return None
                def wait(self,timeout=None):
                    self.code=0
                    return 0
            fake=FakeProcess()
            with patch.dict(os.environ,{"DISPLAY":":91","WINEPREFIX":str(prefix)}), \
                 patch.object(nm.shutil,"which",return_value="/usr/bin/wine"), \
                 patch.object(nm,"winepath",side_effect=lambda p,c,e:"Z:\\"+p.name), \
                 patch.object(nm.subprocess,"Popen",return_value=fake), \
                 patch.object(nm.time,"sleep",return_value=None):
                result=nm.run_native_match(
                    run_id="trial01",game_dir=game,map_file=mapfile,
                    mod_mpq=mpq,output_replay=replay,
                    mpqdraft=mpqdraft,qdp=qdp,observer=observer,
                    race="Zerg",enemy_race="Zerg")
            self.assertEqual(result["status"],"native_bwapi_game_finished_with_saved_replay")
            self.assertFalse(result["true_bwheadless"])
            self.assertFalse(result["actual_mod_stats_verified_in_game"])
            self.assertFalse(result["game_outcome_referee_verified"])
            self.assertEqual(ini.read_bytes(),contents)
            self.assertFalse((game/"maps"/"TC_trial01.scx").exists())
            self.assertFalse(staged_rep.exists())
            self.assertFalse((game/".tensorcraft_match.lock").exists())
            self.assertEqual(result["output_replay_sha256"],nm.checksum(replay))
            self.assertEqual(result["end_frame"],1700)
            self.assertTrue((out/"native_match_receipt.json").is_file())
            self.assertTrue(runner._native_match_callback_attested(
                out,replay,nm.checksum(mpq),nm.checksum(mapfile),"trial01"))
            receipt_path=out/"native_match_receipt.json"
            tampered=json.loads(receipt_path.read_text())
            tampered["mod_mpq_sha256"]="0"*64
            receipt_path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(runner.FarmError,"receipt does not match"):
                runner._native_match_callback_attested(
                    out,replay,nm.checksum(mpq),nm.checksum(mapfile),"trial01")

    def test_false_match_marker_rejected_by_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"ready"
            p.write_text(json.dumps(event("ready")))
            self.assertIsNotNone(rg.marker(p,"ready","trial01",replay_mode=False))
            with self.assertRaisesRegex(rg.ReplayLaunchError,"unverified"):
                rg.marker(p,"ready","trial01",replay_mode=True)
            p.write_text(json.dumps({**event("ready"),"producer":"made_up"}))
            with self.assertRaisesRegex(rg.ReplayLaunchError,"unverified"):
                rg.marker(p,"ready","trial01",replay_mode=False)


if __name__=="__main__":
    unittest.main()
