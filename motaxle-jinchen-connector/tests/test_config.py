import json
import shutil
import socket
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


def port_open(port=8765):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


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
        self.assertFalse(port_open())
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "test", "live-secret")
            with mock.patch.object(inbox, "CONFIG_PATH", path):
                with mock.patch("builtins.print") as printed:
                    code = server.main()
        self.assertEqual(code, 1)
        self.assertFalse(port_open())
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertIn(PASSWORD_SENTENCE, lines)
        self.assertNotIn("live-secret", " ".join(lines))

    def test_server_main_does_not_start_with_a_bad_mode(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            path = write_config(temp, "demo", "")
            with mock.patch.object(inbox, "CONFIG_PATH", path):
                with mock.patch("builtins.print") as printed:
                    code = server.main()
        self.assertEqual(code, 1)
        self.assertFalse(port_open())
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertIn(MODE_SENTENCE, lines)


if __name__ == "__main__":
    unittest.main()
