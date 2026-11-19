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


if __name__ == "__main__":
    unittest.main()

