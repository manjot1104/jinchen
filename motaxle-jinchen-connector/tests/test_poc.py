import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connector.poc import ConfigError, Connector, require_test_mode


def issue(item="ST-20", batch="H-441", qty=120, ref="J-100", uom="Kg"):
    return {
        "kind": "issue", "id": ref, "work_order": "WO-2401",
        "item": item, "qty": qty, "uom": uom, "batch": batch,
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }


class PocTests(unittest.TestCase):
    def test_tc02_live_password_refused(self):
        with self.assertRaises(ConfigError):
            require_test_mode("test", "secret")
        Connector()

    def test_tc03_issue_once(self):
        c = Connector()
        result = c.process(issue())
        self.assertEqual(result.status, "Posted")
        self.assertEqual(c.kg(), {"raw": 380, "wip": 120})
        self.assertEqual(len([e for e in c.ledger.entries if e.kind == "issue"]), 1)

    def test_tc04_repeat_after_log_loss(self):
        c = Connector()
        first = c.process(issue(), drop_log=True)
        self.assertEqual(c.log, [])
        second = c.process(issue())
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(second.document, first.document)
        self.assertEqual(c.kg()["raw"], 380)
        self.assertEqual(len(c.ledger.entries), 1)

    def test_tc05_near_miss(self):
        c = Connector()
        self.assertEqual(c.process(issue(item="ST20", ref="A")).code, "E-ITEM")
        self.assertEqual(c.process(issue(batch="H441", ref="B")).code, "E-BATCH")
        self.assertEqual(c.kg()["raw"], 500)
        self.assertEqual(c.ledger.entries, [])

    def test_tc06_return_once(self):
        c = Connector()
        c.process(issue())
        c.process({
            "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
        })
        c.process({
            "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
        })
        self.assertEqual(c.kg(), {"raw": 384, "wip": 116})

    def test_tc07_confirm_does_not_receive(self):
        c = Connector()
        c.process(issue())
        result = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(result.status, "Posted")
        self.assertEqual(c.pieces()["finished"], 0)
        self.assertEqual(c.pieces()["hold"], 0)
        self.assertFalse(any(e.item == "CR-100" for e in c.ledger.entries))

    def test_tc08_disposition(self):
        c = Connector()
        c.process({"kind": "disposition", "id": "D-REJ", "work_order": "WO-2401", "item": "CR-100", "qty": 2, "decision": "rejection", "reason": "forge-crack"})
        c.process({"kind": "disposition", "id": "D-RW", "work_order": "WO-2401", "item": "CR-100", "qty": 1, "decision": "rework", "reason": "rework-bend"})
        c.process({"kind": "disposition", "id": "D-SC", "work_order": "WO-2401", "item": "CR-100", "qty": 1, "decision": "scrap", "reason": "scale"})
        pieces = c.pieces()
        self.assertEqual(pieces["rejection"], 2)
        self.assertEqual(pieces["rework"], 1)
        self.assertEqual(pieces["scrap"], 1)
        self.assertEqual(pieces["finished"], 0)
        self.assertEqual(c.ledger.capa, 0)

    def test_tc09_two_receipts_and_replay(self):
        c = Connector()
        c.process(issue())
        c.process({"kind": "receipt", "id": "C-1", "work_order": "WO-2401", "item": "CR-100", "qty": 40, "batch": "B-1"})
        c.process({"kind": "receipt", "id": "C-2", "work_order": "WO-2401", "item": "CR-100", "qty": 56, "batch": "B-2"})
        c.process({"kind": "receipt", "id": "C-1", "work_order": "WO-2401", "item": "CR-100", "qty": 40, "batch": "B-1"})
        self.assertEqual(c.pieces()["hold"], 96)
        self.assertEqual(c.pieces()["finished"], 0)

    def test_tc10_rejected_not_saleable(self):
        c = Connector()
        result = c.process({
            "kind": "receipt", "id": "C-BAD", "work_order": "WO-2401",
            "item": "CR-100", "qty": 2, "batch": "B-X", "to_warehouse": "Finished Goods Store",
        })
        self.assertEqual(result.code, "E-QC")
        self.assertEqual(c.pieces()["finished"], 0)

    def test_tc11_lookup(self):
        c = Connector()
        c.process(issue())
        c.process({"kind": "receipt", "id": "C-1", "work_order": "WO-2401", "item": "CR-100", "qty": 40, "batch": "B-1"})
        found = c.lookup("B-1")
        self.assertEqual(found["heat"], "H-441")
        self.assertEqual(found["work_order"], "WO-2401")
        self.assertEqual(c.lookup("H441")["result"], "missing")

    def test_scrap_not_accepted_as_return(self):
        c = Connector()
        c.process(issue())
        result = c.process({
            "kind": "return", "id": "R-S", "work_order": "WO-2401", "against": "J-100",
            "item": "ST-20", "qty": 4, "batch": "H-441", "as_scrap": True,
        })
        self.assertEqual(result.code, "E-REASON")
        self.assertEqual(c.kg(), {"raw": 380, "wip": 120})

    def test_operation_30_ignored(self):
        c = Connector()
        result = c.process({
            "kind": "confirm", "id": "CF-30", "work_order": "WO-2401",
            "operation": 30, "completed": 10, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertIn("ignored", result.message)
        self.assertEqual(c.pieces()["finished"], 0)


if __name__ == "__main__":
    unittest.main()
