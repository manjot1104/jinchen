"""Tests for repeated confirmation idempotency (US-090)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connector.inbox import read_all
from connector.poc import RAW, WIP, Connector
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
        self.assertEqual(len(c._confirm_store), 0)
        self.assertNotIn(("CF-30", "WO-2401", 30), c._confirm_store)
        # Second time is also treated as vendor ignore rather than duplicate
        res2 = c.process(confirm("CF-30", operation=30))
        self.assertEqual(res2.status, "Posted")
        self.assertEqual(res2.code, "")
        self.assertEqual(res2.message, "Operation 30 is vendor work and is ignored.")
        self.assertEqual(len(c._confirm_store), 0)

    def test_replay_after_full_return_gives_e_dup(self):
        """After full return (R-ALL), replayed CF-10 returns Posted E-DUP, not E-QTY."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(first.status, "Posted")
        self.assertEqual(len(c.confirmations), 1)

        ret = c.process(give_back("R-ALL", 120))
        self.assertEqual(ret.status, "Posted")
        self.assertEqual(c.kg()["wip"], 0.0)

        replay = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(replay.status, "Posted")
        self.assertEqual(replay.code, "E-DUP")
        self.assertEqual(replay.document, "")
        self.assertEqual(replay.message, "Already confirmed. Not stored again.")
        self.assertEqual(len(c.confirmations), 1)

    def test_replay_while_order_on_hold_gives_e_dup(self):
        """On hold (H-1), replayed CF-10 returns Posted E-DUP, not E-CLOSED."""
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

    def test_repeat_with_different_quantities_is_refused(self):
        """Repeat with same key but different quantities returns refusal E-QTY."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100, rejected=0, rework=0, scrap=0))
        self.assertEqual(first.status, "Posted")

        # Repeat with changed quantities returns refusal
        repeat = c.process(confirm("CF-10", operation=10, completed=50, rejected=10, rework=0, scrap=0))
        self.assertEqual(repeat.status, "Business Rejected")
        self.assertEqual(repeat.code, "E-QTY")
        self.assertEqual(repeat.message, "Confirmation differs from the stored one. Send it with a new id.")
        self.assertEqual(len(c.confirmations), 1)
        self.assertEqual(c.confirmations[0], ("WO-2401", 10, (100.0, 0.0, 0.0, 0.0)))

        # Bad quantity on repeat also refused with differs
        repeat2 = c.process(confirm("CF-10", operation=10, completed=-5, rejected="abc"))
        self.assertEqual(repeat2.status, "Business Rejected")
        self.assertEqual(repeat2.code, "E-QTY")
        self.assertEqual(repeat2.message, "Confirmation differs from the stored one. Send it with a new id.")

    def test_string_float_operation_normalized_and_repeat_is_e_dup(self):
        """String float operation '10.0' is normalized to 10 and not stored twice."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation="10.0", completed=100))
        self.assertEqual(first.status, "Posted")
        self.assertEqual(first.code, "")
        self.assertEqual(len(c.confirmations), 1)
        self.assertEqual(c.confirmations[0], ("WO-2401", 10, (100.0, 0.0, 0.0, 0.0)))

        repeat = c.process(confirm("CF-10", operation="10.0", completed=100))
        self.assertEqual(repeat.status, "Posted")
        self.assertEqual(repeat.code, "E-DUP")
        self.assertEqual(repeat.message, "Already confirmed. Not stored again.")
        self.assertEqual(len(c.confirmations), 1)

    def test_fractional_operation_returns_e_op_not_e_dup(self):
        """Fractional operation 10.5 returns Business Rejected E-OP, never E-DUP."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(first.status, "Posted")
        self.assertEqual(len(c.confirmations), 1)

        frac = c.process(confirm("CF-10", operation=10.5, completed=100))
        self.assertEqual(frac.status, "Business Rejected")
        self.assertEqual(frac.code, "E-OP")
        self.assertEqual(frac.message, "Operation is not on the demo route.")
        self.assertEqual(len(c.confirmations), 1)

    def test_infinity_and_nan_operation_returns_e_op_without_overflow(self):
        """Operation with Infinity or NaN returns Business Rejected E-OP without OverflowError."""
        c = with_metal()
        res_inf = c.process(confirm("CF-INF", operation=float("inf"), completed=100))
        self.assertEqual(res_inf.status, "Business Rejected")
        self.assertEqual(res_inf.code, "E-OP")

        res_inf_str = c.process(confirm("CF-INF2", operation="Infinity", completed=100))
        self.assertEqual(res_inf_str.status, "Business Rejected")
        self.assertEqual(res_inf_str.code, "E-OP")

        res_nan = c.process(confirm("CF-NAN", operation=float("nan"), completed=100))
        self.assertEqual(res_nan.status, "Business Rejected")
        self.assertEqual(res_nan.code, "E-OP")

    def test_list_or_dict_id_is_refused_without_typeerror(self):
        """A list or dict id returns Business Rejected without raising TypeError."""
        c = Connector()
        res_list = c.process({'kind': 'confirm', 'id': ['CF'], 'work_order': 'WO-2401', 'operation': 10, 'completed': 1})
        self.assertEqual(res_list.status, "Business Rejected")
        self.assertEqual(res_list.code, "E-QTY")
        self.assertEqual(res_list.message, "Id and work order must be strings or numbers.")

        res_dict = c.process({'kind': 'confirm', 'id': {'val': 'CF'}, 'work_order': 'WO-2401', 'operation': 10, 'completed': 1})
        self.assertEqual(res_dict.status, "Business Rejected")
        self.assertEqual(res_dict.code, "E-QTY")
        self.assertEqual(res_dict.message, "Id and work order must be strings or numbers.")

    def test_non_repeat_bad_operation_check_ordering(self):
        """Unparseable operation falls through to normal order."""
        c = with_metal()
        c.process(order("hold", "H-1"))

        # Operation 'ten' on a held order returns E-CLOSED, not E-OP
        res_held = c.process(confirm("CF-NEW", operation="ten", completed=1))
        self.assertEqual(res_held.status, "Business Rejected")
        self.assertEqual(res_held.code, "E-CLOSED")

        # Resume order
        c.process(order("resume", "RS-1"))

        # With rejected 'x' added on open order, returns E-QTY, not E-OP
        res_bad_qty = c.process(confirm("CF-NEW2", operation="ten", completed=1, rejected="x"))
        self.assertEqual(res_bad_qty.status, "Business Rejected")
        self.assertEqual(res_bad_qty.code, "E-QTY")

    def test_replay_while_order_cancelled_gives_e_dup(self):
        """Cancelled order replay with identical quantities returns Posted E-DUP."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(first.status, "Posted")
        self.assertEqual(len(c.confirmations), 1)

        # Return steel so order can be cancelled
        ret = c.process(give_back("R-ALL", 120))
        self.assertEqual(ret.status, "Posted")
        cancelled = c.process(order("cancel", "X-1"))
        self.assertEqual(cancelled.status, "Posted")
        self.assertEqual(c.order_state.get("WO-2401"), "cancelled")

        # Replay with identical quantities returns Posted E-DUP
        replay = c.process(confirm("CF-10", operation=10, completed=100))
        self.assertEqual(replay.status, "Posted")
        self.assertEqual(replay.code, "E-DUP")
        self.assertEqual(replay.document, "")
        self.assertEqual(replay.message, "Already confirmed. Not stored again.")
        self.assertEqual(len(c.confirmations), 1)

    def test_conflicting_replay_on_cancelled_order_returns_e_closed(self):
        """Conflicting replay on cancelled order falls through to E-CLOSED instead of E-QTY."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=50))
        self.assertEqual(first.status, "Posted")

        ret = c.process(give_back("R-ALL", 120))
        self.assertEqual(ret.status, "Posted")
        c.process(order("cancel", "X-1"))
        self.assertEqual(c.order_state.get("WO-2401"), "cancelled")

        # Replay with differing quantities on cancelled order
        replay = c.process(confirm("CF-10", operation=10, completed=60))
        self.assertEqual(replay.status, "Business Rejected")
        self.assertEqual(replay.code, "E-CLOSED")
        self.assertEqual(replay.message, "Work order is cancelled.")

    def test_conflicting_replay_on_held_order_returns_e_closed(self):
        """Conflicting replay on held order falls through to E-CLOSED instead of E-QTY."""
        c = with_metal()
        first = c.process(confirm("CF-10", operation=10, completed=50))
        self.assertEqual(first.status, "Posted")

        c.process(order("hold", "H-1"))
        self.assertEqual(c.order_state.get("WO-2401"), "on hold")

        # Replay with differing quantities on held order
        replay = c.process(confirm("CF-10", operation=10, completed=60))
        self.assertEqual(replay.status, "Business Rejected")
        self.assertEqual(replay.code, "E-CLOSED")
        self.assertEqual(replay.message, "Work order is on hold.")

    def test_huge_integer_does_not_raise_overflow_error(self):
        """Huge integers in operation or quantities return appropriate refusal without OverflowError."""
        c = with_metal()
        # Operation 10**400
        res = c.process(confirm("CF-HUGE", operation=10**400, completed=10))
        self.assertEqual(res.status, "Business Rejected")
        self.assertEqual(res.code, "E-OP")
        self.assertEqual(res.message, "Operation is not on the demo route.")

        # On held order, operation 10**400 still gives E-CLOSED without crashing
        c.process(order("hold", "H-1"))
        res_held = c.process(confirm("CF-HUGE2", operation=10**400, completed=10))
        self.assertEqual(res_held.status, "Business Rejected")
        self.assertEqual(res_held.code, "E-CLOSED")
        self.assertEqual(res_held.message, "Work order is on hold.")
        c.process(order("resume", "RS-1"))

        # Quantity 10**400 in NUMBER_FIELDS
        res_qty = c.process(confirm("CF-HUGE3", operation=10, completed=10**400))
        self.assertEqual(res_qty.status, "Business Rejected")
        self.assertEqual(res_qty.code, "E-QTY")
        self.assertEqual(res_qty.message, "Quantity is not a number.")

    def test_nan_and_infinite_quantities_refused(self):
        """Non-finite quantities (NaN, Inf) are refused with E-QTY Quantity is not a number."""
        c = with_metal()
        res_nan = c.process(confirm("CF-NAN", operation=10, completed="nan"))
        self.assertEqual(res_nan.status, "Business Rejected")
        self.assertEqual(res_nan.code, "E-QTY")
        self.assertEqual(res_nan.message, "Quantity is not a number.")

        res_inf = c.process(confirm("CF-INF", operation=10, completed=float("inf")))
        self.assertEqual(res_inf.status, "Business Rejected")
        self.assertEqual(res_inf.code, "E-QTY")
        self.assertEqual(res_inf.message, "Quantity is not a number.")

        # Verify nothing was stored
        self.assertEqual(len(c.confirmations), 0)
        self.assertEqual(len(c._confirm_store), 0)

    def test_numeric_ids_accepted_and_converted_to_strings(self):
        """Numeric ids and work orders (int and float) are converted to strings."""
        c = Connector()
        # Integer ID on issue
        res_int = c.process({
            "kind": "issue",
            "id": 100,
            "work_order": 2401,
            "item": "ST-20",
            "batch": "H-441",
            "qty": 50,
            "uom": "Kg",
            "from_warehouse": RAW,
            "to_warehouse": WIP,
        })
        self.assertEqual(res_int.status, "Posted")
        self.assertEqual(c.ledger.entries[-1].ref, "100")
        self.assertEqual(c.ledger.entries[-1].work_order, "2401")

        # Float ID on confirm
        res_float = c.process({
            "kind": "confirm",
            "id": 10.5,
            "work_order": "2401",
            "operation": 10,
            "completed": 20,
        })
        self.assertEqual(res_float.status, "Posted")
        self.assertIn(("10.5", "2401", 10), c._confirm_store)

    def test_unhashable_kind_refused_with_e_item(self):
        """An unhashable kind such as a list returns Business Rejected E-ITEM without raising TypeError."""
        c = Connector()
        res = c.process({"kind": ["confirm"], "id": "CF-1", "work_order": "WO-2401"})
        self.assertEqual(res.status, "Business Rejected")
        self.assertEqual(res.code, "E-ITEM")
        self.assertEqual(res.message, "Unknown message.")

    def test_bool_operation_refused_with_e_op(self):
        """Boolean operation (True or False) returns Business Rejected E-OP."""
        c = with_metal()
        res_false = c.process(confirm("CF-BOOL1", operation=False, completed=10))
        self.assertEqual(res_false.status, "Business Rejected")
        self.assertEqual(res_false.code, "E-OP")
        self.assertEqual(res_false.message, "Operation is not on the demo route.")

        res_true = c.process(confirm("CF-BOOL2", operation=True, completed=10))
        self.assertEqual(res_true.status, "Business Rejected")
        self.assertEqual(res_true.code, "E-OP")
        self.assertEqual(res_true.message, "Operation is not on the demo route.")

    def test_e_dup_log_row_has_no_quantities(self):
        """Repeated confirmation logs E-DUP with empty quantities."""
        c = with_metal()
        c.process(confirm("CF-10", operation=10, completed=100))
        c.process(confirm("CF-10", operation=10, completed=100))

        confirm_logs = [row for row in c.log if row.kind == "confirm"]
        self.assertEqual(len(confirm_logs), 2)
        self.assertEqual(confirm_logs[0].code, "")
        self.assertEqual(confirm_logs[0].quantities, {"completed": 100.0, "rejected": 0.0, "rework": 0.0, "scrap": 0.0})
        self.assertEqual(confirm_logs[1].code, "E-DUP")
        self.assertEqual(confirm_logs[1].quantities, {})


class ConfirmationsAreReadOnly(unittest.TestCase):
    def test_confirmations_cannot_be_assigned(self):
        c = Connector()
        with self.assertRaises(AttributeError):
            c.confirmations = [("WO-2401", 10, (1.0, 0.0, 0.0, 0.0))]

    def test_missing_work_order_still_logs_the_id(self):
        c = Connector()
        result = c.process({"kind": "confirm", "id": "CF-9", "operation": 10, "completed": 1})
        self.assertEqual((result.status, result.code), ("Business Rejected", "E-QTY"))
        self.assertEqual(result.message, "Id and work order are required.")
        self.assertEqual(c.log[-1].ref, "CF-9")
        self.assertEqual(c.log[-1].work_order, "")


if __name__ == "__main__":
    unittest.main()

