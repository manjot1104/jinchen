import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        for src_file in sorted(source.glob("*.json")):
            shutil.copy2(src_file, inbox_dir / src_file.name)
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
            self.assertEqual(result["entries"], 6)
            self.assertEqual(result["stock_results"], 7)

    def test_missing_result_when_result_file_is_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            target = results_dir / "09-receipt-C-2.json"
            target.unlink()
            result = reconcile(connector, results_dir)
            self.assertEqual(result["missing_result"], ["STE-0007"])
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["stock_results"], 6)
            self.assertEqual(result["entries"], 7)

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
                if p.is_file()
            }
            before_entry_count = len(connector.ledger.entries)
            before_entry_names = [e.name for e in connector.ledger.entries]

            first = reconcile(connector, results_dir)
            second = reconcile(connector, results_dir)

            after_contents = {
                p.name: p.read_text(encoding="utf-8")
                for p in sorted(results_dir.iterdir())
                if p.is_file()
            }
            after_entry_count = len(connector.ledger.entries)
            after_entry_names = [e.name for e in connector.ledger.entries]

            self.assertEqual(first, second)
            self.assertEqual(first["entries"], before_entry_count)
            self.assertEqual(after_entry_count, before_entry_count)
            self.assertEqual(after_entry_names, before_entry_names)
            self.assertEqual(before_contents, after_contents)

    def test_unreadable_result_file_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            bad_file = results_dir / "broken.json"
            bad_file.write_text("not json", encoding="utf-8")
            result = reconcile(connector, results_dir)
            self.assertEqual(result["unreadable"], ["broken.json"])
            self.assertEqual(result["stock_results"], 7)
            self.assertEqual(result["entries"], 7)
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["missing_result"], [])

    def test_non_object_json_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            bad_file = results_dir / "array.json"
            bad_file.write_text("[1, 2, 3]", encoding="utf-8")
            result = reconcile(connector, results_dir)
            self.assertEqual(result["unreadable"], ["array.json"])
            self.assertEqual(result["stock_results"], 7)
            self.assertEqual(result["entries"], 7)
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(result["missing_result"], [])

    def test_oserror_reading_result_file_listed_under_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            connector, results_dir = self._run_inbox(SAMPLES, Path(temp))
            locked_file = results_dir / "locked.json"
            locked_file.write_text('{"status": "Posted"}', encoding="utf-8")

            real_read_text = Path.read_text

            def fake_read_text(self_path, *args, **kwargs):
                if self_path.name == "locked.json":
                    raise PermissionError(f"Permission denied: {self_path}")
                return real_read_text(self_path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", autospec=True, side_effect=fake_read_text):
                result = reconcile(connector, results_dir)
                self.assertIn("locked.json", result["unreadable"])
                self.assertEqual(result["stock_results"], 7)
                self.assertEqual(result["entries"], 7)
                self.assertEqual(result["missing_entry"], [])
                self.assertEqual(result["missing_result"], [])

    def test_empty_results_dir_reports_all_entries_as_missing_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            connector, _ = self._run_inbox(SAMPLES, root)
            empty_results = root / "empty_results"
            empty_results.mkdir()
            result = reconcile(connector, empty_results)
            self.assertEqual(result["stock_results"], 0)
            self.assertEqual(result["entries"], 7)
            self.assertEqual(result["missing_entry"], [])
            self.assertEqual(
                result["missing_result"],
                ["STE-0001", "STE-0002", "STE-0003", "STE-0004", "STE-0005", "STE-0006", "STE-0007"],
            )
            self.assertEqual(result["unreadable"], [])


if __name__ == "__main__":
    unittest.main()
