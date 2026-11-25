"""US-029: exception list, health view and replay."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from connector import exceptions as ex
from connector.poc import Connector
from server import Desk

PROJECT = Path(__file__).resolve().parent.parent
START = datetime(2026, 11, 25, 8, 0, 0)


def issue(ref="J-100", item="ST-20", qty=120):
    return {
        "kind": "issue", "id": ref, "work_order": "WO-2401", "item": item, "qty": qty,
        "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }


def order(kind, ref):
    return {"kind": kind, "id": ref, "work_order": "WO-2401"}


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, start=START):
        self.now = start

    def __call__(self):
        return self.now

    def add(self, **delta):
        self.now = self.now + timedelta(**delta)


def connector(clock=None, log_path=None):
    c = Connector(log_path=log_path)
    c.clock = clock or Clock()
    return c


def failed_on_hold(c):
    """WO-2401 on hold, then the issue is refused with E-CLOSED. Returns that row's number."""
    c.process(order("hold", "H-1"))
    result = c.process(issue())
    assert result.code == "E-CLOSED", result
    return len(c.log) - 1


class ExceptionList(unittest.TestCase):
    """AC 1: Business Rejected and Dead rows appear with code and age in hours; Posted rows do not."""

    def test_rejected_and_dead_rows_listed_with_code_and_age(self):
        clock = Clock()
        c = connector(clock)
        c.process(issue(item="ST-99"))  # E-ITEM at 08:00
        c.record_attempt("M-7", "WO-2401", "issue", "Dead", "E-TIMEOUT", "")
        clock.add(hours=3, minutes=30)
        listed = ex.exception_list(c)
        self.assertEqual([row["status"] for row in listed], ["Business Rejected", "Dead"])
        self.assertEqual([row["code"] for row in listed], ["E-ITEM", "E-TIMEOUT"])
        self.assertEqual([row["age_hours"] for row in listed], [3.5, 3.5])
        first = listed[0]
        self.assertEqual(first["work_order"], "WO-2401")
        self.assertEqual(first["message"], "issue J-100")
        self.assertEqual((first["item"], first["qty"], first["uom"]), ("ST-99", 120, "Kg"))

    def test_posted_rows_are_not_listed(self):
        c = connector()
        c.process(issue())
        c.process(order("hold", "H-1"))
        self.assertTrue(all(row.status == "Posted" for row in c.log))
        self.assertEqual(ex.exception_list(c), [])

    def test_queued_and_sent_rows_are_not_failures(self):
        c = connector()
        c.record_queued("J-100", issue())
        c.record_attempt("WO-2401", "WO-2401", "release", "Sent", "", "")
        self.assertEqual(ex.exception_list(c), [])

    def test_a_message_that_failed_twice_is_listed_once_with_its_attempts(self):
        c = connector()
        c.process(issue(item="ST-99"))
        c.process(issue(item="ST-99"))
        listed = ex.exception_list(c)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["row"], 1)
        self.assertEqual(listed[0]["attempts"], 2)

    def test_a_row_without_a_time_has_no_age(self):
        c = connector()
        c.process(issue(item="ST-99"))
        c.log[0].timestamp = ""
        self.assertIsNone(ex.exception_list(c)[0]["age_hours"])


