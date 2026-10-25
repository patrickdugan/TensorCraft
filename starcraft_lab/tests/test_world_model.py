"""Offline contract tests: factual catalog, set relations, DAT offsets, surrogates.

Tests use only synthetic bytes; no StarCraft game binaries or game data.
"""
from __future__ import annotations
import json
import struct
import tempfile
import unittest
from pathlib import Path
from starcraft_lab import mod_factorial
from starcraft_lab.world_model import ontology, dat_extract as dat, combat, experiments


def col_offset(columns,name):
    n=0
    for fld,fmt,count,start,parts in columns:
        if fld==name:
            return n,struct.calcsize("<"+fmt)
        n+=struct.calcsize("<"+fmt)*count*parts
    raise KeyError(name)


def synthetic_tables():
    raw={key:bytearray(dat.byte_length(spec)) for key,(count,spec) in dat.TABLES.items()}
    unit_off,_=col_offset(dat.UNIT_COLUMNS,"flingy_id")
    raw["units.dat"][unit_off+62]=147
    air_off,_=col_offset(dat.UNIT_COLUMNS,"air_weapon_id")
    raw["units.dat"][air_off+62]=76
    hp_off,_=col_offset(dat.UNIT_COLUMNS,"hitpoints_raw")
    struct.pack_into("<i",raw["units.dat"],hp_off+62*4,250*256)
    mineral_off,_=col_offset(dat.UNIT_COLUMNS,"mineral_cost")
    struct.pack_into("<H",raw["units.dat"],mineral_off+62*2,250)
    speed_off,_=col_offset(dat.FLINGY_COLUMNS,"top_speed_raw")
    struct.pack_into("<i",raw["flingy.dat"],speed_off+147*4,1280)
    cd_off,_=col_offset(dat.WEAPON_COLUMNS,"cooldown")
    raw["weapons.dat"][cd_off+76]=100
    return raw


class SetAndSchemaTests(unittest.TestCase):
    def test_registry_and_empty_template(self):
        r=ontology.registry()
        self.assertGreaterEqual(r["total_fields"],125)
        self.assertEqual(len(r["fields"]),len({f["name"] for f in r["fields"]}))
        t=ontology.fill_template()
        self.assertEqual(t["unit_catalog_universe"],228)
        self.assertTrue(all(v["value"] is None for v in t["fields"].values()))
    def test_finite_relations_and_set_algebra(self):
        catalog=ontology.load_catalog()
        relations=ontology.relation_sets(catalog)
        self.assertIn(("devourer","air"),relations["attacks"])
        self.assertIn(("devourer","mutalisk"),relations["morphs_from"])
        self.assertIn(("devourer","greater_spire"),relations["requires_tech"])
        self.assertNotIn(("guardian","air"),relations["attacks"])
        self.assertIn("devourer",ontology.attackers(catalog,{"mutalisk"}))
        self.assertNotIn("guardian",ontology.attackers(catalog,{"mutalisk"}))
        self.assertIn("observer",relations["detectors"])
        with self.assertRaises(ValueError):
            ontology.attackers(catalog,{"unicorn"})
    def test_factual_seed_grounding(self):
        units=ontology.load_catalog()["units"]
        self.assertEqual(units["devourer"]["weapons"]["air"]["cooldown_frames"],100)
        self.assertEqual(units["mutalisk"]["weapons"]["air"]["bounce_damage"],[9,3,1])
        self.assertEqual(units["devourer"]["cost"]["minerals"],250)
        self.assertEqual(units["devourer"]["morph_incremental"]["gas"],50)
        self.assertLess(units["devourer"]["speed_px_per_frame"],units["mutalisk"]["speed_px_per_frame"])


