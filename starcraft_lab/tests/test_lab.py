import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from starcraft_lab import mod, telemetry


def assets(aliases=False, speed=1280):
    units = bytearray(20000)
    units[62] = 147
    if aliases:
        units[61] = 147
    flingy = bytearray(mod.FLINGY_SIZE)
    struct.pack_into('<I', flingy, mod.SPEED_OFFSET + 147 * 4, speed)
    return bytes(units), bytes(flingy)


def manifest(**changes):
    m = dict(schema=1, run_id='r1', pair_id='p1', variant='control',
             engine='starcraft_1161', agent='pluto', agent_model='md09b_1331000',
             agent_binary_sha256='a'*64, engine_sha256='b'*64,
             map_sha256='c'*64, mod_sha256='d'*64,
             opponent='Steamhammer', player_race='Zerg', opponent_race='Zerg',
             spawn='A', seed=7, bandit_initial_sha256='e'*64,
             result='win', result_evidence='replay',
             result_evidence_sha256=hashlib.sha256(b'replay').hexdigest())
    m.update(changes)
    return m


class PatchTests(unittest.TestCase):
    def test_one_field_only_and_receipt(self):
        u, f = assets()
        out, receipt = mod.patch(u, f, variant='devourer_speed_550',
                                 expected_units_sha256=mod.digest(u),
                                 expected_flingy_sha256=mod.digest(f))
        self.assertEqual(struct.unpack_from('<I', out, mod.SPEED_OFFSET+147*4)[0], 1408)
        self.assertEqual(receipt['changed_byte_offsets'], [mod.SPEED_OFFSET+147*4])
        self.assertEqual(receipt['output_sha256'], mod.digest(out))
    def test_control_identical(self):
        u, f = assets()
        out, receipt = mod.patch(u, f, variant='control', expected_units_sha256=mod.digest(u), expected_flingy_sha256=mod.digest(f))
        self.assertEqual(f, out)
        self.assertEqual([], receipt['changed_byte_offsets'])
    def test_bad_hash_fails(self):
        u, f = assets()
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            mod.patch(u, f, variant='control', expected_units_sha256='0'*64, expected_flingy_sha256=mod.digest(f))
    def test_alias_fails(self):
        u, f = assets(aliases=True)
        with self.assertRaisesRegex(ValueError, 'shares Flingy'):
            mod.patch(u, f, variant='devourer_speed_550', expected_units_sha256=mod.digest(u), expected_flingy_sha256=mod.digest(f))
    def test_bad_file_size_and_speed_fail(self):
        u, f = assets()
        with self.assertRaisesRegex(ValueError, 'legacy'):
            mod.inspect(u, f[:-1])
        u, f = assets(speed=1800)
        with self.assertRaisesRegex(ValueError, 'expected unmodified'):
            mod.patch(u, f, variant='control', expected_units_sha256=mod.digest(u), expected_flingy_sha256=mod.digest(f))
    def test_refuse_original_overwrite(self):
        u, f = assets()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'units.dat').write_bytes(u)
            (path/'flingy.dat').write_bytes(f)
            with self.assertRaises(SystemExit):
                mod.main(['patch','--units',str(path/'units.dat'),'--flingy',str(path/'flingy.dat'), '--variant','control', '--expect-units-sha256',mod.digest(u),'--expect-flingy-sha256',mod.digest(f),'--output',str(path/'flingy.dat')])


class TelemetryTests(unittest.TestCase):
    def test_manifest_blocked_pluto_openbw(self):
        with self.assertRaisesRegex(ValueError, 'not been validated'):
            telemetry.validate(manifest(engine='openbw'))
    def test_crash_not_a_loss(self):
        telemetry.validate(manifest(result='crash', result_evidence='none', result_evidence_sha256=None))
        with self.assertRaises(ValueError):
            telemetry.validate(manifest(result='loss', result_evidence='none', result_evidence_sha256=None))
    def test_bandit_json_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'manifest.json').write_text(json.dumps(manifest()))
            (root/'replay.rep').write_bytes(b'replay')
            (root/'events.txt').write_text('seq 1\n{"type":"start","build":"x"}\n{"type":"end","result":"win"}\n')
            r = telemetry.collect(root/'manifest.json', root/'events.txt', root/'r.json', root/'replay.rep')
            self.assertEqual(len(r['pluto_json_events']), 2)
            self.assertFalse(r['telemetry_errors'])
            (root/'bad.txt').write_text('{broken\n')
            r2 = telemetry.collect(root/'manifest.json', root/'bad.txt', root/'bad.json', root/'replay.rep')
            self.assertEqual(r2['telemetry_errors'], ['malformed_json_line_1'])
            with self.assertRaises(FileExistsError):
                telemetry.collect(root/'manifest.json', root/'events.txt', root/'r.json', root/'replay.rep')
    def test_paired_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, var, outcome in [('r1','control','loss'),('r2','devourer_speed_550','win')]:
                m = manifest(run_id=name, variant=var, result=outcome,
                             mod_sha256=('d'*64 if var=='control' else 'f'*64))
                r = {'schema':1,'manifest':m,'telemetry_errors':[],'evidence_hash_matches':True,'pluto_json_events':[]}
                (root/f'{name}.json').write_text(json.dumps(r))
            report = telemetry.analyze(root)
            self.assertEqual(report['variants']['devourer_speed_550']['reported_win_rate_decisive'], 1.0)
            self.assertEqual(len(report['artifact_backed_pairs']), 1)
            (root/'r2.json').write_text(json.dumps({'schema':1,'manifest':manifest(run_id='r2', variant='devourer_speed_550', seed=8, mod_sha256='f'*64),'telemetry_errors':[],'evidence_hash_matches':True,'pluto_json_events':[]}))
            report2=telemetry.analyze(root)
            self.assertEqual(report2['excluded_pairs'][0]['reason'], 'different_conditions')
    def test_unverified_outcome_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m = manifest(result_evidence='manual', result_evidence_sha256=None)
            (root/'r1.json').write_text(json.dumps({'schema':1,'manifest':m,'telemetry_errors':[],'evidence_hash_matches':True,'pluto_json_events':[]}))
            report=telemetry.analyze(root)
            self.assertIsNone(report['variants']['control']['reported_win_rate_decisive'])
            self.assertEqual(report['variants']['control']['excluded'], 1)
    def test_missing_evidence_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'manifest.json').write_text(json.dumps(manifest()))
            r=telemetry.collect(root/'manifest.json', None, root/'r1.json')
            self.assertIn('result_evidence_file_missing', r['telemetry_errors'])
            self.assertFalse(telemetry.artifact_backed(r))
    def test_same_mod_hash_blocks_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,var in [('r1','control'),('r2','devourer_speed_550')]:
                m=manifest(run_id=name,variant=var)
                r={'schema':1,'manifest':m,'telemetry_errors':[],'evidence_hash_matches':True}
                (root/f'{name}.json').write_text(json.dumps(r))
            summary=telemetry.analyze(root)
            self.assertEqual(summary['excluded_pairs'][0]['reason'],'same_mod_hash')
    def test_duplicate_run_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            r={'schema':1,'manifest':manifest(),'telemetry_errors':[],'evidence_hash_matches':True}
            (root/'a.json').write_text(json.dumps(r))
            (root/'b.json').write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                telemetry.analyze(root)


if __name__ == '__main__':
    unittest.main()
