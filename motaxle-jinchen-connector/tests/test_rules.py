"""Rule changes from the tech lead's review: repeat key by kind, crash-free input."""
import unittest

from connector.poc import Connector


def issue(ref="J-100", qty=120, **overrides):
    message = {
        "kind": "issue", "id": ref, "work_order": "WO-2401",
        "item": "ST-20", "qty": qty, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }
    message.update(overrides)
    return message


def return_message(ref, against="J-100", qty=4, **overrides):
    message = {
        "kind": "return", "id": ref, "work_order": "WO-2401", "against": against,
        "item": "ST-20", "qty": qty, "uom": "Kg", "batch": "H-441",
    }
    message.update(overrides)
    return message


def receipt(ref, batch, qty, **overrides):
    message = {
        "kind": "receipt", "id": ref, "work_order": "WO-2401",
        "item": "CR-100", "qty": qty, "uom": "Nos", "batch": batch,
    }
    message.update(overrides)
    return message


def disposition(ref="D-REJ", qty=2, decision="rejection", reason="forge-crack"):
    return {
        "kind": "disposition", "id": ref, "work_order": "WO-2401",
        "item": "CR-100", "qty": qty, "decision": decision, "reason": reason,
    }


class RepeatKeyByKind(unittest.TestCase):
    def test_a_return_may_reuse_an_issue_id(self):
        c = Connector()
        self.assertEqual(c.process(issue("X-1")).status, "Posted")

        result = c.process(return_message("X-1", against="X-1", qty=4))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(result.code, "")
        self.assertEqual(result.document, "STE-0002")
        self.assertEqual(c.kg(), {"raw": 384, "wip": 116})
        self.assertEqual(len(c.ledger.entries), 2)

    def test_the_same_return_again_is_e_dup_on_its_own_document(self):
        c = Connector()
        c.process(issue("X-1"))
        first = c.process(return_message("X-1", against="X-1", qty=4))

        second = c.process(return_message("X-1", against="X-1", qty=4))

        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(second.document, first.document)
        self.assertEqual(c.kg()["raw"], 384)
        self.assertEqual(len(c.ledger.entries), 2)

    def test_a_receipt_may_reuse_the_same_id(self):
        c = Connector()
        c.process(issue("X-1"))
        c.process(return_message("X-1", against="X-1", qty=4))

        result = c.process(receipt("X-1", "B-1", 40))

        self.assertEqual(result.status, "Posted")
        self.assertEqual(result.code, "")
        self.assertEqual(c.pieces()["hold"], 40)
        self.assertEqual(len(c.ledger.entries), 3)

    def test_find_is_per_kind(self):
        c = Connector()
        c.process(issue("J-100"))
        self.assertEqual(c.ledger.find("J-100", "WO-2401", "issue").name, "STE-0001")
        self.assertIsNone(c.ledger.find("J-100", "WO-2401", "return"))
        self.assertIsNone(c.ledger.find("J-100", "WO-2401", "receipt"))

    def test_j100_twice_is_still_e_dup_on_ste_0001(self):
        c = Connector()
        c.process(issue("J-100"), drop_log=True)

        again = c.process(issue("J-100"))

        self.assertEqual(again.code, "E-DUP")
        self.assertEqual(again.document, "STE-0001")
        self.assertEqual(c.kg()["raw"], 380)
        self.assertEqual(len(c.ledger.entries), 1)

    def test_d_rej_twice_still_leaves_rejection_2(self):
        c = Connector()
        c.process(disposition())

        again = c.process(disposition())

        self.assertEqual(again.code, "E-DUP")
        self.assertEqual(c.pieces()["rejection"], 2)
        self.assertEqual(c.ledger.capa, 0)

    def test_c1_twice_still_leaves_hold_40(self):
        c = Connector()
        c.process(receipt("C-1", "B-1", 40))

        again = c.process(receipt("C-1", "B-1", 40))

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
        self.assert_refused_untouched(issue("J-TXT", qty="abc"), "E-QTY", "Quantity is not a number.")

    def test_qty_true_is_e_qty(self):
        self.assert_refused_untouched(issue("J-TRUE", qty=True), "E-QTY", "Quantity is not a number.")

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
        result = c.process(issue("J-STR", qty="120"))
        self.assertEqual(result.status, "Posted")
        self.assertEqual(c.kg()["raw"], 380)

    def test_the_callers_dictionary_is_not_changed(self):
        c = Connector()
        message = issue("J-STR", qty="120")
        c.process(message)
        self.assertEqual(message["qty"], "120")
        self.assertIsInstance(message["qty"], str)

    def test_a_fractional_operation_is_e_op(self):
        self.assert_refused_untouched(
            {"kind": "confirm", "id": "CF-HALF", "work_order": "WO-2401", "operation": 10.5, "completed": 1},
            "E-OP", "Operation is not on the demo route.")


if __name__ == "__main__":
    unittest.main()
