import json
import shutil
import tempfile
import unittest
from pathlib import Path

from connector.inbox import read_all
from connector.poc import Connector
from connector.reconcile import reconcile

PROJECT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT / "inbox" / "samples"
EXAMPLES = PROJECT / "inbox" / "examples"


class ReconcileTests(unittest.TestCase):
    def _run_inbox(self, source, root):
        inbox_dir = root / "in"
        done_dir = root / "done"
        results_dir = root / "results"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        for sample in sorted(source.glob("*.json")):
            shutil.copy2(sample, inbox_dir / sample.name)
        connector = Connector()
        read_all(connector, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
        return connector, results_dir

    def test_nine_samples_reconcile_with_no_gaps(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            result = reconcile(connector, results_dir)
            self.assertEqual(result["stock_results"], 7)
            self.assertEqual(result["entries"], 7)
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["missing_result"], [])
            self.assertEqual(result["unreadable"], [])

    def test_missing_entry_when_ledger_entry_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            popped = connector.ledger.entries.pop(1)
            self.assertEqual(popped.name, "STE-0002")
            result = reconcile(connector, results_dir)
            self.assertEqual(result["missing_entry"], ["02-return-R-1.json"])
            self.assertEqual(result["missing_result"], [])

    def test_missing_result_when_result_file_is_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            target = results_dir / "09-receipt-C-2.json"
            target.unlink()
            result = reconcile(connector, results_dir)
            self.assertEqual(result["missing_result"], ["STE-0007"])
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["stock_results"], 6)

    def test_eight_examples_have_zero_stock_results_and_zero_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(EXAMPLES, Path(temp))
            result = reconcile(connector, results_dir)
            self.assertEqual(result["stock_results"], 0)
            self.assertEqual(result["entries"], 0)
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["missing_result"], [])
            self.assertEqual(result["unreadable"], [])
            for res_file in sorted(results_dir.glob("*.json")):
                data = json.loads(res_file.read_text(encoding="utf-8"))
                self.assertEqual(data.get("status"), "Business Rejected")

    def test_reconcile_is_idempotent_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            before_contents = {
                p.name: p.read_text(encoding="utf-8")
                for p in sorted(results_dir.iterdir())
            }
            first = reconcile(connector, results_dir)
            second = reconcile(connector, results_dir)
            after_contents = {
                p.name: p.read_text(encoding="utf-8")
                for p in sorted(results_dir.iterdir())
            }
            self.assertEqual(first, second)
            self.assertEqual(before_contents, after_contents)

    def test_unreadable_result_file_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            bad_file = results_dir / "broken.json"
            bad_file.write_text("not json", encoding="utf-8")
            result = reconcile(connector, results_dir)
            self.assertIn("broken.json", result["unreadable"])

    def test_non_object_json_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            bad_file = results_dir / "array.json"
            bad_file.write_text("[1, 2, 3]", encoding="utf-8")
            result = reconcile(connector, results_dir)
            self.assertIn("array.json", result["unreadable"])


if __name__ == "__main__":
    unittest.main()
