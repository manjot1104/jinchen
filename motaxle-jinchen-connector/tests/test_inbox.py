import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from unittest import mock

import connector.inbox as inbox_mod
from connector.inbox import copy_samples, list_inbox, read_all, read_next
from connector.poc import ConfigError, Connector
from connector.reconcile import reconcile

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

    def test_destination_collision_preserves_done_and_updates_result(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            done.mkdir()
            results.mkdir()
            (done / ISSUE_FILE).write_text("original-done", encoding="utf-8")
            (results / ISSUE_FILE).write_text('{"status": "Queued"}', encoding="utf-8")
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()

            outcome = read_next(
                connector, inbox_dir=inbox, done_dir=done, results_dir=results
            )

            self.assertEqual(outcome["status"], "Posted")
            self.assertEqual(outcome["document"], "STE-0001")
            self.assertFalse((inbox / ISSUE_FILE).exists())
            self.assertEqual((done / ISSUE_FILE).read_text(encoding="utf-8"), "original-done")
            self.assertTrue((done / "01-issue-J-100-log-fails.dup1.json").exists())
            self.assertFalse((results / "01-issue-J-100-log-fails.dup1.json").exists())
            updated_result = json.loads((results / ISSUE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(updated_result["status"], "Posted")
            self.assertEqual(updated_result["document"], "STE-0001")

            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            second = read_next(
                connector, inbox_dir=inbox, done_dir=done, results_dir=results
            )
            self.assertEqual(second["code"], "E-DUP")
            self.assertTrue((done / "01-issue-J-100-log-fails.dup2.json").exists())
            self.assertFalse((results / "01-issue-J-100-log-fails.dup2.json").exists())
            self.assertEqual(
                json.loads((results / ISSUE_FILE).read_text(encoding="utf-8"))["code"],
                "E-DUP",
            )

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
            self.assertFalse((done / "01-issue-J-100-log-fails.dup1.json").exists())

    def test_read_all_with_filled_password_in_config_refuses_before_moving(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()

            with self.assertRaises(ConfigError):
                read_all(
                    connector,
                    inbox_dir=inbox,
                    done_dir=done,
                    results_dir=results,
                    config={"mode": "test", "live_password": "secret"},
                )

            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertFalse(done.exists())
            self.assertFalse(results.exists())

    def test_read_all_with_bad_mode_in_config_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()

            with self.assertRaises(ConfigError) as caught:
                read_all(
                    connector,
                    inbox_dir=inbox,
                    done_dir=done,
                    results_dir=results,
                    config={"mode": "demo", "live_password": ""},
                )

            self.assertEqual(str(caught.exception), "Mode must be test or live.")

    def test_read_all_with_empty_config_dict_refuses_before_moving(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)

            with self.assertRaises(ConfigError) as caught:
                read_all(
                    Connector(),
                    inbox_dir=inbox,
                    done_dir=done,
                    results_dir=results,
                    config={},
                )

            self.assertEqual(str(caught.exception), "Mode must be test or live.")
            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertFalse(done.exists())
            self.assertFalse(results.exists())

    def test_replay_nine_samples_and_c1_reconciles_without_double_count(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            for sample in sorted(SAMPLES.glob("*.json")):
                shutil.copy2(sample, inbox / sample.name)
            connector = Connector()

            read_all(connector, inbox_dir=inbox, done_dir=done, results_dir=results)
            shutil.copy2(done / "08-receipt-C-1.json", inbox / "08-receipt-C-1.json")
            replay = read_all(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(len(replay), 1)
            self.assertEqual(replay[0]["code"], "E-DUP")
            self.assertEqual(replay[0]["document"], "STE-0006")
            self.assertEqual(len(list(results.glob("*.json"))), 9)
            self.assertTrue((done / "08-receipt-C-1.json").exists())
            self.assertTrue((done / "08-receipt-C-1.dup1.json").exists())
            self.assertEqual(
                reconcile(connector, results),
                {
                    "stock_results": 7,
                    "entries": 7,
                    "missing_entry": [],
                    "missing_result": [],
                    "unreadable": [],
                },
            )

    def test_repeated_queued_reads_do_not_accumulate_result_files(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()

            for _ in range(3):
                out = read_all(
                    connector, switch_a=True, inbox_dir=inbox, done_dir=done, results_dir=results
                )
                self.assertEqual(out[0]["status"], "Queued")
                self.assertEqual(len(list(results.glob("*.json"))), 1)

            posted = read_all(
                connector, switch_a=False, inbox_dir=inbox, done_dir=done, results_dir=results
            )
            self.assertEqual(posted[0]["status"], "Posted")
            self.assertEqual(len(list(results.glob("*.json"))), 1)
            self.assertEqual(
                json.loads((results / ISSUE_FILE).read_text(encoding="utf-8"))["status"],
                "Posted",
            )

    def test_done_collision_uses_unambiguous_dup_suffix_for_ids_ending_in_dash_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            done.mkdir()
            (done / "02-return-R-1.json").write_text("earlier-r1", encoding="utf-8")
            (done / "09-receipt-C-2.json").write_text("earlier-c2", encoding="utf-8")
            shutil.copy2(SAMPLES / "02-return-R-1.json", inbox / "02-return-R-1.json")
            shutil.copy2(SAMPLES / "09-receipt-C-2.json", inbox / "09-receipt-C-2.json")

            read_all(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual((done / "02-return-R-1.json").read_text(encoding="utf-8"), "earlier-r1")
            self.assertEqual((done / "09-receipt-C-2.json").read_text(encoding="utf-8"), "earlier-c2")
            self.assertTrue((done / "02-return-R-1.dup1.json").exists())
            self.assertTrue((done / "09-receipt-C-2.dup1.json").exists())
            self.assertFalse((done / "02-return-R-2.json").exists())
            self.assertFalse((done / "09-receipt-C-2-1.json").exists())

    def test_concurrent_done_collisions_preserve_both_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            in1, in2 = root / "in1", root / "in2"
            done, results = root / "done", root / "results"
            in1.mkdir()
            in2.mkdir()
            shutil.copy2(SAMPLES / ISSUE_FILE, in1 / ISSUE_FILE)
            shutil.copy2(SAMPLES / ISSUE_FILE, in2 / ISSUE_FILE)
            errors = []

            def worker(src):
                try:
                    read_next(Connector(), inbox_dir=src, done_dir=done, results_dir=results)
                except Exception as exc:
                    errors.append(exc)

            t1 = threading.Thread(target=worker, args=(in1,))
            t2 = threading.Thread(target=worker, args=(in2,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            self.assertEqual(errors, [])
            self.assertEqual(
                sorted(p.name for p in done.iterdir()),
                ["01-issue-J-100-log-fails.dup1.json", "01-issue-J-100-log-fails.json"],
            )

    def test_stale_passed_config_cannot_bypass_updated_current_config_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, done, results = temp_inbox(root)
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            cfg_path = root / "config.json"
            cfg_path.write_text(json.dumps({"mode": "test", "live_password": ""}))
            stale_config = {"mode": "test", "live_password": "", "weight": {"kg_per_piece": 1.2, "loss_allowance": 0.05}}

            cfg_path.write_text(json.dumps({"mode": "test", "live_password": "secret"}))
            with mock.patch.object(inbox_mod, "CONFIG_PATH", cfg_path):
                with self.assertRaises(ConfigError):
                    read_all(
                        Connector(),
                        inbox_dir=inbox,
                        done_dir=done,
                        results_dir=results,
                        config=stale_config,
                    )

            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertFalse(done.exists())
            self.assertFalse(results.exists())

    def test_read_all_refuses_invalid_weight_in_current_config_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, done, results = temp_inbox(root)
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            cfg_path = root / "config.json"
            cfg_path.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "weight": {"kg_per_piece": 0, "loss_allowance": 0.05},
            }))

            with self.assertRaises(ConfigError):
                read_all(
                    Connector(),
                    inbox_dir=inbox,
                    done_dir=done,
                    results_dir=results,
                    config_path=cfg_path,
                )

            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertFalse(done.exists())
            self.assertFalse(results.exists())

    def test_reset_and_reread_nine_samples_reconciles_without_14_results(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            for sample in sorted(SAMPLES.glob("*.json")):
                shutil.copy2(sample, inbox / sample.name)
            first_connector = Connector()
            read_all(first_connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            # Simulate desk reset (fresh ledger) while inbox/results retains prior run
            reset_connector = Connector()
            for sample in sorted(SAMPLES.glob("*.json")):
                shutil.copy2(sample, inbox / sample.name)
            read_all(reset_connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(len(list(results.glob("*.json"))), 9)
            self.assertEqual(len(list(done.glob("*.json"))), 18)
            self.assertEqual(
                reconcile(reset_connector, results),
                {
                    "stock_results": 7,
                    "entries": 7,
                    "missing_entry": [],
                    "missing_result": [],
                    "unreadable": [],
                },
            )

    def test_failed_move_to_done_cleans_placeholder_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            original_bytes = (inbox / ISSUE_FILE).read_bytes()
            real_replace = os.replace

            def fail_done_replace(src, dst):
                if Path(dst).parent == done:
                    raise PermissionError("simulated Windows lock")
                return real_replace(src, dst)

            with mock.patch("connector.inbox.os.replace", side_effect=fail_done_replace):
                with self.assertRaises(PermissionError):
                    read_next(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertEqual((inbox / ISSUE_FILE).read_bytes(), original_bytes)
            self.assertEqual(list(done.iterdir()), [])

    def test_result_write_uses_tmp_suffix_and_cleans_up_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)

            read_next(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)
            self.assertEqual([p.name for p in results.iterdir()], [ISSUE_FILE])

            shutil.copy2(SAMPLES / "02-return-R-1.json", inbox / "02-return-R-1.json")
            with mock.patch("connector.inbox.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    read_next(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual([p.name for p in results.iterdir()], [ISSUE_FILE])
            self.assertEqual(list(results.glob("*.tmp")), [])

    def test_nine_queued_samples_read_three_times_keep_nine_results(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            for sample in sorted(SAMPLES.glob("*.json")):
                shutil.copy2(sample, inbox / sample.name)
            connector = Connector()

            for _ in range(3):
                read_all(
                    connector, switch_a=True, inbox_dir=inbox, done_dir=done, results_dir=results
                )

            self.assertEqual(len(list(results.glob("*.json"))), 9)
            self.assertEqual(list(results.glob("*.tmp")), [])

    def test_read_all_refuses_when_config_weight_changes_after_connector_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, done, results = temp_inbox(root)
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            cfg_path = root / "config.json"
            cfg_path.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "weight": {"kg_per_piece": 1.5, "loss_allowance": 0.05},
            }))
            connector = Connector(weight={"kg_per_piece": 1.2, "loss_allowance": 0.05})

            with self.assertRaises(ConfigError) as caught:
                read_all(
                    connector,
                    inbox_dir=inbox,
                    done_dir=done,
                    results_dir=results,
                    config_path=cfg_path,
                )

            self.assertIn("Connector weight does not match", str(caught.exception))
            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertFalse(done.exists())
            self.assertFalse(results.exists())

    def test_read_all_allows_equivalent_normalized_weight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, done, results = temp_inbox(root)
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            cfg_path = root / "config.json"
            cfg_path.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "weight": {"kg_per_piece": 2, "loss_allowance": 0},
            }))
            connector = Connector(weight={"kg_per_piece": 2.0, "loss_allowance": 0.0})

            outcomes = read_all(
                connector,
                inbox_dir=inbox,
                done_dir=done,
                results_dir=results,
                config_path=cfg_path,
            )

            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0]["status"], "Posted")


if __name__ == "__main__":
    unittest.main()