class Replay(unittest.TestCase):

    def test_replay_while_the_cause_is_true_fails_with_the_same_code(self):
        """AC 2: stock does not move."""
        c = connector()
        row = failed_on_hold(c)
        before = (dict(c.kg()), len(c.ledger.entries))
        answer = ex.replay(c, row, "Support")
        self.assertTrue(answer["ok"])
        self.assertEqual((answer["status"], answer["code"]), ("Business Rejected", "E-CLOSED"))
        self.assertEqual((dict(c.kg()), len(c.ledger.entries)), before)

    def test_replay_after_the_fix_posts_once_and_stores_the_entry_name(self):
        """AC 3."""
        c = connector()
        row = failed_on_hold(c)
        c.process(order("resume", "RS-1"))
        answer = ex.replay(c, row, "Support")
        self.assertEqual((answer["status"], answer["code"]), ("Posted", ""))
        self.assertEqual(len(c.ledger.entries), 1)
        self.assertEqual(answer["document"], c.ledger.entries[0].name)
        self.assertEqual(c.log[-1].document, c.ledger.entries[0].name)
        # A second replay of the same failed row is a repeat: no second entry.
        again = ex.replay(c, row, "Support")
        self.assertEqual((again["status"], again["code"]), ("Posted", "E-DUP"))
        self.assertEqual(again["document"], answer["document"])
        self.assertEqual(len(c.ledger.entries), 1)
        # Once posted, the message leaves the exception list.
        self.assertEqual(ex.exception_list(c), [])

    def test_replay_of_a_posted_row_creates_no_document(self):
        """AC 4: and returns the original document."""
        c = connector()
        c.process(issue())
        original = c.log[0].document
        answer = ex.replay(c, 0, "Support")
        self.assertEqual((answer["status"], answer["code"], answer["document"]), ("Posted", "E-DUP", original))
        self.assertEqual(len(c.ledger.entries), 1)

    def test_replay_of_a_posted_hold_moves_nothing(self):
        c = connector()
        c.process(order("hold", "H-1"))
        answer = ex.replay(c, 0, "Support")
        self.assertEqual((answer["status"], answer["code"]), ("Posted", "E-DUP"))
        self.assertEqual(c.ledger.entries, [])

    def test_the_original_row_is_kept_and_the_replay_points_at_it(self):
        c = connector()
        row = failed_on_hold(c)
        kept = (c.log[row].status, c.log[row].code)
        ex.replay(c, row, "Support")
        self.assertEqual((c.log[row].status, c.log[row].code), kept)
        self.assertEqual(c.log[-1].replay_of, row)
        self.assertEqual(c.log[-1].attempt_id, "replay of row %d" % row)
        self.assertEqual(c.log[-1].attempts, 2)
        self.assertEqual(c.log[row].replay_of, -1)
        # The marker does not leak into the next ordinary attempt.
        c.process(issue(ref="J-101", item="ST-99"))
        self.assertEqual(c.log[-1].replay_of, -1)

    def test_a_row_with_no_message_cannot_be_replayed(self):
        c = connector()
        c.record_attempt("bad.json", "", "", "Business Rejected", "E-ITEM", "")
        answer = ex.replay(c, 0, "Support")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["message"], "Row 0 has no message to replay.")

    def test_unknown_row_is_refused(self):
        c = connector()
        for row in (5, -1, "x", None, True):
            self.assertFalse(ex.replay(c, row, "Support")["ok"], row)
        self.assertEqual(c.log, [])


class Roles(unittest.TestCase):
    """AC 5: a planner's replay is denied; Support is allowed."""

    def test_planner_cannot_replay(self):
        c = connector()
        row = failed_on_hold(c)
        c.process(order("resume", "RS-1"))
        rows_before = len(c.log)
        answer = ex.replay(c, row, "Planner")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["message"], "Only Support can open a payload or replay a row. Planner was refused.")
        self.assertEqual((len(c.log), len(c.ledger.entries)), (rows_before, 0))
        self.assertTrue(ex.replay(c, row, "Support")["ok"])
        self.assertEqual(len(c.ledger.entries), 1)

    def test_no_role_is_refused(self):
        c = connector()
        row = failed_on_hold(c)
        for role in ("", None, "stores"):
            self.assertFalse(ex.replay(c, row, role)["ok"], role)

    def test_only_support_opens_the_payload_and_secrets_stay_masked(self):
        c = connector()
        message = issue(item="ST-99")
        message["auth"] = {"api_token": "abc123"}
        c.process(message)
        self.assertFalse(ex.open_payload(c, 0, "Planner")["ok"])
        opened = ex.open_payload(c, 0, "support")
        self.assertTrue(opened["ok"])
        self.assertEqual(opened["payload"]["item"], "ST-99")
        self.assertEqual(opened["payload"]["auth"], {"api_token": "***"})
        self.assertNotIn("abc123", json.dumps(opened))


