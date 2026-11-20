import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server
from connector.poc import Connector
from server import Desk


def msg_issue():
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


def msg_return():
    return {
        "kind": "return",
        "id": "R-1",
        "work_order": "WO-2401",
        "against": "J-100",
        "item": "ST-20",
        "qty": 4,
        "uom": "Kg",
        "batch": "H-441",
    }


def msg_receipt():
    return {
        "kind": "receipt",
        "id": "C-1",
        "work_order": "WO-2401",
        "item": "CR-100",
        "qty": 40,
        "uom": "Nos",
        "batch": "B-1",
    }


class LogTests(unittest.TestCase):
    def test_three_messages_written_to_log_file(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c = Connector(log_path=str(log_file))
            c.process(msg_issue())
            c.process(msg_return())
            c.process(msg_receipt())

            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 3)

    def test_new_connector_loads_exact_log_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c1 = Connector(log_path=str(log_file))
            c1.process(msg_issue())
            c1.process(msg_return())
            c1.process(msg_receipt())

            c2 = Connector(log_path=str(log_file))
            self.assertEqual(len(c2.log), 3)
            for row1, row2 in zip(c1.log, c2.log):
                self.assertEqual(row1.ref, row2.ref)
                self.assertEqual(row1.work_order, row2.work_order)
                self.assertEqual(row1.kind, row2.kind)
                self.assertEqual(row1.status, row2.status)
                self.assertEqual(row1.code, row2.code)
                self.assertEqual(row1.document, row2.document)
                self.assertEqual(row1.quantities, row2.quantities)

    def test_drop_log_writes_no_line(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c = Connector(log_path=str(log_file))
            c.process(msg_issue(), drop_log=True)
            self.assertEqual(len(c.log), 0)
            if log_file.exists():
                self.assertEqual(log_file.read_text(encoding="utf-8").strip(), "")

    def test_broken_half_line_skipped_without_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c1 = Connector(log_path=str(log_file))
            c1.process(msg_issue())
            c1.process(msg_return())

            with open(log_file, "a", encoding="utf-8") as f:
                f.write('{"ref": "incomplete\n')

            c2 = Connector(log_path=str(log_file))
            self.assertEqual(len(c2.log), 2)
            self.assertEqual(c2.log[0].ref, "J-100")
            self.assertEqual(c2.log[1].ref, "R-1")

    def test_desk_reset_clears_log_file_and_records_message(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))
            desk.run("confirm-10")
            self.assertTrue(log_file.exists())
            desk.reset()
            self.assertFalse(log_file.exists())
            self.assertEqual(len(desk.attempts), 1)
            self.assertEqual(desk.attempts[0]["id"], "reset")
            self.assertEqual(desk.attempts[0]["status"], "Posted")
            self.assertEqual(
                desk.attempts[0]["message"],
                "Job reset. The message log was cleared.",
            )

    def test_desk_with_no_log_path_creates_no_file(self):
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = os.getcwd()
            os.chdir(temp)
            try:
                desk = Desk()
                self.assertIsNone(desk.log_path)
                desk.run("confirm-10")
                self.assertFalse((Path(temp) / "inbox" / "log.jsonl").exists())
                self.assertFalse((Path(temp) / "log.jsonl").exists())
            finally:
                os.chdir(old_cwd)

    def test_desk_restart_loads_same_three_rows_in_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk1 = Desk(log_path=str(log_file))
            desk1.run("confirm-10")
            desk1.run("confirm-20")
            desk1.run("confirm-30")
            self.assertEqual(len(desk1.attempts), 3)

            desk2 = Desk(log_path=str(log_file))
            self.assertEqual(len(desk2.attempts), 3)
            for row in desk2.attempts:
                self.assertEqual(row["message"], "Read back from the log file.")
            self.assertEqual(desk2.attempts[0]["id"], "confirm-10")
            self.assertEqual(desk2.attempts[1]["id"], "confirm-20")
            self.assertEqual(desk2.attempts[2]["id"], "confirm-30")

    def test_log_file_contains_no_password(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c = Connector(log_path=str(log_file))
            c.process(msg_issue())
            c.process(msg_return())
            c.process(msg_receipt())

            content = log_file.read_text(encoding="utf-8")
            self.assertNotIn("password", content.lower())
            self.assertNotIn("live_password", content)

    def test_duplicate_rejected_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c1 = Connector(log_path=str(log_file))
            res1 = c1.process(msg_issue())
            self.assertEqual(res1.status, "Posted")
            self.assertEqual(res1.code, "")
            self.assertEqual(res1.document, "STE-0001")

            c2 = Connector(log_path=str(log_file))
            res2 = c2.process(msg_issue())
            self.assertEqual(res2.status, "Posted")
            self.assertEqual(res2.code, "E-DUP")
            self.assertEqual(res2.document, "STE-0001")
            self.assertEqual(res2.message, "Already posted. No second stock entry.")
            self.assertEqual(len(c2.ledger.entries), 1)

    def test_oserror_in_log_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c = Connector(log_path=str(log_file))
            with mock.patch("builtins.open", side_effect=OSError("Disk full")):
                res = c.process(msg_issue())
            self.assertEqual(res.status, "Posted")
            self.assertEqual(len(c.log), 1)
            self.assertTrue(getattr(c, "log_file_error", False))

    def test_fragment_without_trailing_newline_fixed_before_append(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            row1 = json.dumps({
                "ref": "J-100", "work_order": "WO-2401", "kind": "issue",
                "status": "Posted", "code": "", "document": "STE-0001",
                "quantities": {}, "attempt_id": "issue",
            })
            log_file.write_text(row1, encoding="utf-8")

            c = Connector(log_path=str(log_file))
            c.process(msg_return())

            c2 = Connector(log_path=str(log_file))
            self.assertEqual(len(c2.log), 2)
            self.assertEqual(c2.log[0].ref, "J-100")
            self.assertEqual(c2.log[1].ref, "R-1")

    def test_non_utf8_byte_in_log_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            c1 = Connector(log_path=str(log_file))
            c1.process(msg_issue())

            with open(log_file, "ab") as f:
                f.write(b"\xff\xfe\xfd\n")

            c2 = Connector(log_path=str(log_file))
            self.assertEqual(len(c2.log), 1)
            self.assertEqual(c2.log[0].ref, "J-100")

    def test_invalid_quantities_skipped_without_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(json.dumps({"ref": "BAD-1", "work_order": "WO-2401", "kind": "issue", "quantities": "string"}) + "\n")
                f.write(json.dumps({"ref": "BAD-2", "work_order": "WO-2401", "kind": "issue", "quantities": [1, 2]}) + "\n")
                f.write(json.dumps({"ref": "BAD-3", "work_order": "WO-2401", "kind": "issue", "quantities": 123}) + "\n")
                f.write(json.dumps({"ref": "OK-1", "work_order": "WO-2401", "kind": "issue", "status": "Posted", "document": "STE-0001", "quantities": {}}) + "\n")

            c = Connector(log_path=str(log_file))
            self.assertEqual(len(c.log), 1)
            self.assertEqual(c.log[0].ref, "OK-1")

    def test_desk_reset_truncates_if_remove_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))
            desk.run("confirm-10")
            self.assertTrue(log_file.exists())
            with mock.patch("os.remove", side_effect=OSError("Permission denied")):
                desk.reset()
            self.assertTrue(log_file.exists())
            self.assertEqual(log_file.read_text(encoding="utf-8"), "")
            self.assertEqual(len(desk.attempts), 1)
            self.assertEqual(desk.attempts[0]["message"], "Job reset. The message log was cleared.")

    def test_criterion_1_restart_restores_raw_wip_and_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk1 = Desk(log_path=str(log_file))
            desk1.run("issue-again")
            desk1.run("return")
            desk1.run("confirm-10")
            desk1.run("confirm-20")
            desk1.run("c1")

            desk2 = Desk(log_path=str(log_file))
            self.assertEqual(desk2.connector.kg()["raw"], 384.0)
            self.assertEqual(desk2.connector._kg_out("WO-2401"), 116.0)
            self.assertEqual(desk2.connector.pieces()["hold"], 40.0)

    def test_criterion_2_duplicate_and_cancel_and_fresh_confirm(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk1 = Desk(log_path=str(log_file))
            desk1.run("issue-again")
            desk1.run("return")
            desk1.run("confirm-10")
            desk1.run("confirm-20")
            desk1.run("c1")

            desk2 = Desk(log_path=str(log_file))
            res_dup = desk2.run("issue-again")
            self.assertEqual(res_dup["status"], "Posted")
            self.assertEqual(res_dup["code"], "E-DUP")
            self.assertEqual(res_dup["document"], "STE-0001")

            res_cancel = desk2.apply("cancel", {"kind": "cancel", "id": "WO-CAN", "work_order": "WO-2401"})
            self.assertEqual(res_cancel["status"], "Business Rejected")
            self.assertEqual(res_cancel["code"], "E-QTY")
            self.assertEqual(res_cancel["message"], "Steel is still out. Return or scrap it before cancel.")

        with tempfile.TemporaryDirectory() as temp2:
            log_file2 = Path(temp2) / "log.jsonl"
            d_fresh1 = Desk(log_path=str(log_file2))
            d_fresh1.run("issue-again")

            d_fresh2 = Desk(log_path=str(log_file2))
            res_conf = d_fresh2.run("confirm-10")
            self.assertEqual(res_conf["status"], "Posted")

    def test_criterion_3_operations_confirmed_and_lookup_equal(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk1 = Desk(log_path=str(log_file))
            desk1.run("issue-again")
            desk1.run("return")
            desk1.run("confirm-10")
            desk1.run("confirm-20")
            desk1.run("c1")
            lookup_before = desk1.connector.lookup("B-1")

            desk2 = Desk(log_path=str(log_file))
            payload2 = desk2.payload()
            ops = {op["op"]: op["state"] for op in payload2["operations"]}
            self.assertEqual(ops[10], "Confirmed")
            self.assertEqual(ops[20], "Confirmed")
            self.assertEqual(desk2.connector.lookup("B-1"), lookup_before)

    def test_old_row_with_no_entry_field_is_history_only(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            row = {
                "ref": "J-100",
                "work_order": "WO-2401",
                "kind": "issue",
                "status": "Posted",
                "code": "",
                "document": "STE-0001",
                "quantities": {},
                "attempt_id": "issue-again",
            }
            log_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

            desk = Desk(log_path=str(log_file))
            self.assertEqual(len(desk.attempts), 1)
            self.assertEqual(desk.attempts[0]["id"], "issue-again")
    def test_review_return_skipped_on_replay_if_source_issue_dropped(self):
        """Review point 1: Return R-1 replayed with no source issue does not turn WIP negative or Raw > 500."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            # Return R-1 against J-100, but J-100 was drop_log so it's not in the file
            return_entry = {
                "name": "STE-0002",
                "kind": "return",
                "ref": "R-1",
                "work_order": "WO-2401",
                "item": "ST-20",
                "qty": 4.0,
                "uom": "Kg",
                "batch": "H-441",
                "source": "Forging WIP",
                "target": "Raw Material Store",
                "against": "J-100",
            }
            row = {
                "ref": "R-1",
                "work_order": "WO-2401",
                "kind": "return",
                "status": "Posted",
                "code": "",
                "document": "STE-0002",
                "quantities": {},
                "attempt_id": "02-return-R-1.json",
                "entry": return_entry,
            }
            log_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

            c = Connector(log_path=str(log_file))
            self.assertEqual(c.kg()["wip"], 0.0)
            self.assertEqual(c.kg()["raw"], 500.0)
            self.assertEqual(c._kg_out("WO-2401"), 0.0)
            self.assertEqual(len(c.ledger.entries), 0)
            self.assertEqual(len(c.log), 1)

    def test_review_e_dup_work_order_events_skipped_on_replay(self):
        """Review point 2: E-DUP repeats of hold/resume/cancel do not change state on replay."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))
            desk.apply("hold", {"kind": "hold", "id": "H-1", "work_order": "WO-2401"})
            desk.apply("resume", {"kind": "resume", "id": "RS-1", "work_order": "WO-2401"})
            desk.apply("hold", {"kind": "hold", "id": "H-1", "work_order": "WO-2401"})  # E-DUP

            desk2 = Desk(log_path=str(log_file))
            # Order must not be on hold after restart because last hold was E-DUP
            self.assertNotIn("WO-2401", desk2.connector.order_state)
            self.assertEqual(len(desk2.connector.order_events), 2)  # only H-1 and RS-1, no duplicate H-1

    def test_review_confirm_op_30_not_restored_on_restart(self):
        """Review point 3: Op-30 confirm is ignored at runtime and not restored on restart."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            row = {
                "ref": "CF-30",
                "work_order": "WO-2401",
                "kind": "confirm",
                "status": "Posted",
                "code": "",
                "document": "",
                "quantities": {"completed": 100, "rejected": 0, "rework": 0, "scrap": 0},
                "attempt_id": "cf30",
                "entry": {},
            }
            log_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

            desk = Desk(log_path=str(log_file))
            self.assertEqual(len(desk.connector.confirmations), 0)

    def test_review_typeerror_entry_not_appended_to_log(self):
        """Review point 4: If rebuilding Entry raises TypeError, row is not appended to self.log."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            bad_row = {
                "ref": "J-BAD",
                "work_order": "WO-2401",
                "kind": "issue",
                "status": "Posted",
                "code": "",
                "document": "STE-9999",
                "quantities": {},
                "attempt_id": "jbad",
                "entry": {
                    "name": "STE-9999",
                    "kind": "issue",
                    "ref": "J-BAD",
                    "work_order": "WO-2401",
                    "item": "ST-20",
                    "qty": 10,
                    "uom": "Kg",
                    "batch": "H-441",
                    "source": "RAW",
                    "target": "WIP",
                    "extra_unknown_field_that_raises_typeerror": 123,
                },
            }
            log_file.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")

            desk = Desk(log_path=str(log_file))
            self.assertEqual(len(desk.connector.log), 1)
            self.assertEqual(len(desk.connector.ledger.entries), 0)

    def test_review_reset_builds_connector_without_log_path_when_clearing_fails(self):
        """Review point 5: When clearing fails, Desk.reset builds Connector without log_path."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            log_file.write_text('{"ref": "J-100"}\n', encoding="utf-8")
            desk = Desk(log_path=str(log_file))

            with mock.patch("os.remove", side_effect=OSError("locked")), \
                 mock.patch("builtins.open", mock.mock_open()) as m_open:
                m_open.side_effect = OSError("read-only")
                desk.reset()
                self.assertIsNone(desk.connector.log_path)
                self.assertEqual(len(desk.connector.log), 0)
                self.assertEqual(
                    desk.attempts[0]["message"],
                    "Job reset. The message log could not be cleared. A restart will bring the old state back.",
                )

    def test_review_queued_and_release_logged_and_restored(self):
        """Review point 6: Switch A Queued rows and release rows are logged and restored."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))
            desk.switch_a = True
            desk.run("issue-again")
            desk.release()

            desk2 = Desk(log_path=str(log_file))
            self.assertEqual(len(desk2.attempts), 2)
            self.assertEqual(desk2.attempts[0]["id"], "issue-again")
            self.assertEqual(desk2.attempts[0]["status"], "Queued")
            self.assertEqual(desk2.attempts[1]["id"], "release")
            self.assertEqual(desk2.attempts[1]["status"], "Sent")

    def test_switch_a_duplicate_queued_rows_skipped(self):
        """Third review: Repeated reads with Switch A do not duplicate Queued rows in log."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir = root / "in"
            done_dir = root / "done"
            results_dir = root / "results"
            inbox_dir.mkdir(parents=True)
            log_file = root / "log.jsonl"

            f = inbox_dir / "01-issue.json"
            f.write_text(json.dumps(msg_issue()), encoding="utf-8")

            c = Connector(log_path=str(log_file))
            from connector.inbox import read_all
            read_all(c, switch_a=True, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
            self.assertEqual(len(c.log), 1)

            # Second read with Switch A
            read_all(c, switch_a=True, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
            self.assertEqual(len(c.log), 1)

            lines = [l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)

    def test_ste_number_restored_from_results_when_log_dropped(self):
        """Third review: When a stock entry's log write was dropped, STE number is restored from results."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir = root / "in"
            done_dir = root / "done"
            results_dir = root / "results"
            inbox_dir.mkdir(parents=True)
            log_file = root / "log.jsonl"

            f = inbox_dir / "01-issue-J-100-log-fails.json"
            f.write_text(json.dumps(msg_issue()), encoding="utf-8")

            c = Connector(log_path=str(log_file), results_dir=str(results_dir))
            from connector.inbox import read_all
            read_all(c, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)

            self.assertTrue(not log_file.exists() or len(log_file.read_text(encoding="utf-8").strip()) == 0)
            res = json.loads((results_dir / "01-issue-J-100-log-fails.json").read_text(encoding="utf-8"))
            self.assertEqual(res["document"], "STE-0001")

            c2 = Connector(log_path=str(log_file), results_dir=str(results_dir))
            self.assertEqual(c2.ledger._n, 1)

            m2 = msg_issue()
            m2["id"] = "J-200"
            res2 = c2.process(m2)
            self.assertEqual(res2.document, "STE-0002")

    def test_released_state_restored_from_log_on_desk_restart(self):
        """Third review: Desk.released starts as True if a release was recorded in the log."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))
            self.assertFalse(desk.released)
            desk.release()
            self.assertTrue(desk.released)

            desk2 = Desk(log_path=str(log_file))
            self.assertTrue(desk2.released)

    def test_typeerror_during_process_is_not_retried_in_inbox(self):
        """Third review: TypeError raised inside process is not caught and retried."""
        from connector.inbox import read_next
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir = root / "in"
            done_dir = root / "done"
            results_dir = root / "results"
            inbox_dir.mkdir(parents=True)

            f = inbox_dir / "bad-batch.json"
            f.write_text('{"kind": "receipt", "id": "R-1", "work_order": "WO-2401", "batch": ["list_is_bad"]}', encoding="utf-8")

            c = Connector()
            calls = []

            def spy_process(msg, drop_log=False, attempt_id=""):
                calls.append(msg)
                raise TypeError("dict key error simulation")

            c.process = spy_process
            res = read_next(c, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir, source_file=f)
            self.assertEqual(len(calls), 1)
            self.assertEqual(res["code"], "E-ITEM")

    def test_review_restart_snapshot_with_real_numbers(self):
        """Fourth review (Tip 2 & 3): Restart snapshot compares full state with real numbers."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))

            # 1. Issue 116 kg on WO-2401
            issue_res = desk.apply("issue", {
                "kind": "issue", "id": "J-1", "work_order": "WO-2401", "item": "ST-20",
                "qty": 116.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
            })
            self.assertEqual(issue_res["status"], "Posted")

            # 2. Confirm op 10
            confirm_res = desk.apply("confirm", {
                "kind": "confirm", "id": "CF-10", "work_order": "WO-2401", "operation": 10,
                "completed": 90.0, "rejected": 0.0, "rework": 0.0, "scrap": 0.0,
            })
            self.assertEqual(confirm_res["status"], "Posted")

            # 3. Return 16 kg against J-1
            return_res = desk.apply("return", {
                "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-1",
                "item": "ST-20", "qty": 16.0, "uom": "Kg", "batch": "H-441",
            })
            self.assertEqual(return_res["status"], "Posted")

            # Verify snapshot before restart
            self.assertEqual(desk.connector.kg()["raw"], 400.0)
            self.assertEqual(desk.connector.kg()["wip"], 100.0)
            self.assertEqual(desk.connector._kg_out("WO-2401"), 100.0)
            self.assertEqual(len(desk.connector.confirmations), 1)
            cancel_res = desk.apply("cancel", {"kind": "cancel", "id": "X-1", "work_order": "WO-2401"})
            self.assertEqual(cancel_res["code"], "E-QTY")  # Steel is still out

            # 4. Restart desk
            desk2 = Desk(log_path=str(log_file))

            # Compare snapshot after restart
            self.assertEqual(desk2.connector.kg()["raw"], 400.0)
            self.assertEqual(desk2.connector.kg()["wip"], 100.0)
            self.assertEqual(desk2.connector._kg_out("WO-2401"), 100.0)
            self.assertEqual(len(desk2.connector.confirmations), 1)
            self.assertEqual(desk2.connector.confirmations[0][0], "WO-2401")
            self.assertEqual(desk2.connector.confirmations[0][1], 10)

            # Duplicate issue is rejected
            dup_res = desk2.apply("issue", {
                "kind": "issue", "id": "J-1", "work_order": "WO-2401", "item": "ST-20",
                "qty": 116.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
            })
            self.assertEqual(dup_res["code"], "E-DUP")

            # Cancel is still refused with steel out
            cancel_res2 = desk2.apply("cancel", {"kind": "cancel", "id": "X-2", "work_order": "WO-2401"})
            self.assertEqual(cancel_res2["code"], "E-QTY")

    def test_desk_drop_log_persists_counter_and_not_reused_on_restart(self):
        """Fourth review comment 2: A desk log-fails posting still persists STE counter so it is not reused."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))

            # Desk posts STE-0001 with drop_log=True
            res = desk.apply("issue-drop", {
                "kind": "issue", "id": "J-1", "work_order": "WO-2401", "item": "ST-20",
                "qty": 50.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
            }, drop_log=True)
            self.assertEqual(res["document"], "STE-0001")
            # Log file is empty because drop_log was True
            self.assertTrue(not log_file.exists() or len(log_file.read_text(encoding="utf-8").strip()) == 0)

            # Restart desk
            desk2 = Desk(log_path=str(log_file))
            self.assertEqual(desk2.connector.ledger._n, 1)

            # Next issue gets STE-0002, not STE-0001
            res2 = desk2.apply("issue-2", {
                "kind": "issue", "id": "J-2", "work_order": "WO-2401", "item": "ST-20",
                "qty": 50.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
            })
            self.assertEqual(res2["document"], "STE-0002")

    def test_reset_clears_results_done_and_counter(self):
        """Fourth review comment 3: Desk reset clears results, done, counter, and resets sequence."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log_file = root / "log.jsonl"
            results_dir = root / "results"
            done_dir = root / "done"
            results_dir.mkdir(parents=True)
            done_dir.mkdir(parents=True)

            # Pre-populate old results
            (results_dir / "01-issue.json").write_text('{"document": "STE-0007"}', encoding="utf-8")
            (done_dir / "01-issue.json").write_text('{"id": "J-1"}', encoding="utf-8")

            desk = Desk(log_path=str(log_file))
            self.assertEqual(desk.connector.ledger._n, 7)

            # Perform reset
            desk.reset()
            self.assertEqual(desk.connector.ledger._n, 0)
            self.assertEqual(len(list(results_dir.glob("*.json"))), 0)
            self.assertEqual(len(list(done_dir.glob("*.json"))), 0)

            # Next issue starts fresh at STE-0001
            res = desk.apply("issue", {
                "kind": "issue", "id": "J-1", "work_order": "WO-2401", "item": "ST-20",
                "qty": 50.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
            })
            self.assertEqual(res["document"], "STE-0001")

    def test_atomic_validation_in_load_log_prevents_partial_state(self):
        """Fourth review comment 1: Bad entry data does not leave half-applied ledger/bin state."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            bad_row = {
                "ref": "J-BAD",
                "work_order": "WO-2401",
                "kind": "issue",
                "status": "Posted",
                "code": "",
                "document": "STE-0001",
                "quantities": {},
                "attempt_id": "jbad",
                "entry": {
                    "name": "STE-0001",
                    "kind": "issue",
                    "ref": "J-BAD",
                    "work_order": "WO-2401",
                    "item": "ST-20",
                    "qty": "not_a_valid_number",
                    "uom": "Kg",
                    "batch": "H-441",
                    "source": "RAW",
                    "target": "WIP",
                },
            }
            log_file.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")

            c = Connector(log_path=str(log_file))
            self.assertEqual(len(c.log), 1)
            self.assertEqual(len(c.ledger.entries), 0)
            self.assertEqual(c.kg()["raw"], 500.0)
            self.assertEqual(c.kg()["wip"], 0.0)

    def test_confirm_replay_guarded_when_no_metal_issued(self):
        """Fourth review comment 4: Confirm replay is guarded when _kg_out <= 0."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            confirm_row = {
                "ref": "CF-10",
                "work_order": "WO-2401",
                "kind": "confirm",
                "status": "Posted",
                "code": "",
                "document": "",
                "quantities": {"completed": 10.0, "rejected": 0.0, "rework": 0.0, "scrap": 0.0},
                "attempt_id": "cf10",
                "entry": {"operation": 10},
            }
            log_file.write_text(json.dumps(confirm_row) + "\n", encoding="utf-8")

            # Connector loads log where confirm has kg_out == 0
            c = Connector(log_path=str(log_file))
            self.assertEqual(len(c.confirmations), 0)

    def test_log_file_error_reported_immediately_on_release_and_switch_a(self):
        """Fourth review comment 5: log_file_error is checked and cleared immediately on record_attempt."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))

            # Simulate log write error
            with mock.patch("builtins.open", side_effect=OSError("disk full")):
                desk.switch_a = True
                res_queued = desk.apply("queued-attempt", {
                    "kind": "issue", "id": "J-1", "work_order": "WO-2401", "item": "ST-20",
                    "qty": 50.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
                })
                self.assertIn("The log file could not be written.", res_queued["message"])
                self.assertFalse(desk.connector.log_file_error)

                # Next action without error should NOT have the warning
                desk.switch_a = False

            with mock.patch("builtins.open", side_effect=OSError("disk full")):
                res_rel = desk.release()
                self.assertIn("The log file could not be written.", res_rel["message"])
                self.assertFalse(desk.connector.log_file_error)

    def test_switch_a_repeat_click_returns_already_queued_and_does_not_add_attempts(self):
        """Fourth review comment 6: Clicking an action multiple times under Switch-A returns already queued and does not add extra rows."""
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            desk = Desk(log_path=str(log_file))
            desk.switch_a = True

            msg = {
                "kind": "issue", "id": "J-1", "work_order": "WO-2401", "item": "ST-20",
                "qty": 50.0, "uom": "Kg", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP", "batch": "H-441",
            }
            res1 = desk.apply("issue", msg)
            self.assertEqual(res1["status"], "Queued")
            self.assertIn("Switch A is on. Logged only.", res1["message"])
            self.assertEqual(len(desk.attempts), 1)

            res2 = desk.apply("issue", msg)
            self.assertEqual(res2["status"], "Queued")
            self.assertEqual(res2["message"], "Switch A is on. Already queued.")
            self.assertEqual(len(desk.attempts), 1)


if __name__ == "__main__":
    unittest.main()



