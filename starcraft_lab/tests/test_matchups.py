import unittest
from starcraft_lab.world_model import ontology, matchups


class PairwiseTest(unittest.TestCase):
    def setUp(self):
        self.catalog=ontology.load_catalog()
    def test_all_pair_coverage_and_non_empirical_labels(self):
        m=matchups.matrix(self.catalog)
        n=len(self.catalog["units"])
        self.assertEqual(m["entries"],n*n)
        self.assertEqual({(x["A"],x["B"]) for x in m["matrix"]},
                         {(a,b) for a in self.catalog["units"] for b in self.catalog["units"]})
        self.assertIn("neither",m["warning"])
    def test_air_only_cannot_hit_ground(self):
        x=matchups.matchup("devourer","marine",self.catalog)
        self.assertFalse(x["A_can_attack"])
        self.assertTrue(x["B_can_attack"])
        self.assertEqual(x["capability_class"],"one_sided_attack_B")
        self.assertIsNone(x["surrogate_favorable_fraction_A"])
    def test_ground_only_cannot_hit_air_without_weapon(self):
        x=matchups.matchup("guardian","mutalisk",self.catalog)
        self.assertFalse(x["A_can_attack"])
        self.assertTrue(x["B_can_attack"])
        self.assertEqual(x["capability_class"],"one_sided_attack_B")
    def test_nonnative_special_carrier_explicitly_excluded(self):
        x=matchups.matchup("devourer","carrier",self.catalog)
        self.assertEqual(x["capability_class"],"unsupported_special_attack_model")
        self.assertIsNone(x["surrogate_favorable_fraction_A"])
    def test_muta_mirror_symmetric(self):
        x=matchups.matchup("mutalisk","mutalisk",self.catalog)
        self.assertEqual(x["surrogate_favorable_fraction_A"],0.5)
    def test_dev_muta_contains_spore_limitation(self):
        x=matchups.matchup("devourer","mutalisk",self.catalog)
        self.assertTrue(x["A_can_attack"] and x["B_can_attack"])
        self.assertGreater(x["A_ttk_B_frames"],0)
        self.assertIn("ability_status",x["unmodeled"])


if __name__=="__main__":
    unittest.main()
