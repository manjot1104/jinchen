

import unittest

from connector.poc import Connector


def make_issue():
    return {
        "kind": "issue",
        "id": "J-100",
        "work_order": "WO-2401",
        "item": "ST-20",
        "qty": 120,
        "uom": "Kg",
        "batch": "H-441",
        "from_warehouse": "Raw Material Store",
        "to_warehouse": "Forging WIP",
    }


def make_return(ref, **overrides):
    message = {
        "kind": "return",
        "id": ref,
        "work_order": "WO-2401",
        "against": "J-100",
        "item": "ST-20",
        "qty": 4,
        "batch": "H-441",
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
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_missing_return_uom_is_allowed(self):
        connector = Connector()
        connector.process(make_issue())

        result = connector.process(make_return("R-NO-UOM"))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(connector.kg()["raw"], 384)
        self.assertEqual(connector.kg()["wip"], 116)


if __name__ == "__main__":
    unittest.main()


class ReceiptUomRules(unittest.TestCase):
    def make_receipt(self, ref, **overrides):
        message = {
            "kind": "receipt",
            "id": ref,
            "work_order": "WO-2401",
            "item": "CR-100",
            "qty": 5,
            "batch": "B-UOM",
        }
        message.update(overrides)
        return message

    def test_wrong_receipt_uom_is_rejected_without_stock_change(self):
        connector = Connector()
        before_pieces = connector.pieces().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process(
            self.make_receipt("C-BAD-UOM", uom="Kg")
        )

        self.assertEqual(result.code, "E-UOM")
        self.assertEqual(connector.pieces(), before_pieces)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_missing_receipt_uom_is_allowed(self):
        connector = Connector()

        result = connector.process(self.make_receipt("C-NO-UOM"))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(connector.pieces()["hold"], 5)
        self.assertEqual(connector.pieces()["finished"], 0)


class DispositionItemRules(unittest.TestCase):
    def make_disposition(self, ref, **overrides):
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

    def assert_item_rejected(self, message):
        connector = Connector()
        before_kg = connector.kg().copy()
        before_pieces = connector.pieces().copy()
        before_entries = len(connector.ledger.entries)
        before_capa = connector.ledger.capa

        result = connector.process(message)

        self.assertEqual(result.code, "E-ITEM")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(connector.pieces(), before_pieces)
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.ledger.capa, before_capa)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_wrong_disposition_item_is_rejected(self):
        self.assert_item_rejected(
            self.make_disposition("D-WRONG-ITEM", item="ST-20")
        )

    def test_missing_disposition_item_is_rejected(self):
        self.assert_item_rejected(
            self.make_disposition("D-MISSING-ITEM", item="")
        )


class ReturnAgainstIssueRules(unittest.TestCase):
    def test_returns_reduce_only_their_own_issue_quantity(self):
        connector = Connector()

        first_issue = make_issue()
        first_issue["qty"] = 120

        second_issue = make_issue()
        second_issue.update({"id": "J-200", "qty": 100})

        self.assertEqual(connector.process(first_issue).status, "Posted")
        self.assertEqual(connector.process(second_issue).status, "Posted")

        first_return = make_return("R-100", qty=20)
        second_return = make_return("R-200", against="J-200", qty=30)

        self.assertEqual(connector.process(first_return).status, "Posted")
        self.assertEqual(connector.process(second_return).status, "Posted")

        self.assertEqual(connector._issued_remaining("J-100", "WO-2401"), (120, 100))
        self.assertEqual(connector._issued_remaining("J-200", "WO-2401"), (100, 70))


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

        connector.process(make_issue())
        connector.process(make_return("R-100"))

        self.assertEqual(connector.ledger.capa, 0)
        self.assertEqual(connector.ledger.capa_rows, [])