class Health(unittest.TestCase):
    """AC 6: the health view says 'failed', not blank, when the check fails, and creates nothing."""

    def test_health_shows_last_success_last_failure_and_dead_count(self):
        clock = Clock()
        c = connector(clock)
        c.process(issue())
        clock.add(hours=1)
        c.process(issue(ref="J-2", item="ST-99"))
        c.record_attempt("M-7", "WO-2401", "issue", "Dead", "E-TIMEOUT", "")
        view = ex.safe_health(c)
        self.assertEqual(view["status"], "ok")
        self.assertEqual(view["last_success"], "2026-11-25T08:00:00")
        self.assertEqual(view["last_failure"], "2026-11-25T09:00:00")
        self.assertEqual(view["dead"], 1)

    def test_empty_log_says_none_yet(self):
        view = ex.safe_health(connector())
        self.assertEqual((view["last_success"], view["last_failure"], view["dead"]), ("none yet", "none yet", 0))

    def test_a_failed_check_says_failed_and_creates_nothing(self):
        c = connector()
        c.process(issue())
        entries = list(c.ledger.entries)
        c.log = None  # the log cannot be read
        view = ex.safe_health(c)
        for name in ("status", "last_success", "last_failure", "dead"):
            self.assertEqual(view[name], "failed", name)
        self.assertTrue(view["message"].startswith("The health check failed:"))
        self.assertEqual(c.ledger.entries, entries)
        self.assertEqual(c.order_state, {})


class Persistence(unittest.TestCase):

    def test_time_and_replay_mark_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log.jsonl"
            c = connector(log_path=str(log))
            row = failed_on_hold(c)
            c.process(order("resume", "RS-1"))
            ex.replay(c, row, "Support")
            again = Connector(log_path=str(log))
            self.assertEqual(again.log[row].timestamp, "2026-11-25T08:00:00")
            self.assertEqual(again.log[-1].replay_of, row)
            self.assertEqual(ex.exception_list(again), [])

    def test_an_old_row_without_time_or_mark_still_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log.jsonl"
            old = {"ref": "J-100", "work_order": "WO-2401", "kind": "issue", "status": "Business Rejected",
                   "code": "E-ITEM", "document": "", "repeat_key": "J-100|WO-2401|issue"}
            log.write_text(json.dumps(old) + "\n", encoding="utf-8")
            c = Connector(log_path=str(log))
            self.assertEqual((c.log[0].timestamp, c.log[0].replay_of), ("", -1))
            self.assertIsNone(ex.exception_list(c)[0]["age_hours"])

    def test_a_bad_replay_mark_loads_as_none(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log.jsonl"
            row = {"ref": "J-100", "work_order": "WO-2401", "kind": "issue", "status": "Posted",
                   "code": "", "document": "STE-0001", "replay_of": "zero", "timestamp": 5}
            log.write_text(json.dumps(row) + "\n", encoding="utf-8")
            c = Connector(log_path=str(log))
            self.assertEqual((c.log[0].replay_of, c.log[0].timestamp), (-1, ""))


class OnTheDesk(unittest.TestCase):

    def test_desk_lists_exceptions_and_health(self):
        desk = Desk()
        desk.connector.clock = Clock()
        desk.apply("bad", issue(item="ST-99"))
        state = desk.payload()
        self.assertEqual([row["code"] for row in state["exceptions"]], ["E-ITEM"])
        self.assertEqual(state["health"]["status"], "ok")

    def test_desk_refuses_a_planner_and_replays_for_support(self):
        desk = Desk()
        desk.apply("hold", order("hold", "H-1"))
        desk.apply("J-100", issue())
        desk.apply("resume", order("resume", "RS-1"))
        row = ex.exception_list(desk.connector)[0]["row"]
        refused = desk.replay(row, "Planner")
        self.assertEqual(refused["status"], "Refused")
        self.assertEqual(desk.connector.ledger.entries, [])
        done = desk.replay(row, "Support")
        self.assertEqual((done["status"], done["code"]), ("Posted", ""))
        self.assertEqual(desk.attempts[-1]["id"], "replay of row %d" % row)
        self.assertEqual(desk.payload()["exceptions"], [])

    def test_page_has_the_exceptions_screen(self):
        import server
        for text in ('"exceptions"', "/api/replay", "/api/payload", "replay-role", "function esc("):
            self.assertIn(text, server.PAGE, text)


class Doc(unittest.TestCase):

    def test_doc_lists_the_columns_roles_and_health(self):
        text = (PROJECT / "docs" / "exceptions.md").read_text(encoding="utf-8")
        for column in ("Work order", "Message", "Item and qty", "Code", "Age (h)", "Status"):
            self.assertIn(column, text)
        for phrase in ("Support", "Planner", "Last success", "Last failure", "Dead", "failed"):
            self.assertIn(phrase, text)
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/exceptions.md", readme)


if __name__ == "__main__":
    unittest.main()
