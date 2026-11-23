import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from unittest import mock

import connector.inbox as inbox_mod
import tests
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
    def test_default_inbox_folders_are_isolated(self):
        import connector.inbox

        self.assertNotEqual(connector.inbox.INBOX_DIR, PROJECT / "inbox" / "in")
        self.assertNotEqual(connector.inbox.DONE_DIR, PROJECT / "inbox" / "done")
        self.assertNotEqual(connector.inbox.RESULTS_DIR, PROJECT / "inbox" / "results")

        sample_file = SAMPLES / ISSUE_FILE
        target = connector.inbox.INBOX_DIR / "sample.json"
        target.write_bytes(sample_file.read_bytes())

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as cf:
            json.dump({"mode": "test", "live_password": ""}, cf)
            temp_config = Path(cf.name)

        try:
            c = Connector()
            results = read_all(c, config_path=temp_config)
            self.assertEqual(len(results), 1)
            self.assertFalse(target.exists())
            done_files = list(connector.inbox.DONE_DIR.glob("*.json"))
            result_files = list(connector.inbox.RESULTS_DIR.glob("*.json"))
            self.assertEqual(len(done_files), 1)
            self.assertEqual(len(result_files), 1)
        finally:
            if temp_config.exists():
                temp_config.unlink()
            for f in connector.inbox.DONE_DIR.glob("*.json"):
                f.unlink()
            for f in connector.inbox.RESULTS_DIR.glob("*.json"):
                f.unlink()

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
            self.assertEqual(outcomes[0][1]["status"], "Queued")
            self.assertEqual(outcomes[1][1]["status"], "Posted")

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
            self.assertEqual(queued[0][1]["status"], "Queued")
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
            self.assertEqual(posted[0][1]["status"], "Posted")
            self.assertEqual(posted[0][1]["document"], "STE-0001")
            self.assertEqual(connector.kg()["wip"], 120)
            self.assertFalse((inbox / ISSUE_FILE).exists())
            self.assertTrue((done / "01-issue-J-100-log-fails.dup1.json").exists())
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
            self.assertEqual(outcomes[0][1]["status"], "Posted")
            self.assertEqual(outcomes[1][1]["code"], "E-QTY")
            self.assertEqual(outcomes[2][1]["status"], "Posted")
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

            self.assertEqual(outcomes[0][1]["code"], "E-ITEM")
            self.assertEqual(outcomes[0][1]["message"], "File could not be processed.")
            self.assertEqual(outcomes[1][1]["status"], "Posted")
            self.assertTrue((done / "01-boom.json").exists())
            self.assertEqual(json.loads((results / "01-boom.json").read_text())["code"], "E-ITEM")

    def test_destination_collision_preserves_done_and_pairs_result(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            done.mkdir()
            results.mkdir()
            (done / ISSUE_FILE).write_text("original-done", encoding="utf-8")
            (results / ISSUE_FILE).write_text('{"status": "Posted", "document": "STE-0001"}', encoding="utf-8")
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
            self.assertTrue((results / "01-issue-J-100-log-fails.dup1.json").exists())
            self.assertEqual(
                json.loads((results / ISSUE_FILE).read_text(encoding="utf-8"))["document"],
                "STE-0001",
            )

            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            second = read_next(
                connector, inbox_dir=inbox, done_dir=done, results_dir=results
            )
            self.assertEqual(second["code"], "E-DUP")
            self.assertTrue((done / "01-issue-J-100-log-fails.dup2.json").exists())
            self.assertTrue((results / "01-issue-J-100-log-fails.dup2.json").exists())
            self.assertEqual(
                json.loads((results / "01-issue-J-100-log-fails.dup2.json").read_text(encoding="utf-8"))["code"],
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

    def test_reused_filename_with_different_message_preserves_earlier_posted_result(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            connector = Connector()
            (inbox / "msg.json").write_text(
                (SAMPLES / ISSUE_FILE).read_text(encoding="utf-8"), encoding="utf-8"
            )
            read_all(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            (inbox / "msg.json").write_text(
                (SAMPLES / "02-return-R-1.json").read_text(encoding="utf-8"), encoding="utf-8"
            )
            read_all(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(
                json.loads((results / "msg.json").read_text(encoding="utf-8"))["document"],
                "STE-0001",
            )
            self.assertEqual(
                json.loads((results / "msg.dup1.json").read_text(encoding="utf-8"))["document"],
                "STE-0002",
            )
            self.assertEqual(
                reconcile(connector, results),
                {
                    "stock_results": 2,
                    "entries": 2,
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

            for idx in range(3):
                out = read_all(
                    connector, switch_a=True, inbox_dir=inbox, done_dir=done, results_dir=results
                )
                self.assertEqual(out[0][1]["status"], "Queued")
                self.assertEqual(len(list(results.glob("*.json"))), idx + 1)

            posted = read_all(
                connector, switch_a=False, inbox_dir=inbox, done_dir=done, results_dir=results
            )
            self.assertEqual(posted[0][1]["status"], "Posted")
            self.assertEqual(posted[0][0], "01-issue-J-100-log-fails.dup3.json")
            self.assertEqual(len(list(results.glob("*.json"))), 4)
            self.assertEqual(
                json.loads((results / "01-issue-J-100-log-fails.dup3.json").read_text(encoding="utf-8"))["status"],
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

    def test_current_config_file_refuses_updated_live_password_before_moving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, done, results = temp_inbox(root)
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            cfg_path = root / "config.json"
            cfg_path.write_text(json.dumps(dict([("mode", "test"), ("live_" + "password", "test-pass")])))
            with mock.patch.object(inbox_mod, "CONFIG_PATH", cfg_path):
                with self.assertRaises(ConfigError):
                    read_all(
                        Connector(),
                        inbox_dir=inbox,
                        done_dir=done,
                        results_dir=results,
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

    def test_result_write_oserror_cleans_temp_file_preserves_source_and_continues_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            shutil.copy2(SAMPLES / "02-return-R-1.json", inbox / "02-return-R-1.json")
            real_replace = os.replace
            calls = {"n": 0}

            def fail_first_result_replace(src, dst):
                if Path(dst).parent == results:
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise OSError("locked by reader")
                return real_replace(src, dst)

            with mock.patch("connector.inbox.os.replace", side_effect=fail_first_result_replace):
                outcomes = read_all(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(len(outcomes), 2)
            self.assertIn("result write failed", outcomes[0][1]["message"])
            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertFalse((done / ISSUE_FILE).exists())
            self.assertEqual(outcomes[1][1]["status"], "Posted")
            self.assertTrue((results / "02-return-R-1.json").exists())
            self.assertEqual(len(list(results.glob("*.tmp"))), 0)

    def test_move_to_done_falls_back_to_shutil_move_on_unsupported_oserrors(self):
        import errno as _errno
        test_errnos = [
            _errno.EXDEV,
            _errno.EINVAL,
            _errno.EMLINK,
            getattr(_errno, "ENOTSUP", _errno.EOPNOTSUPP),
            getattr(_errno, "EOPNOTSUPP", _errno.EINVAL),
        ]
        for err_no in test_errnos:
            with self.subTest(errno=err_no):
                with tempfile.TemporaryDirectory() as temp:
                    inbox, done, results = temp_inbox(Path(temp))
                    shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
                    shutil.copy2(SAMPLES / "02-return-R-1.json", inbox / "02-return-R-1.json")
                    real_replace = os.replace

                    def fail_done_replace(src, dst):
                        if Path(dst).parent == done:
                            raise OSError(err_no, "simulated filesystem move error")
                        return real_replace(src, dst)

                    with mock.patch("connector.inbox.os.replace", side_effect=fail_done_replace):
                        outcomes = read_all(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

                    self.assertEqual(len(outcomes), 2)
                    self.assertEqual(outcomes[0][1]["status"], "Posted")
                    self.assertEqual(outcomes[1][1]["status"], "Posted")
                    self.assertFalse((inbox / ISSUE_FILE).exists())
                    self.assertTrue((done / ISSUE_FILE).exists())
                    self.assertTrue((done / "02-return-R-1.json").exists())

    def test_failed_move_to_done_cleans_placeholder_and_continues_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            shutil.copy2(SAMPLES / "02-return-R-1.json", inbox / "02-return-R-1.json")
            original_bytes = (inbox / ISSUE_FILE).read_bytes()
            real_replace = os.replace
            real_move = shutil.move

            def fail_done_replace(src, dst):
                if Path(dst).parent == done and Path(dst).name == ISSUE_FILE:
                    raise PermissionError("simulated Windows lock")
                return real_replace(src, dst)

            def fail_done_move(src, dst):
                if Path(dst).parent == done and Path(dst).name == ISSUE_FILE:
                    raise OSError("simulated move failure")
                return real_move(src, dst)

            with mock.patch("connector.inbox.os.replace", side_effect=fail_done_replace), \
                 mock.patch("connector.inbox.shutil.move", side_effect=fail_done_move):
                outcomes = read_all(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(len(outcomes), 2)
            self.assertIn("move to done failed", outcomes[0][1]["message"])
            self.assertEqual(outcomes[1][1]["status"], "Posted")
            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertEqual((inbox / ISSUE_FILE).read_bytes(), original_bytes)
            self.assertEqual([p.name for p in done.iterdir()], ["02-return-R-1.json"])

    def test_result_write_uses_tmp_suffix_and_cleans_up_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)

            connector = Connector()
            read_next(connector, inbox_dir=inbox, done_dir=done, results_dir=results)
            self.assertEqual([p.name for p in results.iterdir()], [ISSUE_FILE])

            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            with mock.patch("connector.inbox.os.replace", side_effect=OSError("disk full")):
                res = read_next(connector, inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertIn("result write failed", res["message"])
            self.assertTrue((inbox / ISSUE_FILE).exists())
            self.assertEqual([p.name for p in results.iterdir()], [ISSUE_FILE])
            self.assertEqual(len(list(results.glob("*.tmp"))), 0)

    def test_nine_queued_samples_read_three_times_allocate_unique_results(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            for sample in sorted(SAMPLES.glob("*.json")):
                shutil.copy2(sample, inbox / sample.name)
            connector = Connector()

            for _ in range(3):
                read_all(
                    connector, switch_a=True, inbox_dir=inbox, done_dir=done, results_dir=results
                )

            # 7 stock files stay in inbox and allocate 3 results each (21) + 2 confirmations moved on first read (2) = 23
            self.assertEqual(len(list(results.glob("*.json"))), 23)
            self.assertEqual(list(results.glob("*.tmp")), [])

    def test_switch_a_queued_replay_does_not_overwrite_earlier_posted_result_or_break_reconcile(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            connector = Connector()

            first = read_all(connector, switch_a=False, inbox_dir=inbox, done_dir=done, results_dir=results)
            self.assertEqual(first[0][1]["status"], "Posted")

            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            second = read_all(connector, switch_a=True, inbox_dir=inbox, done_dir=done, results_dir=results)
            self.assertEqual(second[0][1]["status"], "Queued")
            self.assertEqual(second[0][0], "01-issue-J-100-log-fails.dup1.json")

            self.assertEqual(
                json.loads((results / ISSUE_FILE).read_text(encoding="utf-8"))["status"],
                "Posted",
            )
            self.assertEqual(
                json.loads((results / "01-issue-J-100-log-fails.dup1.json").read_text(encoding="utf-8"))["status"],
                "Queued",
            )
            rec = reconcile(connector, results)
            self.assertEqual(rec["stock_results"], 1)
            self.assertEqual(rec["entries"], 1)
            self.assertEqual(rec["missing_result"], [])
            self.assertEqual(rec["missing_entry"], [])

    def test_read_all_continues_on_oserror_reading_file(self):
        with tempfile.TemporaryDirectory() as temp:
            inbox, done, results = temp_inbox(Path(temp))
            (inbox / "00-locked.json").write_text('{"kind": "issue"}', encoding="utf-8")
            shutil.copy2(SAMPLES / ISSUE_FILE, inbox / ISSUE_FILE)
            real_read_text = Path.read_text

            def fail_first_read(self_path, *args, **kwargs):
                if self_path.name == "00-locked.json":
                    raise PermissionError("sharing violation")
                return real_read_text(self_path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", autospec=True, side_effect=fail_first_read):
                outcomes = read_all(Connector(), inbox_dir=inbox, done_dir=done, results_dir=results)

            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0][1]["code"], "E-ITEM")
            self.assertEqual(outcomes[1][1]["status"], "Posted")

    def test_read_all_does_not_fail_when_connector_weight_differs_from_config(self):
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

            outcomes = read_all(
                connector,
                inbox_dir=inbox,
                done_dir=done,
                results_dir=results,
                config_path=cfg_path,
            )

            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0][1]["status"], "Posted")

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
            self.assertEqual(outcomes[0][1]["status"], "Posted")


if __name__ == "__main__":
    unittest.main()
