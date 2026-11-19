"""Tests for repeated confirmation idempotency (US-090)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from connector.inbox import read_all
from connector.poc import Connector
from connector.reconcile import reconcile

PROJECT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT / "inbox" / "samples"


class ConfirmRepeatTests(unittest.TestCase):
    def test_criterion_1_repeated_confirm_returns_e_dup_code_and_message(self):
        """Criterion 1: After J-100, CF-10 twice: Posted, then Posted with E-DUP and
        Already confirmed. Not stored again.
        """
        c = Connector()
        c.process({
            "kind": "issue", "id": "J-100", "work_order": "WO-2401",
            "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
            "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
        })
        first = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        second = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(first.status, "Posted")
        self.assertEqual(first.code, "")
        self.assertEqual(second.status, "Posted")
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(second.document, "")
        self.assertEqual(second.message, "Already confirmed. Not stored again.")

    def test_criterion_1_repeated_confirm_does_not_add_to_confirmations_list(self):
        """Criterion 1: confirmations has 1 row after CF-10 sent twice."""
        c = Connector()
        c.process({
            "kind": "issue", "id": "J-100", "work_order": "WO-2401",
            "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
            "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
        })
        c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(len(c.confirmations), 1)
        self.assertEqual(c.confirmations[0], ("WO-2401", 10, (100.0, 0.0, 0.0, 0.0)))

    def test_criterion_1_repeated_confirm_logs_both_attempts(self):
        """Criterion 1: the log has 2 rows for confirmations."""
        c = Connector()
        c.process({
            "kind": "issue", "id": "J-100", "work_order": "WO-2401",
            "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
            "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
        })
        c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        confirm_logs = [row for row in c.log if row.kind == "confirm"]
        self.assertEqual(len(confirm_logs), 2)
        self.assertEqual(confirm_logs[0].code, "")
        self.assertEqual(confirm_logs[1].code, "E-DUP")

    def test_criterion_2_same_id_with_different_operation_still_posts(self):
        """Criterion 2: The key is id + work order + operation: CF-10 with operation 20 still posts."""
        c = Connector()
        c.process({
            "kind": "issue", "id": "J-100", "work_order": "WO-2401",
            "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
            "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
        })
        first = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        second = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 20, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(first.status, "Posted")
        self.assertEqual(first.code, "")
        self.assertEqual(second.status, "Posted")
        self.assertEqual(second.code, "")
        self.assertEqual(len(c.confirmations), 2)

    def test_criterion_3_refused_confirmation_adds_no_key_and_posts_after_issue(self):
        """Criterion 3: A refused confirmation adds no key: CF-10 before any issue is E-QTY,
        then J-100, then CF-10 posts.
        """
        c = Connector()
        refused = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(refused.status, "Business Rejected")
        self.assertEqual(refused.code, "E-QTY")
        self.assertEqual(len(c.confirmations), 0)
        self.assertNotIn(("CF-10", "WO-2401", 10), c.confirm_keys)

        c.process({
            "kind": "issue", "id": "J-100", "work_order": "WO-2401",
            "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
            "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
        })
        posted = c.process({
            "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
            "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(posted.status, "Posted")
        self.assertEqual(posted.code, "")
        self.assertEqual(len(c.confirmations), 1)

    def test_criterion_4_samples_plus_replayed_confirm_gives_e_dup_and_no_reconcile_gaps(self):
        """Criterion 4: The nine samples still reach 384/116/96/0 with 2 confirmations, and replaying
        03-confirm-op10.json gives E-DUP. Reconcile still shows no gaps.
        """
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir = root / "in"
            done_dir = root / "done"
            results_dir = root / "results"
            inbox_dir.mkdir(parents=True, exist_ok=True)

            for src_file in sorted(SAMPLES.glob("*.json")):
                shutil.copy2(src_file, inbox_dir / src_file.name)

            connector = Connector()
            results = read_all(connector, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
            self.assertEqual(len(results), 9)

            self.assertEqual(connector.kg()["raw"], 384.0)
            self.assertEqual(connector.kg()["wip"], 116.0)
            self.assertEqual(connector.pieces()["hold"], 96.0)
            self.assertEqual(connector.pieces()["finished"], 0.0)
            self.assertEqual(len(connector.confirmations), 2)

            # Replay 03-confirm-op10.json as a 10th file
            tenth = inbox_dir / "10-confirm-op10-repeat.json"
            shutil.copy2(SAMPLES / "03-confirm-op10.json", tenth)

            res_tenth = read_all(connector, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
            self.assertEqual(len(res_tenth), 1)
            self.assertEqual(res_tenth[0]["status"], "Posted")
            self.assertEqual(res_tenth[0]["code"], "E-DUP")
            self.assertEqual(res_tenth[0]["document"], "")
            self.assertEqual(res_tenth[0]["message"], "Already confirmed. Not stored again.")

            # Stock and confirmations unchanged
            self.assertEqual(connector.kg()["raw"], 384.0)
            self.assertEqual(connector.kg()["wip"], 116.0)
            self.assertEqual(connector.pieces()["hold"], 96.0)
            self.assertEqual(connector.pieces()["finished"], 0.0)
            self.assertEqual(len(connector.confirmations), 2)

            # Reconcile still shows 7 stock results, 7 entries, no gaps
            reconcile_result = reconcile(connector, results_dir)
            self.assertEqual(reconcile_result["stock_results"], 7)
            self.assertEqual(reconcile_result["entries"], 7)
            self.assertEqual(reconcile_result["missing_entry"], [])
            self.assertEqual(reconcile_result["missing_result"], [])
            self.assertEqual(reconcile_result["unreadable"], [])

    def test_operation_30_stays_ignored_and_adds_no_key(self):
        """Operation 30 stays ignored and adds no key."""
        c = Connector()
        res = c.process({
            "kind": "confirm", "id": "CF-30", "work_order": "WO-2401",
            "operation": 30, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
        })
        self.assertEqual(res.status, "Posted")
        self.assertEqual(res.code, "")
        self.assertEqual(res.message, "Operation 30 is vendor work and is ignored.")
        self.assertEqual(len(c.confirmations), 0)
        self.assertEqual(len(c.confirm_keys), 0)


if __name__ == "__main__":
    unittest.main()
