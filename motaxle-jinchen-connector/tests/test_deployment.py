"""The connector is its own deployable unit (US-024)."""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import connector.inbox as inbox

PROJECT = Path(__file__).resolve().parent.parent
REPO = PROJECT.parent
FILLED_PASSWORD = re.compile(r'"live_password"\s*:\s*"[^"]+"')
BENCH_APP_FILES = {"hooks.py", "modules.txt", "patches.txt"}


def tracked_files():
    """Every file git tracks in the repository, or every file in the folder without git."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True,
                             capture_output=True).stdout.decode("utf-8")
        return [REPO / name for name in out.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in PROJECT.rglob("*") if path.is_file()]


def text_of(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


class OutsideErpnextAndMotaxle(unittest.TestCase):
    def test_no_bench_app_files(self):
        names = {path.name for path in tracked_files()}
        self.assertEqual(names & BENCH_APP_FILES, set())
        for path in tracked_files():
            self.assertNotIn("motaxle-bench", path.parts, path)
            self.assertNotIn("apps", path.relative_to(REPO).parts[:1], path)

    def test_no_frappe_or_erpnext_import(self):
        imports = re.compile(r"^\s*(from|import)\s+(frappe|erpnext)\b", re.M)
        for path in sorted(PROJECT.rglob("*.py")):
            self.assertIsNone(imports.search(text_of(path)), path.name)

    def test_standard_library_only(self):
        for name in ("requirements.txt", "setup.py", "pyproject.toml", "Pipfile"):
            self.assertFalse((PROJECT / name).exists(), name)


class NoLivePassword(unittest.TestCase):
    def test_no_tracked_file_holds_a_filled_live_password(self):
        for path in tracked_files():
            self.assertIsNone(FILLED_PASSWORD.search(text_of(path)), path.name)

    def test_no_local_log_or_result_holds_a_filled_live_password(self):
        for path in sorted((PROJECT / "inbox").rglob("*")):
            if path.is_file():
                self.assertIsNone(FILLED_PASSWORD.search(text_of(path)), path.name)

    def test_the_pattern_finds_a_filled_password(self):
        # Built at run time, so this file does not hold the pattern itself.
        filled = json.dumps(dict([("live_password", "x")]))
        empty = json.dumps(dict([("live_password", "")]))
        self.assertIsNotNone(FILLED_PASSWORD.search(filled))
        self.assertIsNone(FILLED_PASSWORD.search(empty))


class StartStopRestart(unittest.TestCase):
    def run_main(self, serve_forever):
        import server  # importing must not bind the port
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"mode": "test", "live_password": ""}))
            with mock.patch.object(inbox, "CONFIG_PATH", path), \
                 mock.patch.object(server, "ThreadingHTTPServer") as built, \
                 mock.patch("builtins.print") as printed:
                built.return_value.serve_forever.side_effect = serve_forever
                code = server.main()
        lines = [call.args[0] for call in printed.call_args_list if call.args]
        return code, built.return_value, lines

    def test_ctrl_c_stops_the_desk_and_frees_the_port(self):
        code, httpd, lines = self.run_main(KeyboardInterrupt)
        self.assertEqual(code, 0)
        httpd.server_close.assert_called_once()
        self.assertEqual(lines[-1], "The desk stopped.")

    def test_a_restart_is_a_second_start(self):
        first = self.run_main(KeyboardInterrupt)
        second = self.run_main(KeyboardInterrupt)
        self.assertEqual(first[0], 0)
        self.assertEqual(second[0], 0)
        self.assertIn("Open http://127.0.0.1:8765", second[2])

    def test_deployment_doc_matches_the_code(self):
        doc = text_of(PROJECT / "docs" / "deployment.md")
        code = text_of(PROJECT / "server.py")
        for sentence in ("The desk stopped.", "The desk did not start.", "Ctrl+C stops it."):
            self.assertIn(sentence, doc)
            self.assertIn(sentence, code)
        for heading in ("## Start", "## Stop", "## Restart", "## Test and live settings"):
            self.assertIn(heading, doc)
        self.assertIn("docs/deployment.md", text_of(PROJECT / "README.md"))
        self.assertTrue((PROJECT / "evidence" / "US-024-restart.txt").is_file())


if __name__ == "__main__":
    unittest.main()
