import shutil
import tempfile
import unittest
from pathlib import Path

from connector.inbox import read_all, read_next
from connector.poc import Connector


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
            self.assertEqual(connector.kg()["raw"], 380)
            self.assertEqual(connector.log, [])
            self.assertEqual(len(connector.ledger.entries), 1)

            shutil.copy2(sample, inbox / sample.name)

            second = read_next(
                connector, inbox_dir=inbox,
                done_dir=done, results_dir=results
            )

            self.assertEqual(second["code"], "E-DUP")
            self.assertEqual(connector.kg()["raw"], 380)
            self.assertEqual(len(connector.ledger.entries), 1)

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
            self.assertEqual(connector.kg(), {"raw": 500, "wip": 0})

            # Queued stock file stays in inbox; confirmation is completed.
            self.assertTrue((inbox / issue_file).exists())
            self.assertTrue((done / confirm_file).exists())


if __name__ == "__main__":
    unittest.main()
