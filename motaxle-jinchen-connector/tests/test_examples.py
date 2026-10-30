import json
import unittest
from pathlib import Path

from connector.poc import Connector

EXAMPLES = Path(__file__).resolve().parent.parent / "inbox" / "examples"

EXPECTED_REFUSAL_FILES = {
    "ex-st20.json",
    "ex-h441.json",
    "ex-nos.json",
    "ex-warehouse.json",
    "ex-999.json",
    "ex-finished.json",
    "ex-nobatch.json",
    "ex-scrap-return.json",
}


class ExampleFileTests(unittest.TestCase):
    def load(self, filename):
        return json.loads((EXAMPLES / filename).read_text())

    def test_eight_refusal_examples_are_valid_json_objects(self):
        files = {path.name for path in EXAMPLES.glob("*.json")}
        self.assertEqual(files, EXPECTED_REFUSAL_FILES)

        for path in sorted(EXAMPLES.glob("*.json")):
            with self.subTest(file=path.name):
                data = json.loads(path.read_text())
                self.assertIsInstance(data, dict)
                self.assertIn("kind", data)
                self.assertIn("id", data)
                self.assertIn("work_order", data)

    def test_st20_disk_example_is_rejected(self):
        connector = Connector()
        before = connector.kg().copy()
        entries = len(connector.ledger.entries)

        result = connector.process(self.load("ex-st20.json"))

        self.assertEqual(result.code, "E-ITEM")
        self.assertEqual(connector.kg(), before)
        self.assertEqual(len(connector.ledger.entries), entries)

    def test_finished_goods_disk_example_is_blocked(self):
        connector = Connector()

        result = connector.process(self.load("ex-finished.json"))

        self.assertEqual(result.code, "E-QC")
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.pieces()["hold"], 0)
        self.assertEqual(len(connector.ledger.entries), 0)


if __name__ == "__main__":
    unittest.main()
