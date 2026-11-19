import json
import shutil
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from connector.inbox import copy_samples, list_inbox, read_all, read_next
from connector.poc import Connector

PROJECT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT / "inbox" / "samples"
ISSUE_FILE = "01-issue-J-100-log-fails.json"


def temp_inbox(root):
    """inbox/in, done, results under a temporary root; only in/ is created."""
    inbox = root / "in"
    inbox.mkdir()
    return inbox, root / "done", root / "results"


class InboxTests(unittest.TestCase):
    def test_log_failure_replay_does_not_duplicate_stock(self):
        project = Path(__file__).resolve().parent.parent
        sample = project / "inbox" / "samples" / "01-issue-J-100-log-fails.json"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "in"
            done = root / "done"
            results = root / "results"
            inbox.mkdir()

            shutil.copy2(sample, inbox / sample.name)
            connector = Connector()

            first = read_next(
                connector, inbox_dir=inbox,
                done_dir=done, results_dir=results
            )

            self.assertEqual(first["status"], "Posted")
            self.assertEqual(first["document"], "STE-0001")
            self.assertEqual(connector.kg()["raw"], 380)
            self.assertEqual(connector.log, [])
            self.assertEqual(len(connector.ledger.entries), 1)

            shutil.copy2(sample, inbox / sample.name)

            second = read_next(
                connector, inbox_dir=inbox,
                done_dir=done, results_dir=results
            )

            self.assertEqual(second["code"], "E-DUP")
            self.assertEqual(second["document"], "STE-0001")
            self.assertEqual(connector.kg()["raw"], 380)
            self.assertEqual(connector.kg()["wip"], 120)
            self.assertEqual(len(connector.ledger.entries), 1)
            self.assertTrue((done / sample.name).exists())

    def test_switch_a_queues_stock_but_processes_confirmation(self):
        project = Path(__file__).resolve().parent.parent
        samples = project / "inbox" / "samples"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "in"
            done = root / "done"
            results = root / "results"
            inbox.mkdir()

            issue_file = "01-issue-J-100-log-fails.json"
            confirm_file = "03-confirm-op10.json"

            shutil.copy2(samples / issue_file, inbox / issue_file)
            shutil.copy2(samples / confirm_file, inbox / confirm_file)

            connector = Connector()
            # A confirmation needs metal on the order (BR-36), so J-099 is
            # posted directly. The queued J-100 file must still post nothing.
            connector.process(dict(json.loads((samples / issue_file).read_text()), id="J-099"))

            outcomes = read_all(
                connector,
                switch_a=True,
                inbox_dir=inbox,
                done_dir=done,
                results_dir=results,
            )

            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0]["status"], "Queued")
            self.assertEqual(outcomes[1]["status"], "Posted")

            # Switch A must prevent stock posting, not confirmation processing.
            self.assertEqual(connector.kg(), {"raw": 380, "wip": 120})
            self.assertEqual(len(connector.ledger.entries), 1)

            # Queued stock file stays in inbox; confirmation is completed.
            self.assertTrue((inbox / issue_file).exists())
            self.assertTrue((done / confirm_file).exists())

    def test_list_inbox_reads_01_before_02_whatever_the_write_order(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, _, _ = temp_inbox(Path(temp))
            # write 02 first so that modified time would put it first
            shutil.copy2(SAMPLES / "02-return-R-1.json", inbox / "02-return-R-1.json")
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            (inbox / "notes.txt").write_text("not a message")

            names = [path.name for path in list_inbox(inbox)]

        self.assertEqual(names, [ISSUE_FILE, "02-return-R-1.json"])

    def test_non_object_file_is_e_item_without_calling_process(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            (inbox / "01-list.json").write_text('["not", "an", "object"]')
            connector = Connector()

            with mock.patch.object(connector, "process", side_effect=AssertionError("process was called")) as process:
                result = read_next(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(process.call_count, 0)
            self.assertEqual(result["status"], "Rejected")
            self.assertEqual(result["code"], "E-ITEM")
            self.assertFalse((inbox / "01-list.json").exists())
            self.assertTrue((done / "01-list.json").exists())
            written = json.loads((results / "01-list.json").read_text())
            self.assertEqual(written["code"], "E-ITEM")
            self.assertEqual(connector.kg(), {"raw": 500, "wip": 0})
            self.assertEqual(len(connector.ledger.entries), 0)

    def test_broken_json_file_is_e_item_without_calling_process(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            (inbox / "01-broken.json").write_text('{"kind": "issue", "id": "J-100",}')
            connector = Connector()

            with mock.patch.object(connector, "process", side_effect=AssertionError("process was called")) as process:
                result = read_next(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(process.call_count, 0)
            self.assertEqual(result["code"], "E-ITEM")
            self.assertTrue((done / "01-broken.json").exists())
            self.assertEqual(connector.kg()["raw"], 500)

    def test_copy_samples_refuses_when_a_json_file_is_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, _, _ = temp_inbox(Path(temp))
            waiting = inbox / "01-issue-J-100-log-fails.json"
            waiting.write_text('{"kind": "issue", "id": "J-WAIT", "work_order": "WO-2401"}')
            before = waiting.read_text()

            outcome = copy_samples(inbox_dir=inbox, samples_dir=SAMPLES)

            self.assertEqual(outcome["copied"], 0)
            self.assertEqual(outcome["files"], [])
            self.assertIn("not empty", outcome["message"])
            self.assertEqual([p.name for p in list_inbox(inbox)], [waiting.name])
            self.assertEqual(waiting.read_text(), before)

    def test_copy_samples_fills_an_empty_inbox_once(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, _, _ = temp_inbox(Path(temp))

            first = copy_samples(inbox_dir=inbox, samples_dir=SAMPLES)
            second = copy_samples(inbox_dir=inbox, samples_dir=SAMPLES)

            self.assertEqual(first["copied"], 9)
            self.assertEqual(second["copied"], 0)
            self.assertEqual(len(list_inbox(inbox)), 9)

    def test_switch_a_holds_the_issue_until_the_next_read_500_500_380(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()
            raw = []

            # A5.1: Switch A on, read: queued, file stays, raw 500
            queued = read_all(connector, switch_a=True, inbox_dir=inbox, done_dir=done, results_dir=results)
            raw.append(connector.kg()["raw"])
            self.assertEqual(queued[0]["status"], "Queued")
            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertEqual(len(connector.ledger.entries), 0)

            # A5.2: Switch A off, no read: nothing moves by itself
            switch_a = False
            raw.append(connector.kg()["raw"])
            self.assertTrue((inbox / ISSUE_FILE).exists())

            # A5.3: one read with the switch off posts once
            posted = read_all(connector, switch_a=switch_a, inbox_dir=inbox, done_dir=done, results_dir=results)
            raw.append(connector.kg()["raw"])

            self.assertEqual(raw, [500, 500, 380])
            self.assertEqual(posted[0]["status"], "Posted")
            self.assertEqual(posted[0]["document"], "STE-0001")
            self.assertEqual(connector.kg()["wip"], 120)
            self.assertFalse((inbox / ISSUE_FILE).exists())
            self.assertTrue((done / ISSUE_FILE).exists())
            self.assertEqual(len(connector.ledger.entries), 1)

    def test_sample_in_inbox_samples_is_untouched_by_a_read(self):
        before = (SAMPLES / ISSUE_FILE).read_text()
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            read_all(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)
        self.assertEqual((SAMPLES / ISSUE_FILE).read_text(), before)
        self.assertEqual(len(list(SAMPLES.glob("*.json"))), 9)

    def test_read_all_continues_past_a_bad_value_file(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            (inbox / "02-bad-qty.json").write_text(
                '{"kind": "issue", "id": "J-TXT", "work_order": "WO-2401", "qty": "abc"}')
            shutil.copy2(SAMPLES / "03-confirm-op10.json", inbox / "03-confirm-op10.json")
            connector = Connector()

            outcomes = read_all(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(len(outcomes), 3)
            self.assertEqual(outcomes[0]["status"], "Posted")
            self.assertEqual(outcomes[1]["code"], "E-QTY")
            self.assertEqual(outcomes[2]["status"], "Posted")
            self.assertEqual(connector.kg()["raw"], 380)
            self.assertEqual(len(connector.confirmations), 1)
            self.assertEqual(sorted(p.name for p in done.iterdir()),
                             [ISSUE_FILE, "02-bad-qty.json", "03-confirm-op10.json"])
            self.assertEqual(len(list(inbox.glob("*.json"))), 0)

    def test_read_all_survives_a_process_that_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            (inbox / "01-boom.json").write_text('{"kind": "issue", "id": "J-BOOM", "work_order": "WO-2401"}')
            shutil.copy2(SAMPLES / "03-confirm-op10.json", inbox / "03-confirm-op10.json")
            connector = Connector()
            connector.process(json.loads((SAMPLES / ISSUE_FILE).read_text()))
            real = connector.process

            def explode_once(message, drop_log=False):
                if message.get("id") == "J-BOOM":
                    raise RuntimeError("unexpected")
                return real(message, drop_log=drop_log)

            with mock.patch.object(connector, "process", side_effect=explode_once):
                outcomes = read_all(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(outcomes[0]["code"], "E-ITEM")
            self.assertEqual(outcomes[0]["message"], "File could not be processed.")
            self.assertEqual(outcomes[1]["status"], "Posted")
            self.assertTrue((done / "01-boom.json").exists())
            self.assertEqual(json.loads((results / "01-boom.json").read_text())["code"], "E-ITEM")

    def test_destination_collision_preserves_existing_done_and_result_files(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            done.mkdir()
            results.mkdir()
            (done / ISSUE_FILE).write_text("original-done", encoding="utf-8")
            (results / ISSUE_FILE).write_text("original-result", encoding="utf-8")
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()

            outcome = read_next(
                connector, inbox_dir=inbox, done_dir=done, results_dir=results
            )

            self.assertEqual(outcome["status"], "Posted")
            self.assertEqual(outcome["document"], "STE-0001")
            self.assertFalse((inbox / ISSUE_FILE).exists())
            self.assertEqual((done / ISSUE_FILE).read_text(encoding="utf-8"), "original-done")
            self.assertEqual((results / ISSUE_FILE).read_text(encoding="utf-8"), "original-result")
            self.assertTrue((done / "01-issue-J-100-log-fails-1.json").exists())
            collided_result = json.loads(
                (results / "01-issue-J-100-log-fails-1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(collided_result["status"], "Posted")
            self.assertEqual(collided_result["document"], "STE-0001")

    def test_non_collision_preserves_original_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)

            outcome = read_next(
                Connector(), inbox_dir=inbox, done_dir=done, results_dir=results
            )

            self.assertEqual(outcome["status"], "Posted")
            self.assertTrue((done / ISSUE_FILE).exists())
            self.assertTrue((results / ISSUE_FILE).exists())
            self.assertFalse((done / "01-issue-J-100-log-fails-1.json").exists())


if __name__ == "__main__":
    unittest.main()
