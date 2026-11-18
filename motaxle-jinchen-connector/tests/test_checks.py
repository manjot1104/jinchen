import inspect
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connector import poc
from connector.poc import RAW, WIP, Connector


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
        self.assertEqual(connector.pieces()["finished"], 0)
        self.assertEqual(connector.ledger.capa, 0)
        self.assertEqual(len(connector.ledger.entries), before_entries)

    def test_wrong_uom(self):
        self.assert_rejected_without_stock_change(
            issue(qty=1, uom="Nos", id="J-UOM"), "E-UOM"
        )

    def test_wrong_warehouse(self):
        self.assert_rejected_without_stock_change(
            issue(qty=1, from_warehouse="Finished Goods Store", id="J-WH"), "E-WH"
        )

    def test_insufficient_stock(self):
        self.assert_rejected_without_stock_change(
            issue(qty=999), "E-STOCK"
        )

    def test_unsupported_operation(self):
        self.assert_rejected_without_stock_change({
            "kind": "confirm", "id": "CF-BAD",
            "work_order": "WO-2401", "operation": 40,
            "completed": 1, "rejected": 0, "rework": 0, "scrap": 0,
        }, "E-OP")

    def test_too_many_completed_pieces(self):
        self.assert_rejected_without_stock_change({
            "kind": "confirm", "id": "CF-101",
            "work_order": "WO-2401", "operation": 10,
            "completed": 150, "rejected": 0, "rework": 0, "scrap": 0,
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
        # Fresh connector, no issue first: the flag is refused before the quantity.
        connector = Connector()
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
        self.assertEqual(connector.kg()["raw"], 500)
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
            "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 1, "batch": "H-441",
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
        self.assertEqual(connector.pieces()["rejection"], 2)
        self.assertEqual(connector.ledger.capa, 0)
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
        self.assertEqual(connector.process(make_issue()).status, "Posted")

        result = connector.process(
            make_return("R-N2", against="J-999", uom="Nos")
        )

        self.assertEqual(result.code, "E-UOM")
        self.assertEqual(result.message, "Return unit must be Kg.")
        self.assertEqual(connector.kg()["raw"], 380)
        self.assertEqual(len(connector.ledger.entries), 1)

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
        # Two issues on one work order. Each return may only use the
        # quantity still out on the issue it names.
        connector = Connector()

        self.assertEqual(connector.process(make_issue()).status, "Posted")
        self.assertEqual(
            connector.process(make_issue(ref="J-101", qty=100)).status,
            "Posted",
        )
        self.assertEqual(connector.kg()["raw"], 280)

        result_a = connector.process(make_return("R-A", qty=100))
        self.assertEqual(result_a.status, "Posted")
        self.assertEqual(connector.kg()["raw"], 380)

        result_b = connector.process(
            make_return("R-B", against="J-101", qty=100)
        )
        self.assertEqual(result_b.status, "Posted")
        self.assertEqual(connector.kg(), {"raw": 480.0, "wip": 20.0})

        result_c = connector.process(make_return("R-C", qty=20))
        self.assertEqual(result_c.status, "Posted")
        self.assertEqual(connector.kg()["raw"], 500)

        result_d = connector.process(make_return("R-D", qty=1))
        self.assertEqual(result_d.code, "E-QTY")
        self.assertEqual(
            result_d.message, "Return is above the quantity still out."
        )
        self.assertEqual(connector.kg()["raw"], 500)

        result_e = connector.process(
            make_return("R-E", against="J-999", qty=1)
        )
        self.assertEqual(result_e.code, "E-ITEM")
        self.assertEqual(result_e.message, "Original issue was not posted.")
        self.assertEqual(connector.kg()["raw"], 500)

        self.assertEqual(len(connector.ledger.entries), 5)


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

    def test_second_heat_traces_from_its_own_issue(self):
        # H-442 is written nowhere in poc.py, so B-9 can only find it by
        # reading the issue entry on its own work order.
        connector = Connector()
        connector.process(make_issue())
        connector.ledger.add(RAW, "ST-20", "H-442", 200.0)
        connector.ledger.submit(
            "issue", "J-442", "WO-2402", "ST-20", 50.0, "Kg", "H-442", RAW, WIP
        )
        connector.process(make_receipt("C-9", "B-9", work_order="WO-2402", qty=10))
        connector.process(make_receipt("C-1", "B-1", qty=40))

        self.assertEqual(connector.lookup("B-9")["heat"], "H-442")
        self.assertEqual(connector.lookup("B-1")["heat"], "H-441")

    def test_issued_remaining_source_has_no_item_or_heat(self):
        source = inspect.getsource(Connector._issued_remaining)
        self.assertNotIn("ST-20", source)
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
            connector.process(make_receipt("C-1", "B-1", qty=40)).status,
            "Posted",
        )
        self.assertEqual(
            connector.process(make_receipt("C-2", "B-2", qty=56)).status,
            "Posted",
        )

        self.assertEqual(connector.lookup("B-2")["result"], "found")
        self.assertEqual(connector.lookup("B-2")["heat"], "H-441")
        self.assertEqual(connector.kg(), {"raw": 384.0, "wip": 116.0})
        self.assertEqual(connector.pieces(), {
            "hold": 96.0, "finished": 0.0,
            "rejection": 2.0, "rework": 1.0, "scrap": 1.0,
        })
        self.assertEqual(len(connector.ledger.entries), 7)
        self.assertEqual(connector.ledger.capa, 0)
        self.assertEqual(connector.ledger.capa_rows, [])

    def test_open_capa_is_defined_only_once_in_connector_source(self):
        # Read the module's own file, so the test works from any folder.
        source = Path(inspect.getsourcefile(poc)).read_text()
        self.assertEqual(source.count("open_capa"), 1)
        self.assertIn("    def open_capa(", source)


class RepeatKeyByKind(unittest.TestCase):
    def test_a_return_may_reuse_an_issue_id(self):
        c = Connector()
        self.assertEqual(c.process(make_issue("X-1")).status, "Posted")

        result = c.process(make_return("X-1", against="X-1", qty=4))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(result.code, "")
        self.assertEqual(result.document, "STE-0002")
        self.assertEqual(c.kg(), {"raw": 384, "wip": 116})
        self.assertEqual(len(c.ledger.entries), 2)

    def test_the_same_return_again_is_e_dup_on_its_own_document(self):
        c = Connector()
        c.process(make_issue("X-1"))
        first = c.process(make_return("X-1", against="X-1", qty=4))

        second = c.process(make_return("X-1", against="X-1", qty=4))

        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(second.document, first.document)
        self.assertEqual(c.kg()["raw"], 384)
        self.assertEqual(len(c.ledger.entries), 2)

    def test_a_receipt_may_reuse_the_same_id(self):
        c = Connector()
        c.process(make_issue("X-1"))
        c.process(make_return("X-1", against="X-1", qty=4))

        result = c.process(make_receipt("X-1", "B-1", qty=40))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(result.code, "")
        self.assertEqual(c.pieces()["hold"], 40)
        self.assertEqual(len(c.ledger.entries), 3)

    def test_find_is_per_kind(self):
        c = Connector()
        c.process(make_issue("J-100"))
        self.assertEqual(c.ledger.find("J-100", "WO-2401", "issue").name, "STE-0001")
        self.assertIsNone(c.ledger.find("J-100", "WO-2401", "return"))
        self.assertIsNone(c.ledger.find("J-100", "WO-2401", "receipt"))

    def test_j100_twice_is_still_e_dup_on_ste_0001(self):
        c = Connector()
        c.process(make_issue("J-100"), drop_log=True)

        again = c.process(make_issue("J-100"))

        self.assertEqual(again.code, "E-DUP")
        self.assertEqual(again.document, "STE-0001")
        self.assertEqual(c.kg()["raw"], 380)
        self.assertEqual(len(c.ledger.entries), 1)

    def test_d_rej_twice_still_leaves_rejection_2(self):
        c = Connector()
        c.process(make_disposition("D-REJ"))

        again = c.process(make_disposition("D-REJ"))

        self.assertEqual(again.code, "E-DUP")
        self.assertEqual(c.pieces()["rejection"], 2)
        self.assertEqual(c.ledger.capa, 0)

    def test_c1_twice_still_leaves_hold_40(self):
        c = Connector()
        c.process(make_receipt("C-1", "B-1", qty=40))

        again = c.process(make_receipt("C-1", "B-1", qty=40))

        self.assertEqual(again.code, "E-DUP")
        self.assertEqual(c.pieces()["hold"], 40)


class BadInputIsARefusalNotACrash(unittest.TestCase):
    def assert_refused_untouched(self, message, code, sentence):
        c = Connector()
        result = c.process(message)  # must not raise
        self.assertEqual(result.status, "Business Rejected")
        self.assertEqual(result.code, code)
        self.assertEqual(result.message, sentence)
        self.assertEqual(c.kg(), {"raw": 500, "wip": 0})
        self.assertEqual(c.pieces()["finished"], 0)
        self.assertEqual(len(c.ledger.entries), 0)
        return c

    def test_qty_abc_is_e_qty(self):
        self.assert_refused_untouched(make_issue("J-TXT", qty="abc"), "E-QTY", "Quantity is not a number.")

    def test_qty_true_is_e_qty(self):
        self.assert_refused_untouched(make_issue("J-TRUE", qty=True), "E-QTY", "Quantity is not a number.")

    def test_operation_ten_is_e_op(self):
        self.assert_refused_untouched(
            {"kind": "confirm", "id": "CF-TXT", "work_order": "WO-2401", "operation": "ten", "completed": 1},
            "E-OP", "Operation is not on the demo route.")

    def test_rejected_x_is_e_qty(self):
        self.assert_refused_untouched(
            {"kind": "confirm", "id": "CF-X", "work_order": "WO-2401", "operation": 10,
             "completed": 1, "rejected": "x", "rework": 0, "scrap": 0},
            "E-QTY", "Quantity is not a number.")

    def test_a_list_is_e_item(self):
        c = self.assert_refused_untouched(["not", "a", "dict"], "E-ITEM", "Unknown message.")
        self.assertEqual(c.log, [])

    def test_none_and_a_string_are_e_item(self):
        self.assert_refused_untouched(None, "E-ITEM", "Unknown message.")
        self.assert_refused_untouched("issue", "E-ITEM", "Unknown message.")

    def test_a_numeric_string_still_posts(self):
        c = Connector()
        result = c.process(make_issue("J-STR", qty="120"))
        self.assertEqual(result.status, "Posted")
        self.assertEqual(c.kg()["raw"], 380)

    def test_the_callers_dictionary_is_not_changed(self):
        c = Connector()
        message = make_issue("J-STR", qty="120")
        c.process(message)
        self.assertEqual(message["qty"], "120")
        self.assertIsInstance(message["qty"], str)

    def test_a_fractional_operation_is_e_op(self):
        self.assert_refused_untouched(
            {"kind": "confirm", "id": "CF-HALF", "work_order": "WO-2401", "operation": 10.5, "completed": 1},
            "E-OP", "Operation is not on the demo route.")


if __name__ == "__main__":
    unittest.main()
