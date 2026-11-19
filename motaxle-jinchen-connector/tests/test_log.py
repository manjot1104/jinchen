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
            self.assertEqual(len(desk.connector.log), 0)
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
                self.assertEqual(desk.attempts[0]["message"], "Job reset. The message log could not be cleared.")

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


if __name__ == "__main__":
    unittest.main()


