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


if __name__ == "__main__":
    unittest.main()
