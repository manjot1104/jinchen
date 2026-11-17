import inspect
import unittest
from pathlib import Path

from connector.poc import Connector


def make_issue(ref="J-100", qty=120):
    return {
        "kind": "issue",
        "id": ref,
        "work_order": "WO-2401",
        "item": "ST-20",
        "qty": qty,
        "uom": "Kg",
        "batch": "H-441",
        "from_warehouse": "Raw Material Store",
        "to_warehouse": "Forging WIP",
    }


def make_return(ref, against="J-100", qty=4, **overrides):
    message = {
        "kind": "return",
        "id": ref,
        "work_order": "WO-2401",
        "against": against,
        "item": "ST-20",
        "qty": qty,
        "batch": "H-441",
    }
    message.update(overrides)
    return message


def make_receipt(ref, batch, **overrides):
    message = {
        "kind": "receipt",
        "id": ref,
        "work_order": "WO-2401",
        "item": "CR-100",
        "qty": 5,
        "batch": batch,
    }
    message.update(overrides)
    return message


def make_disposition(ref, **overrides):
    message = {
        "kind": "disposition",
        "id": ref,
        "work_order": "WO-2401",
        "item": "CR-100",
        "qty": 2,
        "decision": "rejection",
        "reason": "forge-crack",
    }
    message.update(overrides)
    return message


class ReturnUomRules(unittest.TestCase):
    def test_wrong_return_uom_is_rejected_without_stock_change(self):
        connector = Connector()
        connector.process(make_issue())
        before_kg = connector.kg().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process(make_return("R-BAD-UOM", uom="Nos"))

        self.assertEqual(result.code, "E-UOM")
        self.assertEqual(result.message, "Return unit must be Kg.")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(len(connector.ledger.entries), before_entries)
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.ledger.capa, 0)

    def test_wrong_uom_is_checked_before_issue_lookup(self):
        connector = Connector()

        result = connector.process(
            make_return("R-NO-ISSUE", against="J-999", uom="Nos")
        )

        self.assertEqual(result.code, "E-UOM")
        self.assertEqual(result.message, "Return unit must be Kg.")

    def test_missing_return_uom_is_allowed(self):
        connector = Connector()
        connector.process(make_issue())

        result = connector.process(make_return("R-NO-UOM"))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(connector.kg()["raw"], 384)
        self.assertEqual(connector.kg()["wip"], 116)


class ReceiptUomRules(unittest.TestCase):
    def test_wrong_receipt_uom_is_rejected_without_stock_change(self):
        connector = Connector()
        before_pieces = connector.pieces().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process(
            make_receipt("C-BAD-UOM", "B-UOM", uom="Kg")
        )

        self.assertEqual(result.code, "E-UOM")
        self.assertEqual(result.message, "Receipt unit must be Nos.")
        self.assertEqual(connector.pieces(), before_pieces)
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.ledger.capa, 0)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_missing_receipt_uom_is_allowed(self):
        connector = Connector()

        result = connector.process(make_receipt("C-NO-UOM", "B-NO-UOM"))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(connector.pieces()["hold"], 5)
        self.assertEqual(connector.pieces()["finished"], 0)


class DispositionItemRules(unittest.TestCase):
    def assert_item_rejected(self, message):
        connector = Connector()
        before_kg = connector.kg().copy()
        before_pieces = connector.pieces().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process(message)

        self.assertEqual(result.code, "E-ITEM")
        self.assertEqual(result.message, "Disposition item is not CR-100.")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(connector.pieces(), before_pieces)
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.ledger.capa, 0)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_wrong_disposition_item_is_rejected(self):
        self.assert_item_rejected(
            make_disposition("D-WRONG-ITEM", item="ST-20")
        )

    def test_missing_disposition_item_is_rejected(self):
        self.assert_item_rejected(
            make_disposition("D-MISSING-ITEM", item="")
        )


