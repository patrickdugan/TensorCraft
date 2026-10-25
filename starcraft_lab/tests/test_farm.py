"""Asset-free CI tests for concurrent worker isolation and video recipes.

Mock replay envelopes never count as verified games. Synthetic FFmpeg patterns
test only YouTube container properties, not authentic StarCraft footage.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from starcraft_lab.batch import PINNED_FILES
from starcraft_lab.farm import schema, runner, video


def dig(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(home: Path, *, workers=2, pairs=2):
    root=home
    basic={}
    for variant in ("control","devourer_speed_550"):
        original=root/("original-"+variant)
        original.mkdir()
        for key,rel in PINNED_FILES.items():
            target=original/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(key.encode()+b"-pinned")
        basic[variant]=original
    map_file=root/"map.scx"
    map_file.write_bytes(b"test map bytes")
    mods={}
    for v in basic:
        mpq=root/(v+".mpq")
        mpq.write_bytes(v.encode()+b" \x00\x01 fake mod archive")
        mods[v]={"mpq":str(mpq),"sha256":schema.hash_file(mpq)}
    seed=b"seq 40\nend\n"
    source=root/"plan"
    source.mkdir()
    (source/"bandit_baseline.txt").write_bytes(seed)
    games=[]
    for i in range(pairs):
        for v in basic:
            m={
                "schema":1,"run_id":f"p{i}-{v}","pair_id":f"p{i}",
                "variant":v,"engine":"starcraft_1161","agent":"pluto",
                "bandit_initial_sha256":dig(seed),
                "map_sha256":dig(map_file.read_bytes()),
                "seed":i+700,"spawn":"A",
                "assignment_index":i*2+(1 if v=="control" else 2),
            }
            for key,rel in PINNED_FILES.items():
                m[key]=schema.hash_file(basic[v]/rel)
            games.append({
                "manifest":m,
                "game_dir":str(basic[v]),
                "map_file":str(map_file),
                "mod_file":str(mods[v]["mpq"]),
            })
    plan={"schema":1,"games":games,
          "source_bandit_sha256":dig(seed),"source_map_sha256":dig(map_file.read_bytes())}
    plan_file=source/"plan.json"
    plan_file.write_text(json.dumps(plan))
    slots=[]
    for i in range(workers):
        private=root/f"slot{i}"
        prefix=root/f"prefix{i}"
        prefix.mkdir()
        private.mkdir()
        for key,rel in PINNED_FILES.items():
            path=private/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(key.encode()+b"-pinned")
        slots.append({
            "id":f"worker{i}","game_dir":str(private),"wineprefix":str(prefix),
            "display":f":{91+i}","port_base":45000+i*100,
            "bandit_paths":[str(private/"bwapi-data"/"read"/"pluto_bandit_Enemy.txt"),
                            str(private/"bwapi-data"/"write"/"pluto_bandit_Enemy.txt")],
        })
    # A source-level mock: creates a plausible envelope but not a valid replay
    # body. The runner must NOT claim the replay was independently parsed.
    mock='import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"\x00"*12+b"reRS"+b"\x00"*100)'
    # Encode Python command text without literal NUL bytes.
    mock='from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(bytes(12)+b"reRS"+bytes(100))'
    runtime={
        "schema":1,"mods":mods,"capacity":{
            "max_parallel":min(workers,2),
            "cpu_budget_threads":12,"threads_per_match":6,
            "ram_budget_mb":12000,"ram_per_match_mb":5000,
        },
        "slots":slots,
        "launcher":{"argv":[sys.executable,"-c",mock,"{replay_path}",
                           "{run_id}","{map_path}","{mod_mpq}"],
                    "timeout_seconds":120},
    }
    runtime_file=root/"runtime.json"
    runtime_file.write_text(json.dumps(runtime))
    return plan_file,runtime_file,plan,runtime


class FarmSchemaTests(unittest.TestCase):
    def test_prepare_isolation_and_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pf,rf,plan,spec=fixture(root)
            prepared=runner.prepare(pf,rf,root/"out")
            self.assertEqual(prepared["pairs"],2)
            self.assertEqual(prepared["games"],4)
            self.assertEqual(prepared["max_parallel"],2)
            self.assertFalse(prepared["native_headless_backend_verified"])
            with self.assertRaises(schema.FarmError):
                runner.prepare(pf,rf,root/"out")
            with self.assertRaisesRegex(schema.FarmError,"disabled by default"):
                runner.execute(root/"out")
    def test_refuse_slot_aliasing(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pf,rf,plan,runtime=fixture(root)
            runtime["slots"][1]["wineprefix"]=runtime["slots"][0]["wineprefix"]
            with self.assertRaisesRegex(schema.FarmError,"share ID"):
                schema.validate_runtime(runtime,plan,root/"runs")
            runtime["slots"][1]["wineprefix"]=str(root/"prefix1")
            runtime["slots"][1]["game_dir"]=runtime["slots"][0]["game_dir"]
            with self.assertRaisesRegex(schema.FarmError,"share ID"):
                schema.validate_runtime(runtime,plan,root/"runs")
    def test_refuse_overcommitted_cpu_memory(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,_,plan,rt=fixture(root)
            rt["capacity"]["cpu_budget_threads"]=6
            with self.assertRaisesRegex(schema.FarmError,"CPU budget"):
                schema.validate_runtime(rt,plan,root/"out")
            rt["capacity"]["cpu_budget_threads"]=12
            rt["capacity"]["ram_budget_mb"]=6000
            with self.assertRaisesRegex(schema.FarmError,"RAM budget"):
                schema.validate_runtime(rt,plan,root/"out")
    def test_sha_and_command_arguments_guarded(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,_,plan,rt=fixture(root)
            rt["mods"]["control"]["sha256"]="0"*64
            with self.assertRaisesRegex(schema.FarmError,"MPQ SHA"):
                schema.validate_runtime(rt,plan,root/"out")
            rt["mods"]["control"]["sha256"]=schema.hash_file(Path(rt["mods"]["control"]["mpq"]))
            rt["launcher"]["argv"]=["sh","-c","echo {evil}"]
            with self.assertRaisesRegex(schema.FarmError,"invalid launcher template"):
                schema.validate_runtime(rt,plan,root/"out")
    def test_same_source_install_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,_,plan,rt=fixture(root)
            rt["slots"][0]["game_dir"]=plan["games"][0]["game_dir"]
            with self.assertRaisesRegex(schema.FarmError,"worker install"):
                schema.validate_runtime(rt,plan,root/"out")
    def test_bandit_targets_cannot_overwrite_game_binaries(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,_,plan,rt=fixture(root)
            rt["slots"][0]["bandit_paths"]=[str(root/"slot0"/"StarCraft.exe")]
            with self.assertRaisesRegex(schema.FarmError,"Pluto stats"):
                schema.validate_runtime(rt,plan,root/"out")
    def test_pair_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _,_,plan,rt=fixture(Path(td))
            plan["games"][1]["manifest"]["seed"]=999
            with self.assertRaisesRegex(schema.FarmError,"conflicting planned"):
                schema.validate_runtime(rt,plan,Path(td)/"out")


class FarmRunTests(unittest.TestCase):
    def test_two_slots_run_pair_order_and_no_verified_game(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pf,rf,_,_=fixture(root)
            runner.prepare(pf,rf,root/"out")
            done=runner.execute(root/"out",allow_native=True)
            self.assertEqual(done["jobs"],4)
            self.assertEqual(done["counts"],{"unparsed_replay":4})
            self.assertFalse(done["native_headless_backend_verified"])
            self.assertEqual(done["games_with_parser_clean_replays"],0)
            receipts=[]
            for file in done["records"]:
                rec=json.loads(Path(file).read_text())
                self.assertFalse(rec["game_outcome_verified"])
                self.assertFalse(rec["native_game_execution_authenticated"])
                self.assertEqual(rec["status"],"unparsed_replay")
                receipts.append(rec)
                self.assertTrue(Path(rec["replay"]).is_file())
                self.assertEqual(len(rec["bandit_resets"]),2)
                for x in rec["bandit_resets"]:
                    self.assertEqual(x["reset_sha256"],rec["initial_bandit_sha256"])
            self.assertEqual({x["slot_id"] for x in receipts},{"worker0","worker1"})
            self.assertFalse((root/"out"/".farm.lock").exists())
            with self.assertRaises(FileExistsError):
                runner.execute(root/"out",allow_native=True)
    def test_failed_launcher_is_not_a_loss(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pf,rf,plan,rt=fixture(root,workers=1,pairs=1)
            rt["launcher"]["argv"]=[sys.executable,"-c","import sys;sys.exit(19)",
                                    "{run_id}","{map_path}","{mod_mpq}","{replay_path}"]
            rf.write_text(json.dumps(rt))
            runner.prepare(pf,rf,root/"out")
            res=runner.execute(root/"out",allow_native=True)
            self.assertEqual(res["counts"],{"launcher_failed":2})
            self.assertEqual(res["game_outcomes_verified"],0)
            self.assertEqual(res["video_jobs_emitted"],0)
    @unittest.skipIf(os.name=="nt","POSIX process-group termination smoke")
    def test_timeout_terminates_owned_subprocess_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            slot={"game_dir":str(root)}
            cmd=[sys.executable,"-c","import time; time.sleep(30)"]
            with (root/"stdout.log").open("wb") as output, (root/"stderr.log").open("wb") as errors:
                import time
                start=time.monotonic()
                rc,timed_out=runner._launch(cmd,slot,os.environ.copy(),output,errors,1)
                self.assertTrue(timed_out)
                self.assertNotEqual(rc,0)
                self.assertLess(time.monotonic()-start,8)
    def test_lock_prevents_duplicate_controllers(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pf,rf,plan,rt=fixture(root,workers=1,pairs=1)
            runner.prepare(pf,rf,root/"out")
            (root/"slot0"/".tensorcraft_worker.lock").write_text("other controller")
            with self.assertRaisesRegex(schema.FarmError,"lock exists"):
                runner.execute(root/"out",allow_native=True)
            self.assertFalse((root/"out"/".farm.lock").exists())


class VideoSpecTests(unittest.TestCase):
    def test_ffmpeg_youtube_4_by_3_pillarbox(self):
        cmd=video.ffmpeg_argv("ffmpeg",":121",Path("/tmp/test.mp4"),600)
        self.assertIn("x11grab",cmd)
        self.assertIn(":121.0",cmd)
        self.assertIn("+faststart",cmd)
        self.assertIn("libx264",cmd)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000",cmd)
        self.assertIn("pad=1920:1080",video.VIDEO_FILTER)
        self.assertNotIn("-nostdin",cmd)
    def test_replay_launcher_ready_done_contract(self):
        good=["launcher","{replay}","{mod_mpq}","{ready_marker}","{done_marker}","{display}"]
        self.assertEqual(len(video._validate_render_script(good)),5)
        with self.assertRaisesRegex(schema.FarmError,"omitted mandatory"):
            video._validate_render_script(["launcher","{replay}"])
        with self.assertRaisesRegex(schema.FarmError,"unsupported"):
            video._validate_render_script(["launcher","{bad}"])
    def test_bad_replay_signature_cannot_be_marked_as_capture(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            game=root/"game";game.mkdir()
            wine=root/"wine";wine.mkdir()
            bad=root/"bad.rep";bad.write_bytes(b"not the engine" + bytes(100))
            mod=root/"patch.mpq";mod.write_bytes(b"mock")
            spec={
                "schema":1,"run_id":"invalid",
                "replay":str(bad),"replay_sha256":schema.hash_file(bad),
                "mod_mpq":str(mod),"mod_mpq_sha256":schema.hash_file(mod),
                "game_dir":str(game),"wineprefix":str(wine),
                "display":":121","capture_limit_seconds":30,
                "ready_timeout_seconds":10,
                "replay_launcher":["p","{replay}","{mod_mpq}",
                                   "{ready_marker}","{done_marker}","{display}"],
            }
            with self.assertRaisesRegex(schema.FarmError,"replay envelope"):
                video.render_spec(spec,root/"out")
    def test_faststart_box_order(self):
        def box(name,data=b"x"*4):
            return (8+len(data)).to_bytes(4,"big")+name+data
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"movie.mp4"
            p.write_bytes(box(b"ftyp")+box(b"moov")+box(b"mdat"))
            self.assertTrue(video._faststart(p))
            p.write_bytes(box(b"ftyp")+box(b"mdat")+box(b"moov"))
            self.assertFalse(video._faststart(p))
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "ffmpeg + ffprobe are needed for container smoke test")
    def test_synthetic_ffmpeg_video_profile_is_valid(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"test-pattern.mp4"
            cmd=[
                shutil.which("ffmpeg"),"-hide_banner","-loglevel","error","-y",
                "-f","lavfi","-i","testsrc2=size=640x480:rate=30:duration=4.5",
                "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t","4.0","-vf",video.VIDEO_FILTER,
                "-r","30","-c:v","libx264","-preset","ultrafast","-crf","22",
                "-pix_fmt","yuv420p","-c:a","aac","-b:a","160k",
                "-ar","48000","-ac","2","-movflags","+faststart","-shortest",
                str(p)
            ]
            call=subprocess.run(cmd,capture_output=True,timeout=80)
            if call.returncode!=0 and b"Unknown encoder 'libx264'" in call.stderr:
                self.skipTest("ffmpeg build lacks libx264")
            self.assertEqual(call.returncode,0,call.stderr.decode(errors="replace")[:800])
            outcome=video.validate_mp4(p,shutil.which("ffprobe"))
            self.assertTrue(outcome["youtube_container_and_codec_compatible"])
            self.assertEqual(outcome["audio_source"],"generated_silence_no_game_audio")
            self.assertFalse(outcome["gameplay_visual_content_verified"])
            self.assertTrue(outcome["faststart_moov_before_mdat"])


if __name__=="__main__":
    unittest.main()
