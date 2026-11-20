import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import connector.inbox as inbox
from connector.inbox import load_config, read_all
from connector.poc import ConfigError, Connector

PROJECT = Path(__file__).resolve().parent.parent
SAMPLE = PROJECT / "inbox" / "samples" / "01-issue-J-100-log-fails.json"
PASSWORD_SENTENCE = "Test mode refuses to start when a live password is set."
MODE_SENTENCE = "Mode must be test or live."


def write_config(folder, mode, live_password):
    path = Path(folder) / "config.json"
    path.write_text(json.dumps({"mode": mode, "live_password": live_password}))
    return path


class ConfigTests(unittest.TestCase):
    def test_committed_config_has_an_empty_password_and_loads(self):
        committed = json.loads((PROJECT / "config.json").read_text())
        self.assertEqual(committed["mode"], "test")
        self.assertEqual(committed["live_password"], "")
        self.assertEqual(load_config()["mode"], "test")

    def test_filled_password_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "live-secret")
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
        self.assertEqual(str(caught.exception), PASSWORD_SENTENCE)

    def test_bad_mode_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "demo", "")
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
        self.assertEqual(str(caught.exception), MODE_SENTENCE)

    def test_read_all_refuses_a_filled_password_before_any_file_moves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir, done, results = root / "in", root / "done", root / "results"
            inbox_dir.mkdir()
            shutil.copy2(SAMPLE, inbox_dir / SAMPLE.name)
            config = write_config(root, "test", "live-secret")
            connector = Connector()

            with self.assertRaises(ConfigError) as caught:
                read_all(connector, inbox_dir=inbox_dir, done_dir=done,
                         results_dir=results, config_path=config)

            self.assertEqual(str(caught.exception), PASSWORD_SENTENCE)
            self.assertTrue((inbox_dir / SAMPLE.name).exists())
            self.assertFalse(done.exists())
            self.assertEqual(connector.kg(), {"raw": 500, "wip": 0})
            self.assertEqual(len(connector.ledger.entries), 0)

    def test_read_all_refuses_a_bad_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir = root / "in"
            inbox_dir.mkdir()
            shutil.copy2(SAMPLE, inbox_dir / SAMPLE.name)
            config = write_config(root, "demo", "")
            connector = Connector()

            with self.assertRaises(ConfigError) as caught:
                read_all(connector, inbox_dir=inbox_dir, done_dir=root / "done",
                         results_dir=root / "results", config_path=config)

            self.assertEqual(str(caught.exception), MODE_SENTENCE)
            self.assertEqual(connector.kg()["raw"], 500)

    def test_read_all_reads_with_an_empty_password(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox_dir = root / "in"
            inbox_dir.mkdir()
            shutil.copy2(SAMPLE, inbox_dir / SAMPLE.name)
            config = write_config(root, "test", "")
            connector = Connector()

            results = read_all(connector, inbox_dir=inbox_dir, done_dir=root / "done",
                               results_dir=root / "results", config_path=config)

            self.assertEqual(len(results), 1)
            self.assertEqual(connector.kg()["raw"], 380)

    def test_server_main_does_not_start_with_a_filled_password(self):
        import server  # importing must not bind the port
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "live-secret")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(server, "ThreadingHTTPServer") as built, \
                 mock.patch("builtins.print") as printed:
                code = server.main()
        self.assertEqual(code, 1)
        built.assert_not_called()
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertIn(PASSWORD_SENTENCE, lines)
        self.assertNotIn("live-secret", " ".join(lines))

    def test_server_main_does_not_start_with_a_bad_mode(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "demo", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(server, "ThreadingHTTPServer") as built, \
                 mock.patch("builtins.print") as printed:
                code = server.main()
        self.assertEqual(code, 1)
        built.assert_not_called()
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertIn(MODE_SENTENCE, lines)

    def test_server_main_builds_a_server_with_a_good_config(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(server, "ThreadingHTTPServer") as built, \
                 mock.patch("builtins.print"):
                server.main()
        built.assert_called_once_with((server.HOST, server.PORT), server.Handler)
        built.return_value.serve_forever.assert_called_once()

    def test_prove_main_refuses_a_filled_password(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "live-secret")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertEqual(lines, [PASSWORD_SENTENCE, "prove.py did not run"])
        self.assertFalse(any(str(line).startswith("TC-02") for line in lines))
        self.assertNotIn("live-secret", " ".join(lines))

    def test_prove_main_refuses_a_bad_mode(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "demo", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertEqual(lines, [MODE_SENTENCE, "prove.py did not run"])
        self.assertFalse(any(str(line).startswith("TC-02") for line in lines))

    def test_validate_config_uses_source_neutral_message_for_non_dict(self):
        with self.assertRaises(ConfigError) as caught:
            inbox._validate_config(["not", "a", "dict"])
        self.assertEqual(str(caught.exception), "Config is not a valid JSON object.")

    def test_invalid_json_file_keeps_file_specific_error_message(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
        self.assertEqual(str(caught.exception), "config.json is not a valid JSON object.")

    def test_prove_main_propagates_validated_config_and_revalidates_on_read_all(self):
        import prove
        real_prove_load = prove.load_config
        real_inbox_load = inbox.load_config
        real_read_all = read_all
        configs_passed = []

        def spy_read_all(*args, **kwargs):
            configs_passed.append(kwargs.get("config"))
            return real_read_all(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(prove, "load_config", wraps=real_prove_load) as mocked_prove_load, \
                 mock.patch.object(inbox, "load_config", wraps=real_inbox_load) as mocked_inbox_load, \
                 mock.patch.object(prove, "read_all", side_effect=spy_read_all), \
                 mock.patch("builtins.print"):
                prove.main()

        self.assertEqual(mocked_prove_load.call_count, 1)
        self.assertEqual(mocked_inbox_load.call_count, 2)
        self.assertEqual(len(configs_passed), 2)
        for cfg in configs_passed:
            self.assertIsNotNone(cfg)
            self.assertEqual(cfg["mode"], "test")

    def test_prove_run_folder_isolates_samples_and_examples(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path):
                with prove.run_folder(PROJECT / "inbox" / "samples") as (connector, results, results_dir):
                    self.assertEqual(len(results), 9)
                    self.assertEqual(connector.kg(), {"raw": 384.0, "wip": 116.0})
                    self.assertEqual(len(list(results_dir.glob("*.json"))), 9)
                with prove.run_folder(PROJECT / "inbox" / "examples") as (connector, results, results_dir):
                    self.assertEqual(len(results), 8)
                    self.assertEqual(connector.kg(), {"raw": 500.0, "wip": 0.0})
                    self.assertEqual(len(connector.ledger.entries), 0)
                    self.assertEqual(connector.ledger.capa, 0)

    def test_prove_main_fails_with_tighter_loss_allowance(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "weight": {"kg_per_piece": 1.2, "loss_allowance": 0.02},
            }))
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("TC-12 FAIL", output)
        self.assertNotIn("ALL PASS", output)

    def test_prove_main_fails_with_heavier_kg_per_piece(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "weight": {"kg_per_piece": 1.5, "loss_allowance": 0.05},
            }))
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("TC-12 FAIL", output)
        self.assertNotIn("ALL PASS", output)

    def test_prove_main_passes_with_consistent_custom_weight(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "weight": {"kg_per_piece": 1.5, "loss_allowance": 0.30},
            }))
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch("builtins.print") as printed:
                prove.main()
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("Config weight: kg_per_piece=1.5, loss_allowance=0.3", output)
        self.assertIn("ALL PASS", output)

    def test_prove_main_fails_if_a_registered_check_is_missing(self):
        import prove
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(prove, "CHECKS", prove.CHECKS + ("14",)), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        lines = [str(call.args[0]) for call in printed.call_args_list if call.args]
        self.assertNotIn("ALL PASS", lines)
        self.assertTrue(any(line.startswith("NOT PASS:") for line in lines))

    def test_prove_main_fails_if_an_unexpected_check_id_runs(self):
        import prove
        real_check = prove.check

        def renumbered_check(number, description, test):
            mapped = "99" if number == "13" else number
            return real_check(mapped, description, test)

        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(prove, "check", side_effect=renumbered_check), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        lines = [str(call.args[0]) for call in printed.call_args_list if call.args]
        self.assertNotIn("ALL PASS", lines)
        self.assertTrue(any(line.startswith("NOT PASS:") for line in lines))

    def test_prove_main_fails_if_a_check_id_runs_twice(self):
        import prove
        real_check = prove.check

        def duplicate_check(number, description, test):
            mapped = "12" if number == "13" else number
            return real_check(mapped, description, test)

        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(prove, "check", side_effect=duplicate_check), \
                 mock.patch("builtins.print") as printed:
                with self.assertRaises(SystemExit) as caught:
                    prove.main()
        self.assertEqual(caught.exception.code, 1)
        lines = [str(call.args[0]) for call in printed.call_args_list if call.args]
        self.assertNotIn("ALL PASS", lines)
        self.assertTrue(any(line.startswith("NOT PASS:") for line in lines))


if __name__ == "__main__":
    unittest.main()
