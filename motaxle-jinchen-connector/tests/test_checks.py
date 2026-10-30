import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connector.poc import Connector


def issue(ref="J-100", **overrides):
    message = {
        "kind": "issue",
        "id": ref,
        "work_order": "WO-2401",
        "item": "ST-20",
        "qty": 120,
        "uom": "Kg",
        "batch": "H-441",
        "from_warehouse": "Raw Material Store",
        "to_warehouse": "Forging WIP",
    }
    message.update(overrides)
    return message


class ValidationChecks(unittest.TestCase):
    def assert_rejected_without_stock_change(self, message, expected_code):
        connector = Connector()
        before_kg = connector.kg().copy()
        before_pieces = connector.pieces().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process(message)

        self.assertEqual(result.code, expected_code)
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(connector.pieces(), before_pieces)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_wrong_uom(self):
        self.assert_rejected_without_stock_change(
            issue(uom="Nos"), "E-UOM"
        )

    def test_wrong_warehouse(self):
        self.assert_rejected_without_stock_change(
            issue(from_warehouse="Wrong Warehouse"), "E-WH"
        )

    def test_insufficient_stock(self):
        self.assert_rejected_without_stock_change(
            issue(qty=999), "E-STOCK"
        )

    def test_unsupported_operation(self):
        self.assert_rejected_without_stock_change({
            "kind": "confirm", "id": "CF-BAD",
            "work_order": "WO-2401", "operation": 99,
            "completed": 1, "rejected": 0, "rework": 0, "scrap": 0,
        }, "E-OP")

    def test_too_many_completed_pieces(self):
        self.assert_rejected_without_stock_change({
            "kind": "confirm", "id": "CF-101",
            "work_order": "WO-2401", "operation": 10,
            "completed": 101, "rejected": 0, "rework": 0, "scrap": 0,
        }, "E-QTY")

    def test_missing_output_batch(self):
        self.assert_rejected_without_stock_change({
            "kind": "receipt", "id": "C-NOBATCH",
            "work_order": "WO-2401", "item": "CR-100",
            "qty": 1, "batch": "",
        }, "E-BATCH")

    def test_wrong_disposition_reason(self):
        self.assert_rejected_without_stock_change({
            "kind": "disposition", "id": "D-BAD",
            "work_order": "WO-2401", "item": "CR-100",
            "qty": 1, "decision": "rejection", "reason": "scale",
        }, "E-REASON")

    def test_scrap_return_rejected(self):
        connector = Connector()
        connector.process(issue())
        before_kg = connector.kg().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process({
            "kind": "return", "id": "R-SCRAP",
            "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 1, "batch": "H-441",
            "as_scrap": True,
        })

        self.assertEqual(result.code, "E-REASON")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_missing_id(self):
        self.assert_rejected_without_stock_change(
            issue(id=""), "E-QTY"
        )

    def test_missing_work_order(self):
        self.assert_rejected_without_stock_change(
            issue(work_order=""), "E-QTY"
        )

    def test_unknown_message_kind(self):
        self.assert_rejected_without_stock_change({
            "kind": "unknown", "id": "UNKNOWN-1",
            "work_order": "WO-2401",
        }, "E-ITEM")


    def test_return_before_issue_is_rejected(self):
        self.assert_rejected_without_stock_change({
            "kind": "return", "id": "R-EARLY",
            "work_order": "WO-2401", "against": "J-MISSING",
            "item": "ST-20", "qty": 1, "uom": "Kg", "batch": "H-441",
        }, "E-ITEM")

    def test_return_above_quantity_still_out_is_rejected(self):
        connector = Connector()
        connector.process(issue())
        before_kg = connector.kg().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process({
            "kind": "return", "id": "R-TOO-MUCH",
            "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 999, "uom": "Kg", "batch": "H-441",
        })

        self.assertEqual(result.code, "E-QTY")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_return_wrong_heat_is_rejected(self):
        connector = Connector()
        connector.process(issue())
        before_kg = connector.kg().copy()
        before_entries = len(connector.ledger.entries)

        result = connector.process({
            "kind": "return", "id": "R-WRONG-HEAT",
            "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 1, "uom": "Kg", "batch": "H441",
        })

        self.assertEqual(result.code, "E-BATCH")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_negative_confirmation_quantity_is_rejected(self):
        self.assert_rejected_without_stock_change({
            "kind": "confirm", "id": "CF-NEG",
            "work_order": "WO-2401", "operation": 10,
            "completed": -1, "rejected": 0, "rework": 0, "scrap": 0,
        }, "E-QTY")

    def test_zero_disposition_quantity_is_rejected(self):
        self.assert_rejected_without_stock_change({
            "kind": "disposition", "id": "D-ZERO",
            "work_order": "WO-2401", "item": "CR-100",
            "qty": 0, "decision": "rejection", "reason": "forge-crack",
        }, "E-QTY")

    def test_wrong_receipt_item_is_rejected(self):
        self.assert_rejected_without_stock_change({
            "kind": "receipt", "id": "C-WRONG-ITEM",
            "work_order": "WO-2401", "item": "ST-20",
            "qty": 1, "batch": "B-WRONG",
        }, "E-ITEM")

    def test_zero_receipt_quantity_is_rejected(self):
        self.assert_rejected_without_stock_change({
            "kind": "receipt", "id": "C-ZERO",
            "work_order": "WO-2401", "item": "CR-100",
            "qty": 0, "batch": "B-ZERO",
        }, "E-QTY")

    def test_duplicate_disposition_is_rejected_without_second_entry(self):
        connector = Connector()
        message = {
            "kind": "disposition", "id": "D-REJ",
            "work_order": "WO-2401", "item": "CR-100",
            "qty": 2, "decision": "rejection", "reason": "forge-crack",
        }
        first = connector.process(message)
        before_pieces = connector.pieces().copy()
        before_entries = len(connector.ledger.entries)

        second = connector.process(message)

        self.assertEqual(first.status, "Posted")
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(connector.pieces(), before_pieces)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_operation_30_is_ignored_without_stock_entry(self):
        connector = Connector()
        result = connector.process({
            "kind": "confirm", "id": "CF-30-EXTRA",
            "work_order": "WO-2401", "operation": 30,
            "completed": 10, "rejected": 0, "rework": 0, "scrap": 0,
        })

        self.assertEqual(result.status, "Posted")
        self.assertIn("ignored", result.message)
        self.assertEqual(len(connector.ledger.entries), 0)
        self.assertEqual(connector.pieces()["finished"], 0)

    def test_finished_goods_receipt_is_blocked(self):
        self.assert_rejected_without_stock_change({
            "kind": "receipt", "id": "C-FG-BLOCK",
            "work_order": "WO-2401", "item": "CR-100",
            "qty": 2, "batch": "B-FG",
            "to_warehouse": "Finished Goods Store",
        }, "E-QC")

    def test_duplicate_return_is_rejected_without_second_entry(self):
        connector = Connector()
        connector.process(issue())
        message = {
            "kind": "return", "id": "R-REPLAY",
            "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
        }

        first = connector.process(message)
        before_kg = connector.kg().copy()
        before_entries = len(connector.ledger.entries)
        second = connector.process(message)

        self.assertEqual(first.status, "Posted")
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(connector.kg(), before_kg)
        self.assertEqual(len(connector.ledger.entries), before_entries)


if __name__ == "__main__":
    unittest.main()
