import json
import unittest
from pathlib import Path

from connector.poc import Connector

EXAMPLES = Path(__file__).resolve().parent.parent / "inbox" / "examples"


class ExampleFileTests(unittest.TestCase):
    def load(self, filename):
        return json.loads((EXAMPLES / filename).read_text())

    def test_all_sixteen_examples_are_valid_json_objects(self):
        files = sorted(EXAMPLES.glob("*.json"))
        self.assertEqual(len(files), 16)

        for path in files:
            with self.subTest(file=path.name):
                data = json.loads(path.read_text())
                self.assertIsInstance(data, dict)
                self.assertIn("kind", data)
                self.assertIn("id", data)
                self.assertIn("work_order", data)

    def test_valid_issue_posts(self):
        connector = Connector()
        before = connector.kg()["raw"]

        result = connector.process(self.load("ex-issue-valid.json"))

        self.assertEqual(result.code, "")
        self.assertEqual(connector.kg()["raw"], before - 10)

    def test_wrong_item_is_rejected_without_stock_change(self):
        connector = Connector()
        before = connector.kg().copy()
        entries = len(connector.ledger.entries)

        result = connector.process(self.load("ex-issue-wrong-item.json"))

        self.assertEqual(result.code, "E-ITEM")
        self.assertEqual(connector.kg(), before)
        self.assertEqual(len(connector.ledger.entries), entries)

    def test_wrong_heat_is_rejected_without_stock_change(self):
        connector = Connector()
        before = connector.kg().copy()
        entries = len(connector.ledger.entries)

        result = connector.process(self.load("ex-issue-wrong-heat.json"))

        self.assertEqual(result.code, "E-BATCH")
        self.assertEqual(connector.kg(), before)
        self.assertEqual(len(connector.ledger.entries), entries)

    def test_duplicate_issue_is_rejected_without_second_stock_entry(self):
        connector = Connector()
        message = self.load("ex-issue-duplicate.json")

        first = connector.process(message)
        before = connector.kg().copy()
        entries = len(connector.ledger.entries)

        second = connector.process(message)

        self.assertEqual(first.code, "")
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(connector.kg(), before)
        self.assertEqual(len(connector.ledger.entries), entries)

    def test_cutting_confirmation_does_not_add_finished_goods(self):
        connector = Connector()
        before = connector.pieces()["finished"]

        result = connector.process(self.load("ex-confirm-cutting.json"))

        self.assertEqual(result.code, "")
        self.assertEqual(connector.pieces()["finished"], before)

    def test_rejection_disposition_updates_rejection_count(self):
        connector = Connector()
        before = connector.pieces()["rejection"]

        result = connector.process(self.load("ex-disposition-rejection.json"))

        self.assertEqual(result.code, "")
        self.assertEqual(connector.pieces()["rejection"], before + 2)

    def test_receipt_example_posts_to_hold(self):
        connector = Connector()
        before = connector.pieces()["hold"]

        result = connector.process(self.load("ex-receipt-hold.json"))

        self.assertEqual(result.code, "")
        self.assertEqual(connector.pieces()["hold"], before + 40)
        self.assertEqual(connector.pieces()["finished"], 0)

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
