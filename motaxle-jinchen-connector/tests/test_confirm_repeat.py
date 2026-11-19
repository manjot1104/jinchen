"""Tests for repeated confirmation idempotency (US-090)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from connector.inbox import read_all
from connector.poc import Connector
from connector.reconcile import reconcile
from tests.test_stand_in_rules import confirm, give_back, issue, order, with_metal

PROJECT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT / "inbox" / "samples"


class ConfirmRepeatTests(unittest.TestCase):
    def test_criterion_1_repeated_confirm_returns_e_dup_code_and_message(self):
        """Criterion 1: After J-100, CF-10 twice: Posted, then Posted with E-DUP and
        Already confirmed. Not stored again.
        """
        c = Connector()
        c.process(issue())
        first = c.process(confirm())
        second = c.process(confirm())
        self.assertEqual(first.status, "Posted")
        self.assertEqual(first.code, "")
        self.assertEqual(second.status, "Posted")
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(second.document, "")
        self.assertEqual(second.message, "Already confirmed. Not stored again.")

    def test_criterion_1_repeated_confirm_does_not_add_to_confirmations_list(self):
        """Criterion 1: confirmations has 1 row after CF-10 sent twice."""
        c = Connector()
        c.process(issue())
        c.process(confirm())
        c.process(confirm())
        self.assertEqual(len(c.confirmations), 1)
        self.assertEqual(c.confirmations[0], ("WO-2401", 10, (100.0, 0.0, 0.0, 0.0)))

    def test_criterion_1_repeated_confirm_logs_both_attempts(self):
        """Criterion 1: the log has 2 rows for confirmations."""
        c = Connector()
        c.process(issue())
        c.process(confirm())
        c.process(confirm())
        confirm_logs = [row for row in c.log if row.kind == "confirm"]
        self.assertEqual(len(confirm_logs), 2)
        self.assertEqual(confirm_logs[0].code, "")
        self.assertEqual(confirm_logs[1].code, "E-DUP")

    def test_criterion_2_same_id_with_different_operation_still_posts(self):
        """Criterion 2: The key is id + work order + operation: CF-10 with operation 20 still posts."""
        c = Connector()
        c.process(issue())
        first = c.process(confirm("CF-10", operation=10))
        second = c.process(confirm("CF-10", operation=20))
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
        refused = c.process(confirm())
        self.assertEqual(refused.status, "Business Rejected")
        self.assertEqual(refused.code, "E-QTY")
        self.assertEqual(len(c.confirmations), 0)
        self.assertNotIn(("CF-10", "WO-2401", 10), c.confirm_keys)

        c.process(issue())
        posted = c.process(confirm())
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
        res = c.process(confirm("CF-30", operation=30))
        self.assertEqual(res.status, "Posted")
        self.assertEqual(res.code, "")
        self.assertEqual(res.message, "Operation 30 is vendor work and is ignored.")
        self.assertEqual(len(c.confirmations), 0)
        self.assertEqual(len(c.confirm_keys), 0)

    def test_replay_after_full_return_gives_e_dup(self):
        """Review case 1: After full return (R-ALL), replayed CF-10 returns Posted E-DUP, not E-QTY."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(first.status, "Posted")
        self.assertEqual(len(c.confirmations), 1)

        ret = c.process(give_back("R-ALL", 120))
        self.assertEqual(ret.status, "Posted")
        self.assertEqual(c._kg_out("WO-2401"), 0.0)

        replay = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(replay.status, "Posted")
        self.assertEqual(replay.code, "E-DUP")
        self.assertEqual(replay.document, "")
        self.assertEqual(replay.message, "Already confirmed. Not stored again.")
        self.assertEqual(len(c.confirmations), 1)

    def test_replay_while_order_on_hold_gives_e_dup(self):
        """Review case 2: On hold (H-1), replayed CF-10 returns Posted E-DUP, not E-CLOSED."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(first.status, "Posted")
        self.assertEqual(len(c.confirmations), 1)

        held = c.process(order("hold", "H-1"))
        self.assertEqual(held.status, "Posted")
        self.assertEqual(c.order_state.get("WO-2401"), "on hold")

        replay = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(replay.status, "Posted")
        self.assertEqual(replay.code, "E-DUP")
        self.assertEqual(replay.document, "")
        self.assertEqual(replay.message, "Already confirmed. Not stored again.")
        self.assertEqual(len(c.confirmations), 1)

    def test_repeat_with_different_quantities_logs_stored_quantities(self):
        """Review case 3: Repeat with same key but different quantities returns E-DUP and logs stored quantities."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100, rejected=0, rework=0, scrap=0))
        self.assertEqual(first.status, "Posted")

        # Repeat with changed quantities
        repeat = c.process(confirm("CF-10", operation=10, completed=50, rejected=10, rework=0, scrap=0))
        self.assertEqual(repeat.status, "Posted")
        self.assertEqual(repeat.code, "E-DUP")
        self.assertEqual(repeat.message, "Already confirmed. Not stored again.")
        self.assertEqual(len(c.confirmations), 1)
        self.assertEqual(c.confirmations[0], ("WO-2401", 10, (100.0, 0.0, 0.0, 0.0)))

        confirm_logs = [row for row in c.log if row.kind == "confirm"]
        self.assertEqual(len(confirm_logs), 2)
        self.assertEqual(confirm_logs[0].quantities, {"completed": 100.0, "rejected": 0.0, "rework": 0.0, "scrap": 0.0})
        # The repeated log row must record stored quantities (100, 0, 0, 0), not incoming (50, 10, 0, 0)
        self.assertEqual(confirm_logs[1].quantities, {"completed": 100.0, "rejected": 0.0, "rework": 0.0, "scrap": 0.0})


if __name__ == "__main__":
    unittest.main()

