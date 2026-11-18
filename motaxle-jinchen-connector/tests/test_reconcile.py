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
    def run_samples(self, root):
        inbox_dir = root / "in"
        done_dir = root / "done"
        results_dir = root / "results"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        for sample in sorted(SAMPLES.glob("*.json")):
            shutil.copy2(sample, inbox_dir / sample.name)
        connector = Connector()
        read_all(connector, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
        return connector, results_dir

    def run_examples(self, root):
        inbox_dir = root / "in"
        done_dir = root / "done"
        results_dir = root / "results"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        for example in sorted(EXAMPLES.glob("*.json")):
            shutil.copy2(example, inbox_dir / example.name)
        connector = Connector()
        read_all(connector, inbox_dir=inbox_dir, done_dir=done_dir, results_dir=results_dir)
        return connector, results_dir

    def test_nine_samples_reconcile_with_no_gaps(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self.run_samples(Path(temp))
            result = reconcile(connector, results_dir)
            self.assertEqual(result["stock_results"], 7)
            self.assertEqual(result["entries"], 7)
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["missing_result"], [])
            self.assertEqual(result["unreadable"], [])

    def test_missing_entry_when_ledger_entry_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self.run_samples(Path(temp))
            popped = connector.ledger.entries.pop(1)
            self.assertEqual(popped.name, "STE-0002")
            result = reconcile(connector, results_dir)
            self.assertEqual(result["missing_entry"], ["02-return-R-1.json"])
            self.assertEqual(result["missing_result"], [])

    def test_missing_result_when_result_file_is_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self.run_samples(Path(temp))
            target = results_dir / "09-receipt-C-2.json"
            target.unlink()
            result = reconcile(connector, results_dir)
            self.assertEqual(result["missing_result"], ["STE-0007"])

    def test_eight_examples_have_zero_stock_results_and_zero_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self.run_examples(Path(temp))
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
            connector, results_dir = self.run_samples(Path(temp))
            before_entries = len(connector.ledger.entries)
            before_files = sorted(p.name for p in results_dir.iterdir())
            reconcile(connector, results_dir)
            after_entries = len(connector.ledger.entries)
            after_files = sorted(p.name for p in results_dir.iterdir())
            self.assertEqual(before_entries, after_entries)
            self.assertEqual(before_files, after_files)

    def test_unreadable_result_file_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self.run_samples(Path(temp))
            bad_file = results_dir / "broken.json"
            bad_file.write_text("not json", encoding="utf-8")
            result = reconcile(connector, results_dir)
            self.assertIn("broken.json", result["unreadable"])

    def test_non_object_json_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self.run_samples(Path(temp))
            bad_file = results_dir / "array.json"
            bad_file.write_text("[1, 2, 3]", encoding="utf-8")
            result = reconcile(connector, results_dir)
            self.assertIn("array.json", result["unreadable"])


if __name__ == "__main__":
    unittest.main()
