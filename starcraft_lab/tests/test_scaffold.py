import csv
import json
import tempfile
import unittest
from pathlib import Path
from starcraft_lab.world_model import scaffold, ontology


class ScaffoldTests(unittest.TestCase):
    def test_template_counts_and_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"world_model_fill"
            result=scaffold.generate(root)
            self.assertEqual(result["files"]["units.csv"],228)
            self.assertEqual(result["files"]["weapons.csv"],130)
            self.assertEqual(result["files"]["experiments.csv"],32)
            with (root/"units.csv").open(newline="") as fp:
                rows=list(csv.DictReader(fp))
            self.assertEqual(len(rows),228)
            self.assertEqual(rows[62]["known_name"],"devourer")
            self.assertEqual(rows[62]["top_speed"],"5")
            self.assertEqual(rows[62]["air_weapon_id"],"")
            self.assertEqual(rows[200]["status"],"missing")
            self.assertEqual(rows[200]["hp"],"")
            registry=json.loads((root/"variable_registry.json").read_text())
            self.assertEqual(result["files"]["variable_registry.json"],registry["total_fields"])
            relations=json.loads((root/"seed_relations.json").read_text())
            self.assertTrue(relations["known_seed_only"])
            with self.assertRaises(FileExistsError):
                scaffold.generate(root)
    def test_unknown_weapon_tables_are_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"blank"
            scaffold.generate(root)
            with (root/"weapons.csv").open(newline="") as fp:
                rows=list(csv.DictReader(fp))
            self.assertEqual(len(rows),130)
            self.assertTrue(all(x["status"]=="missing" and not x["damage"] for x in rows))


if __name__=="__main__":
    unittest.main()