class DatModelTests(unittest.TestCase):
    def test_widths_and_offset_match_mod_patcher(self):
        self.assertEqual(dat.byte_length(dat.UNIT_COLUMNS),19916)
        self.assertEqual(dat.byte_length(dat.WEAPON_COLUMNS),5460)
        self.assertEqual(dat.byte_length(dat.FLINGY_COLUMNS),3135)
        self.assertEqual(dat.byte_length(dat.UPGRADE_COLUMNS),1281)
        self.assertEqual(dat.byte_length(dat.TECH_COLUMNS),836)
        self.assertEqual(col_offset(dat.UNIT_COLUMNS,"air_weapon_id")[0],mod_factorial.AIR_WEAPON_OFFSET)
        self.assertEqual(col_offset(dat.WEAPON_COLUMNS,"cooldown")[0],mod_factorial.COOLDOWN_OFFSET)
    def test_synthetic_extraction_and_computed_fields(self):
        raw=synthetic_tables()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            paths={}
            for name,content in raw.items():
                paths[name]=p/name
                paths[name].write_bytes(content)
            result=dat.extract(paths)
            self.assertEqual(len(result["tables"]["units.dat"]),228)
            self.assertEqual(len(result["tables"]["weapons.dat"]),130)
            self.assertEqual(result["tables"]["units.dat"][62]["hp"],250)
            self.assertEqual(result["tables"]["units.dat"][62]["mineral_cost"],250)
            self.assertEqual(result["tables"]["units.dat"][62]["movement"]["top_speed_px_per_frame"],5)
            self.assertEqual(result["tables"]["units.dat"][62]["air_weapon"]["cooldown"],100)
            self.assertEqual(len(result["provenance"]["techdata.dat"]["sha256"]),64)
    def test_reject_missing_and_invalid_sizes(self):
        with self.assertRaisesRegex(ValueError,"mandatory"):
            dat.extract({"units.dat":Path("fake.dat")})
        with self.assertRaisesRegex(ValueError,"expected"):
            dat.decode(b"",228,dat.UNIT_COLUMNS)


class CombatModelTests(unittest.TestCase):
    def test_damage_types(self):
        self.assertEqual(combat.effective_hp_damage(25,0,"small","explosive"),12.5)
        self.assertEqual(combat.effective_hp_damage(25,0,"large","explosive"),25)
        self.assertEqual(combat.effective_hp_damage(9,0,"small","normal",3),12)
        self.assertGreater(combat.effective_hp_damage(25,2,"large","normal"),
                           combat.effective_hp_damage(25,2,"small","explosive"))
    def test_speed_cooldown_variant_factors(self):
        self.assertEqual(combat.VARIANTS["control"],(5,100))
        self.assertEqual(combat.VARIANTS["devourer_speed_550"],(5.5,100))
        self.assertEqual(combat.VARIANTS["devourer_cooldown_90"],(5,90))
        self.assertEqual(combat.VARIANTS["devourer_speed_550_cooldown_90"],(5.5,90))
    def test_simulation_is_reproducible_and_bounded(self):
        scenario=experiments.load_scenarios()[0]
        first=combat.simulate_once(scenario,"devourer_speed_550_cooldown_90",101)
        self.assertEqual(first,combat.simulate_once(scenario,"devourer_speed_550_cooldown_90",101))
        self.assertIn(first["winner"],(-1,0,1))
        self.assertTrue(-1 <=first["score_difference"]<=1)
        self.assertEqual(first["engine_fidelity"],"surrogate_unvalidated")
        report=combat.simulate_batch(scenario,"control",5,seed=8)
        self.assertEqual(sum(report[x] for x in ("a_wins","b_wins","draws")),5)
        with self.assertRaises(ValueError):
            combat.simulate_batch(scenario,"control",0)
    def test_acid_spore_damage_and_decay_toggle(self):
        cat=ontology.load_catalog()["units"]
        a=combat.Soldier(kind="mutalisk",side="B",x=0,y=0,hp=120,shields=0,cooldown_until=0,spores=4)
        counter=__import__("collections").Counter()
        combat._apply_hit(a,cat["mutalisk"],9,"normal",__import__("random").Random(1),
                          1,True,counter)
        self.assertEqual(a.hp,107)
        self.assertEqual(counter["spore_bonus_damage"],4)
    def test_factorial_consistency_and_experiment_ranking(self):
        cases=experiments.load_scenarios()
        self.assertGreaterEqual(len(cases),6)
        report=experiments.estimate_all(cases[:2],trials=2,seed=12,sensitivity_trials=1)
        self.assertTrue(report["sampled_matches_are_not_starcraft_matches"])
        self.assertEqual(len(report["active_learning_queue"]),2)
        row=report["scenario_results"][0]
        self.assertEqual(set(row["predictions"]),set(combat.VARIANTS))
        y={k:v["a_favorable_fraction"] for k,v in row["predictions"].items()}
        i=y["devourer_speed_550_cooldown_90"]-y["devourer_speed_550"]-y["devourer_cooldown_90"]+y["control"]
        self.assertAlmostEqual(i,row["factorial"]["interaction"],places=4)


if __name__=="__main__":
    unittest.main()
