import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from connector.inbox import read_next
from connector.poc import Connector
from server import Desk


def msg_issue(ref="J-100", wo="WO-2401"):
    return {
        "kind": "issue",
        "id": ref,
        "work_order": wo,
        "item": "ST-20",
        "qty": 120,
        "uom": "Kg",
        "batch": "H-441",
        "from_warehouse": "Raw Material Store",
        "to_warehouse": "Forging WIP",
    }


def msg_return(ref="R-1", wo="WO-2401"):
    return {
        "kind": "return",
        "id": ref,
        "work_order": wo,
        "against": "J-100",
        "item": "ST-20",
        "qty": 4,
        "uom": "Kg",
        "batch": "H-441",
    }


class CountsAndLogScreenTests(unittest.TestCase):
    def setUp(self):
        self.desk = Desk()

    def test_counts_initial_state_difference_zero(self):
        """AC1: Initially no rows, difference is 0."""
        counts = self.desk.counts()
        self.assertEqual(counts["posted"], 0)
        self.assertEqual(counts["business_rejected"], 0)
        self.assertEqual(counts["dead"], 0)
        self.assertEqual(counts["total"], 0)
        self.assertEqual(counts["difference"], 0)
        self.assertEqual(counts["difference"], len(self.desk.attempts) - counts["total"])

    def test_counts_match_log_rows_difference_always_zero(self):
        """AC1: Counts match log rows (difference = 0) across posted, rejected, and dead rows."""
        # 1. Posted action
        self.desk.apply("issue", msg_issue("J-100"))
        counts1 = self.desk.counts()
        self.assertEqual(counts1["posted"], 1)
        self.assertEqual(counts1["difference"], 0)
        self.assertEqual(counts1["total"], len(self.desk.attempts))

        # 2. Business rejected action (wrong item ST20)
        bad_item = msg_issue("J-BAD")
        bad_item["item"] = "ST20"
        self.desk.apply("bad-item", bad_item)
        counts2 = self.desk.counts()
        self.assertEqual(counts2["posted"], 1)
        self.assertEqual(counts2["business_rejected"], 1)
        self.assertEqual(counts2["difference"], 0)
        self.assertEqual(counts2["total"], len(self.desk.attempts))

        # 3. Dead / Stopped / Queued action (switch A on)
        self.desk.set_switch("A", True)
        self.desk.apply("queued-msg", msg_issue("J-QUEUED"))
        counts3 = self.desk.counts()
        self.assertEqual(counts3["posted"], 1)
        self.assertEqual(counts3["business_rejected"], 1)
        self.assertEqual(counts3["dead"], 1)
        self.assertEqual(counts3["difference"], 0)
        self.assertEqual(counts3["total"], len(self.desk.attempts))

        # 4. Release with Switch B on (Stopped -> dead)
        self.desk.set_switch("B", True)
        self.desk.release()
        counts4 = self.desk.counts()
        self.assertEqual(counts4["dead"], 2)
        self.assertEqual(counts4["difference"], 0)
        self.assertEqual(counts4["total"], len(self.desk.attempts))

        # 5. Normal Release (Sent -> posted)
        self.desk.set_switch("B", False)
        self.desk.release()
        counts5 = self.desk.counts()
        self.assertEqual(counts5["posted"], 2)
        self.assertEqual(counts5["difference"], 0)
        self.assertEqual(counts5["total"], len(self.desk.attempts))

    def test_last_successful_call_updates_after_call(self):
        """AC2: Last successful call time shown and updates after a call."""
        # Initially None
        self.assertIsNone(self.desk.last_successful_call)

        # Successful call updates timestamp
        self.desk.apply("issue", msg_issue("J-100"))
        first_time = self.desk.last_successful_call
        self.assertIsNotNone(first_time)

        # Unsuccessful call (business rejected) does NOT advance or clear last_successful_call
        bad_heat = msg_issue("J-HEAT")
        bad_heat["batch"] = "H441"
        self.desk.apply("bad-heat", bad_heat)
        self.assertEqual(self.desk.last_successful_call, first_time)

        # Queued call does NOT advance last_successful_call
        self.desk.set_switch("A", True)
        self.desk.apply("queued", msg_issue("J-QUEUED"))
        self.assertEqual(self.desk.last_successful_call, first_time)
        self.desk.set_switch("A", False)

        # Another successful call updates timestamp
        with patch("server.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-11-23 12:34:56"
            self.desk.apply("return", msg_return("R-1"))
            self.assertEqual(self.desk.last_successful_call, "2026-11-23 12:34:56")

        # Reset clears the timestamp
        self.desk.reset()
        self.assertIsNone(self.desk.last_successful_call)

    def test_log_filter_by_work_order(self):
        """AC3: Log filter by WO-2401 returns only that order."""
        # Add a message for WO-2401
        self.desk.apply("issue", msg_issue("J-100", wo="WO-2401"))

        # Add a message for a different work order WO-9999
        self.desk.apply("other-order", msg_issue("J-999", wo="WO-9999"))

        # Add a release (WO-2401)
        self.desk.release()

        # Check that rows store work_order
        self.assertEqual(self.desk.attempts[0]["work_order"], "WO-2401")
        self.assertEqual(self.desk.attempts[1]["work_order"], "WO-9999")
        self.assertEqual(self.desk.attempts[2]["work_order"], "WO-2401")

        # Emulate the client-side log filter logic:
        # const blob = (row.id + " " + (row.work_order || "") + " " + row.code + " " + row.status + " " + row.document + " " + row.message).toLowerCase();
        # return !query || blob.indexOf(query) >= 0;
        def filter_rows(rows, query):
            q = query.lower()
            return [
                r for r in rows
                if not q or q in (
                    f"{r['id']} {r.get('work_order', '')} {r['code']} {r['status']} {r['document']} {r['message']}"
                ).lower()
            ]

        filtered_wo2401 = filter_rows(self.desk.attempts, "WO-2401")
        filtered_wo9999 = filter_rows(self.desk.attempts, "WO-9999")

        # Filter by WO-2401 returns only WO-2401 rows
        self.assertEqual(len(filtered_wo2401), 2)
        for r in filtered_wo2401:
            self.assertEqual(r["work_order"], "WO-2401")

        # Filter by WO-9999 returns only WO-9999 rows
        self.assertEqual(len(filtered_wo9999), 1)
        self.assertEqual(filtered_wo9999[0]["id"], "other-order")
        self.assertEqual(filtered_wo9999[0]["work_order"], "WO-9999")

    def test_payload_exposes_counts_and_last_successful_call(self):
        """Verify /api/state payload includes counts dictionary and last_successful_call."""
        payload = self.desk.payload()
        self.assertIn("counts", payload)
        self.assertIn("last_successful_call", payload)
        self.assertEqual(payload["counts"]["difference"], 0)
        self.assertIsNone(payload["last_successful_call"])

        self.desk.apply("issue", msg_issue("J-100"))
        updated_payload = self.desk.payload()
        self.assertEqual(updated_payload["counts"]["posted"], 1)
        self.assertEqual(updated_payload["counts"]["difference"], 0)
        self.assertIsNotNone(updated_payload["last_successful_call"])

    def test_inbox_read_captures_work_order_and_updates_counts(self):
        """Verify inbox reading extracts work_order and updates counts & last call."""
        temp_dir = Path(tempfile.mkdtemp())
        inbox_in = temp_dir / "in"
        inbox_done = temp_dir / "done"
        inbox_results = temp_dir / "results"
        inbox_in.mkdir(parents=True)

        try:
            file_path = inbox_in / "msg1.json"
            file_path.write_text(json.dumps(msg_issue("J-INBOX", wo="WO-2401")), encoding="utf-8")

            connector = Connector()
            result = read_next(
                connector=connector,
                done_dir=inbox_done,
                results_dir=inbox_results,
                source_file=file_path,
                switch_a=False,
            )
            self.assertEqual(result.get("work_order"), "WO-2401")
            self.assertEqual(result.get("status"), "Posted")

            # Test Desk.read_inbox with mocked list_inbox and read_all
            with patch("server.list_inbox", return_value=[file_path]), \
                 patch("server.read_all", return_value=[result]):
                self.desk.read_inbox()

            self.assertEqual(len(self.desk.attempts), 1)
            row = self.desk.attempts[0]
            self.assertEqual(row["work_order"], "WO-2401")
            self.assertEqual(row["status"], "Posted")
            self.assertEqual(self.desk.counts()["posted"], 1)
            self.assertEqual(self.desk.counts()["difference"], 0)
            self.assertIsNotNone(self.desk.last_successful_call)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_replays_maintain_difference_zero_at_every_step(self):
        """Rule 6 & Rule 3: Replaying every action maintains difference = 0 and updates counts correctly."""
        # Step 1: Issue J-100 (posted)
        r1 = self.desk.run("issue")
        self.assertEqual(r1["status"], "Posted")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["posted"], 1)

        # Step 2: Replay Issue J-100 (posted with E-DUP)
        r2 = self.desk.run("issue-again")
        self.assertEqual(r2["code"], "E-DUP")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["posted"], 2)

        # Step 3: Return R-1 (posted)
        r3 = self.desk.run("return")
        self.assertEqual(r3["status"], "Posted")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["posted"], 3)

        # Step 4: Replay Return R-1 (posted with E-DUP)
        r4 = self.desk.run("return-again")
        self.assertEqual(r4["code"], "E-DUP")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["posted"], 4)

        # Step 5: Bad input (business rejected)
        r5 = self.desk.run("bad-item")
        self.assertEqual(r5["code"], "E-ITEM")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["business_rejected"], 1)

        # Step 6: Confirm CF-10 (posted)
        r6 = self.desk.run("confirm-10")
        self.assertEqual(r6["status"], "Posted")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["posted"], 5)

        # Step 7: Replay Confirm CF-10 (posted)
        r7 = self.desk.run("confirm-10")
        self.assertEqual(r7["status"], "Posted")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["posted"], 6)

        # Step 8: Switch A on, then issue (queued -> dead)
        self.desk.set_switch("A", True)
        r8 = self.desk.apply("queued", msg_issue("J-QUEUED"))
        self.assertEqual(r8["status"], "Queued")
        c = self.desk.counts()
        self.assertEqual(c["difference"], 0)
        self.assertEqual(c["dead"], 1)

        self.assertEqual(c["total"], len(self.desk.attempts))
        self.assertEqual(c["total"], 8)
        self.assertEqual(c["difference"], 0)

    def test_non_dict_message_graceful_rejection(self):
        """Rule 1 & Rule 4: Non-dict inputs are safely refused without crash and counted."""
        for bad_msg in [None, "hello", [1, 2, 3], 123]:
            row = self.desk.apply("bad", bad_msg)
            self.assertEqual(row["status"], "Business Rejected")
            self.assertEqual(row["code"], "E-ITEM")
            self.assertEqual(self.desk.counts()["difference"], 0)

    def test_reset_full_state_snapshot(self):
        """Rule 2 & Rule 3: State snapshot before and after reset."""
        self.desk.apply("issue", msg_issue("J-100"))
        self.desk.apply("return", msg_return("R-1"))
        self.assertIsNotNone(self.desk.last_successful_call)
        self.assertEqual(len(self.desk.attempts), 2)
        self.assertEqual(self.desk.counts()["posted"], 2)

        # Reset
        self.desk.reset()
        self.assertIsNone(self.desk.last_successful_call)
        self.assertEqual(len(self.desk.attempts), 0)
        c = self.desk.counts()
        self.assertEqual(c["posted"], 0)
        self.assertEqual(c["business_rejected"], 0)
        self.assertEqual(c["dead"], 0)
        self.assertEqual(c["total"], 0)
        self.assertEqual(c["difference"], 0)


if __name__ == "__main__":
    unittest.main()