class ReturnAgainstIssueRules(unittest.TestCase):
    def test_returns_reduce_only_their_own_issue_quantity(self):
        connector = Connector()

        self.assertEqual(connector.process(make_issue()).status, "Posted")
        self.assertEqual(connector.kg()["raw"], 380)

        result_a = connector.process(make_return("R-A", qty=100))
        self.assertEqual(result_a.status, "Posted")
        self.assertEqual(connector.kg()["raw"], 480)

        result_b = connector.process(make_return("R-B", qty=20))
        self.assertEqual(result_b.status, "Posted")
        self.assertEqual(connector.kg()["raw"], 500)

        result_c = connector.process(make_return("R-C", qty=1))
        self.assertEqual(result_c.code, "E-QTY")
        self.assertEqual(connector.kg()["raw"], 500)

        result_d = connector.process(make_return("R-D", qty=121))
        self.assertEqual(result_d.code, "E-QTY")
        self.assertEqual(connector.kg()["raw"], 500)

        result_e = connector.process(
            make_return("R-E", against="J-999", qty=1)
        )
        self.assertEqual(result_e.code, "E-ITEM")
        self.assertEqual(connector.kg()["raw"], 500)


class LookupHeatRules(unittest.TestCase):
    def test_lookup_heat_comes_from_first_issue_entry(self):
        connector = Connector()
        connector.process(make_issue())
        connector.process(make_issue(ref="J-200", qty=10))
        connector.process(make_receipt("C-2", "B-2"))

        first_issue = next(
            entry for entry in connector.ledger.entries
            if entry.kind == "issue"
        )
        result = connector.lookup("B-2")

        self.assertEqual(first_issue.kind, "issue")
        self.assertEqual(result["result"], "found")
        self.assertEqual(result["heat"], first_issue.batch)

    def test_receipt_without_issue_has_missing_lookup(self):
        connector = Connector()
        connector.process(make_receipt("C-1", "B-1"))

        self.assertEqual(
            connector.lookup("B-1"),
            {"batch": "B-1", "result": "missing"},
        )

    def test_lookup_source_has_no_hardcoded_heat(self):
        source = inspect.getsource(Connector.lookup)
        self.assertNotIn("H-441", source)


class CapaControlRules(unittest.TestCase):
    def test_explicit_open_capa_adds_one_row(self):
        connector = Connector()

        connector.ledger.open_capa(
            "D-100", "WO-2401", "forge-crack"
        )

        self.assertEqual(connector.ledger.capa, 1)
        self.assertEqual(
            connector.ledger.capa_rows,
            [("D-100", "WO-2401", "forge-crack")],
        )

    def test_normal_workflow_does_not_open_capa(self):
        connector = Connector()

        self.assertEqual(connector.process(make_issue()).status, "Posted")
        self.assertEqual(
            connector.process(make_return("R-1")).status, "Posted"
        )

        for ref, operation in (("CF-10", 10), ("CF-20", 20)):
            result = connector.process({
                "kind": "confirm",
                "id": ref,
                "work_order": "WO-2401",
                "operation": operation,
                "completed": 10,
                "rejected": 0,
                "rework": 0,
                "scrap": 0,
            })
            self.assertEqual(result.status, "Posted")

        for message in (
            make_disposition("D-REJ"),
            make_disposition(
                "D-RW", decision="rework", reason="rework-bend", qty=1
            ),
            make_disposition(
                "D-SC", decision="scrap", reason="scale", qty=1
            ),
        ):
            self.assertEqual(connector.process(message).status, "Posted")

        duplicate = connector.process(make_disposition("D-REJ"))
        self.assertEqual(duplicate.code, "E-DUP")

        self.assertEqual(
            connector.process(make_receipt("C-1", "B-1")).status,
            "Posted",
        )
        self.assertEqual(
            connector.process(make_receipt("C-2", "B-2")).status,
            "Posted",
        )

        self.assertEqual(connector.lookup("B-2")["result"], "found")
        self.assertEqual(connector.lookup("B-2")["heat"], "H-441")
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.ledger.capa, 0)
        self.assertEqual(connector.ledger.capa_rows, [])

    def test_open_capa_is_defined_only_once_in_connector_source(self):
        source = Path("connector/poc.py").read_text()
        self.assertEqual(source.count("open_capa"), 1)
        self.assertIn("def open_capa(", source)


if __name__ == "__main__":
    unittest.main()
