"""Render-queue tests use forged parser outputs, never actual game replays."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest

from starcraft_lab.farm import render_queue, runner
from starcraft_lab.farm.schema import FarmError
from starcraft_lab.tests.test_farm import fixture


class RenderQueueTests(unittest.TestCase):
    def _prepared(self,root):
        pf,rf,_,_=fixture(root,workers=2,pairs=2)
        runner.prepare(pf,rf,root/"out")
        results=runner.execute(root/"out",allow_native=True)
        return results

    def test_no_unparsed_games_scheduled_for_capture(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self._prepared(root)
            selected=render_queue.choose(root/"out",root/"selection",count=3)
            self.assertEqual(selected["eligible_replays"],0)
            self.assertEqual(selected["selected"],[])
            self.assertFalse(selected["gameplay_authenticity_proven"])

    def test_parser_backed_replays_ranked_without_claiming_video(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            aggregate=self._prepared(root)
            recs=aggregate["records"]
            for i,path in enumerate(recs[:2]):
                receipt=json.loads(Path(path).read_text())
                receipt["status"]="parsed_replay"
                Path(path).write_text(json.dumps(receipt))
                src=Path(receipt["replay"])
                probe={
                    "schema":1,"producer":"starcraft-replay-probe/replay-core",
                    "replay_sha256":receipt["replay_sha256"],
                    "engine_type":"BroodWar",
                    "command_warnings":{"clean":True},
                    "frame_count":6000,
                    "players":[{"unit_orders":{
                        "devourer":{"issued_orders":5 if i==1 else 0},
                        "mutalisk":{"issued_orders":9},
                        "guardian":{"issued_orders":0},
                        "scout":{"issued_orders":0},
                        "corsair":{"issued_orders":0},
                    }}],
                }
                (src.parent/"replay_probe.json").write_text(json.dumps(probe))
            selection=render_queue.choose(root/"out",root/"selection",count=2)
            self.assertEqual(selection["eligible_replays"],2)
            self.assertEqual(len(selection["selected"]),2)
            self.assertEqual(selection["selected"][0]["issued_orders"]["devourer"],5)
            self.assertFalse(selection["selected"][0]["gameplay_video_verified"])
            with self.assertRaisesRegex(FarmError,"directory already exists"):
                render_queue.choose(root/"out",root/"selection",count=2)

    def test_invalid_parser_outputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            aggregate=self._prepared(root)
            path=Path(aggregate["records"][0])
            receipt=json.loads(path.read_text())
            receipt["status"]="parsed_replay"
            path.write_text(json.dumps(receipt))
            rep=Path(receipt["replay"])
            (rep.parent/"replay_probe.json").write_text(json.dumps({
                "schema":1,"producer":"forged-output",
                "replay_sha256":receipt["replay_sha256"],
                "engine_type":"BroodWar",
                "command_warnings":{"clean":True},
                "frame_count":400,
            }))
            with self.assertRaisesRegex(FarmError,"parser evidence"):
                render_queue.candidates(root/"out")

    def test_render_pool_rejects_overcommit_and_shared_prefixes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            game0=root/"v0";game0.mkdir()
            game1=root/"v1";game1.mkdir()
            w0=root/"p0";w0.mkdir()
            w1=root/"p1";w1.mkdir()
            pool={
                "schema":1,
                "max_parallel":2,
                "capacity":{"cpu_budget_threads":6,"threads_per_render":2,
                            "ram_budget_mb":6000,"ram_per_render_mb":2500},
                "replay_launcher":["python3","replay.py",
                    "{replay}","{mod_mpq}","{ready_marker}","{done_marker}","{display}"],
                "slots":[{"id":"v0","game_dir":str(game0),"wineprefix":str(w0),"display":":121"},
                         {"id":"v1","game_dir":str(game1),"wineprefix":str(w1),"display":":122"}],
            }
            n,slots=render_queue._validate_pool(pool,[])
            self.assertEqual(n,2)
            pool["slots"][1]["wineprefix"]=str(w0)
            with self.assertRaisesRegex(FarmError,"duplicate render"):
                render_queue._validate_pool(pool,[])
            pool["slots"][1]["wineprefix"]=str(w1)
            pool["capacity"]["cpu_budget_threads"]=2
            with self.assertRaisesRegex(FarmError,"CPU oversubscribed"):
                render_queue._validate_pool(pool,[])
            pool["capacity"]["cpu_budget_threads"]=6
            pool["capacity"]["ram_budget_mb"]=3000
            with self.assertRaisesRegex(FarmError,"RAM oversubscribed"):
                render_queue._validate_pool(pool,[])


if __name__=="__main__":
    unittest.main()
