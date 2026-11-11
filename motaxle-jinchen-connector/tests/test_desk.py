import unittest

from server import Desk
from connector.poc import Connector


def issue():
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


def receipt(ref, batch, qty):
    return {
        "kind": "receipt",
        "id": ref,
        "work_order": "WO-2401",
        "item": "CR-100",
        "qty": qty,
        "uom": "Nos",
        "batch": batch,
    }


def confirm(ref, operation):
    return {
        "kind": "confirm",
        "id": ref,
        "work_order": "WO-2401",
        "operation": operation,
        "completed": 100,
        "rejected": 0,
        "rework": 0,
        "scrap": 0,
    }


def disposition(ref, decision, qty, reason):
    return {
        "kind": "disposition",
        "id": ref,
        "work_order": "WO-2401",
        "item": "CR-100",
        "qty": qty,
        "decision": decision,
        "reason": reason,
    }


class DeskTests(unittest.TestCase):
    def test_switch_b_on_blocks_release(self):
        desk = Desk()
        desk.set_switch("B", True)

        result = desk.release()

        self.assertFalse(desk.released)
        self.assertIn("WO-2401 was not released", result["message"])
        self.assertEqual(len(desk.connector.ledger.entries), 0)

    def test_switch_b_off_records_local_release(self):
        desk = Desk()
        desk.set_switch("B", False)

        result = desk.release()

        self.assertTrue(desk.released)
        self.assertIn("Jinchen was not called", result["message"])
        self.assertEqual(len(desk.connector.ledger.entries), 0)
        self.assertEqual(desk.connector.pieces()["finished"], 0)

    def test_b2_lookup_and_h441_missing(self):
        connector = Connector()

        self.assertEqual(connector.process(issue()).code, "")
        self.assertEqual(connector.process(receipt("C-1", "B-1", 40)).code, "")
        self.assertEqual(connector.process(receipt("C-2", "B-2", 56)).code, "")

        b2 = connector.lookup("B-2")
        self.assertEqual(b2["result"], "found")
        self.assertEqual(b2["heat"], "H-441")
        self.assertEqual(b2["work_order"], "WO-2401")

        h441 = connector.lookup("H441")
        self.assertEqual(h441["result"], "missing")

    def test_piece_split_totals_100_and_finished_stays_zero(self):
        connector = Connector()

        self.assertEqual(connector.process(confirm("CF-10", 10)).code, "")
        self.assertEqual(connector.process(confirm("CF-20", 20)).code, "")
        self.assertEqual(
            connector.process(
                disposition("D-REJ", "rejection", 2, "forge-crack")
            ).code,
            "",
        )
        self.assertEqual(
            connector.process(
                disposition("D-RW", "rework", 1, "rework-bend")
            ).code,
            "",
        )
        self.assertEqual(
            connector.process(
                disposition("D-SC", "scrap", 1, "scale")
            ).code,
            "",
        )
        self.assertEqual(connector.process(receipt("C-1", "B-1", 40)).code, "")
        self.assertEqual(connector.process(receipt("C-2", "B-2", 56)).code, "")

        pieces = connector.pieces()
        total = (
            pieces["hold"]
            + pieces["rejection"]
            + pieces["rework"]
            + pieces["scrap"]
        )

        self.assertEqual(total, 100)
        self.assertEqual(pieces["hold"], 96)
        self.assertEqual(pieces["rejection"], 2)
        self.assertEqual(pieces["rework"], 1)
        self.assertEqual(pieces["scrap"], 1)
        self.assertEqual(pieces["finished"], 0)


if __name__ == "__main__":
    unittest.main()
