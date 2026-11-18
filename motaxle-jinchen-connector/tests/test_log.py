import json
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
            with mock.patch("server.LOG_PATH", str(log_file)):
                desk = Desk()
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


if __name__ == "__main__":
    unittest.main()
