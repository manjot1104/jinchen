"""The message log keeps twelve fields, seven statuses, no secrets, and every attempt (US-025)."""
import json
import re
import shutil
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from connector.inbox import read_all
from connector.poc import LOG_STATUSES, Connector
from server import ACTIONS, Desk

PROJECT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT / "inbox" / "samples"
TWELVE = ("kind", "direction", "repeat_key", "work_order", "ref", "received", "posted",
          "status", "code", "document", "attempts", "payload")


def issue(ref="J-100", qty=120, **extra):
    message = dict(ACTIONS["issue-again"]["message"])
    message.update(id=ref, qty=qty, **extra)
    return message


def run_samples(connector, root):
    inbox = root / "in"
    inbox.mkdir()
    for path in SAMPLES.glob("*.json"):
        shutil.copy2(path, inbox / path.name)
    config = root / "config.json"
    config.write_text(json.dumps({"mode": "test", "live_password": ""}))
    return read_all(connector, inbox_dir=inbox, done_dir=root / "done",
                    results_dir=root / "results", config_path=config)


class TwelveFields(unittest.TestCase):
    def test_every_row_has_all_twelve_fields(self):
        connector = Connector()
        with tempfile.TemporaryDirectory() as temp:
            run_samples(connector, Path(temp))
        # Sample 01 is the log-fails case: it posts but drops its own log row.
        self.assertEqual(len(connector.log), 8)
        for row in connector.log:
            data = asdict(row)
            for name in TWELVE:
                self.assertIn(name, data)
            self.assertIn(row.direction, ("Jinchen to ERPNext", "ERPNext to Jinchen"))
            self.assertTrue(row.repeat_key.startswith(row.ref + "|" + row.work_order + "|"), row)
            self.assertEqual(row.payload["kind"], row.kind)

    def test_received_is_as_sent_and_posted_is_as_booked(self):
        connector = Connector()
        connector.process(issue(qty="120"))
        row = connector.log[-1]
        self.assertEqual(row.received, {"item": "ST-20", "qty": "120", "uom": "Kg", "batch": "H-441"})
        self.assertEqual(row.posted, {"item": "ST-20", "qty": 120.0, "uom": "Kg", "batch": "H-441"})
        self.assertEqual((row.status, row.document), ("Posted", "STE-0001"))

    def test_a_refused_row_has_nothing_posted(self):
        connector = Connector()
        connector.process(issue(batch="H441"))
        row = connector.log[-1]
        self.assertEqual((row.status, row.code), ("Business Rejected", "E-BATCH"))
        self.assertEqual(row.received["batch"], "H441")
        self.assertEqual(row.posted, {})

    def test_a_confirmation_keys_on_its_operation(self):
        connector = Connector()
        connector.process(issue())
        connector.process({"kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
                           "operation": 10, "completed": 100})
        row = connector.log[-1]
        self.assertEqual(row.repeat_key, "CF-10|WO-2401|op10")
        self.assertEqual(row.posted["completed"], 100.0)

    def test_hold_goes_from_erpnext_to_jinchen(self):
        connector = Connector()
        connector.process({"kind": "hold", "id": "H-1", "work_order": "WO-2401"})
        self.assertEqual(connector.log[-1].direction, "ERPNext to Jinchen")


class SevenStatuses(unittest.TestCase):
    def test_a_whole_desk_session_logs_only_the_seven(self):
        desk = Desk()
        for action in ("issue", "issue-again", "return", "confirm-10", "confirm-20"):
            desk.run(action)
        desk.switch_b = True
        desk.release()
        desk.switch_b = False
        desk.release()
        desk.switch_a = True
        desk.run("issue-again")
        statuses = {row.status for row in desk.connector.log}
        self.assertTrue(statuses <= set(LOG_STATUSES), statuses)
        self.assertIn("Queued", statuses)
        self.assertIn("Sent", statuses)

    def test_an_unknown_status_is_refused(self):
        connector = Connector()
        with self.assertRaises(ValueError):
            connector.record_attempt("X", "WO-2401", "issue", "Stopped", "", "")
        self.assertEqual(connector.log, [])

    def test_switch_b_is_logged_as_business_rejected(self):
        desk = Desk()
        desk.switch_b = True
        self.assertEqual(desk.release()["status"], "Stopped")
        self.assertEqual(desk.connector.log[-1].status, "Business Rejected")


class NoSecrets(unittest.TestCase):
    SECRETS = ("test-pass-4711", "tok-0815", "key-9999", "shh-1234")

    def message(self):
        return issue(password=self.SECRETS[0], token=self.SECRETS[1],
                     auth={"api_key": self.SECRETS[2], "client_secret": self.SECRETS[3]})

    def test_secrets_are_stored_zero_times(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            connector = Connector(log_path=str(log_file))
            connector.process(self.message())
            text = log_file.read_text(encoding="utf-8")
        for secret in self.SECRETS:
            self.assertEqual(text.count(secret), 0)
            self.assertEqual(json.dumps([asdict(row) for row in connector.log]).count(secret), 0)
        row = connector.log[-1]
        self.assertEqual(row.payload["password"], "***")
        self.assertEqual(row.payload["auth"], {"api_key": "***", "client_secret": "***"})
        self.assertEqual(row.status, "Posted")

    def test_a_queued_message_is_masked_too(self):
        connector = Connector()
        connector.record_queued("01.json", self.message())
        text = json.dumps(asdict(connector.log[-1]))
        for secret in self.SECRETS:
            self.assertEqual(text.count(secret), 0)

    def test_the_callers_message_is_not_changed(self):
        message = self.message()
        Connector().process(message)
        self.assertEqual(message["password"], self.SECRETS[0])


class Attempts(unittest.TestCase):
    def test_each_retry_adds_a_row_and_counts_up(self):
        connector = Connector()
        for _ in range(3):
            connector.process(issue())
        rows = [row for row in connector.log if row.repeat_key == "J-100|WO-2401|issue"]
        self.assertEqual([row.attempts for row in rows], [1, 2, 3])
        self.assertEqual([row.code for row in rows], ["", "E-DUP", "E-DUP"])

    def test_the_count_carries_over_a_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = str(Path(temp) / "log.jsonl")
            first = Connector(log_path=log_file)
            first.process(issue())
            first.process(issue())
            second = Connector(log_path=log_file)
            self.assertEqual([row.attempts for row in second.log], [1, 2])
            second.process(issue())
            self.assertEqual(second.log[-1].attempts, 3)
            self.assertEqual(len(second.log), 3)

    def test_a_row_from_before_these_fields_still_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            log_file = Path(temp) / "log.jsonl"
            log_file.write_text(json.dumps({
                "ref": "H-1", "work_order": "WO-2401", "kind": "hold", "status": "Posted",
                "code": "", "document": "", "quantities": {}, "attempt_id": "", "entry": {},
            }) + "\n")
            connector = Connector(log_path=str(log_file))
        row = connector.log[0]
        self.assertEqual((row.attempts, row.received, row.payload), (1, {}, {}))
        self.assertEqual(connector.order_state.get("WO-2401"), "on hold")


class Doc(unittest.TestCase):
    def test_the_doc_lists_the_twelve_fields_and_seven_statuses(self):
        text = (PROJECT / "docs" / "message-log.md").read_text(encoding="utf-8")
        numbers = re.findall(r"^\| (\d+) \|", text, re.M)
        self.assertEqual(numbers, [str(n) for n in range(1, 13)])
        for status in LOG_STATUSES:
            self.assertIn(status, text)


if __name__ == "__main__":
    unittest.main()
