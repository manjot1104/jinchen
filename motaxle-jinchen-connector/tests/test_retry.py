"""Regression tests for technical failure retries and Dead status."""

from datetime import datetime, timedelta
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from connector.inbox import load_config
from connector.poc import ConfigError
from connector.retry import DEFAULT_RETRY, RetryManager


class FakeClock:
    def __init__(self, start=None):
        self.now = start or datetime(2026, 11, 20, 9, 0, 0)

    def __call__(self):
        current = self.now
        self.now = self.now + timedelta(seconds=1)
        return current


class RetryTests(unittest.TestCase):
    def _make_manager(self, config=None, state_path=None):
        sleeps = []
        clock = FakeClock()
        mgr = RetryManager(
            config=config,
            state_path=state_path,
            clock=clock,
            sleeper=sleeps.append,
        )
        return mgr, sleeps, clock

    def test_01_e_timeout_succeeds_after_retry(self):
        mgr, sleeps, _ = self._make_manager()
        calls = {"n": 0}

        def transport():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"status": "Technical Failure", "code": "E-TIMEOUT"}
            return {"status": "Posted", "code": "", "document": "STE-0001"}

        row = mgr.run("J-100", "WO-2401", transport)
        self.assertEqual(row["status"], "Posted")
        self.assertEqual(row["document"], "STE-0001")
        self.assertEqual(len(row["attempts"]), 2)
        self.assertEqual(sleeps, [1.0])

    def test_02_e_timeout_exhausts_default_5_total_attempts(self):
        mgr, sleeps, _ = self._make_manager()
        row = mgr.run("J-100", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})
        self.assertEqual(len(row["attempts"]), 5)
        self.assertEqual(len(sleeps), 4)
        self.assertEqual(row["status"], "Dead")

    def test_03_retry_delays_grow_strictly_between_consecutive_retries(self):
        mgr, sleeps, _ = self._make_manager()
        mgr.run("J-100", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0])
        for earlier, later in zip(sleeps, sleeps[1:]):
            self.assertGreater(later, earlier)

    def test_04_custom_attempt_count_works(self):
        mgr, sleeps, _ = self._make_manager(config={"max_attempts": 3})
        row = mgr.run("J-100", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})
        self.assertEqual(len(row["attempts"]), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(row["status"], "Dead")

    def test_05_custom_delay_configuration_works(self):
        mgr, sleeps, _ = self._make_manager(
            config={"max_attempts": 4, "initial_delay_seconds": 0.5, "backoff_multiplier": 3.0}
        )
        mgr.run("J-100", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})
        self.assertEqual(sleeps, [0.5, 1.5, 4.5])

    def test_06_e_auth_results_in_zero_retries(self):
        mgr, sleeps, _ = self._make_manager()
        calls = {"n": 0}

        def transport():
            calls["n"] += 1
            return {"status": "Auth Rejected", "code": "E-AUTH"}

        row = mgr.run("J-100", "WO-2401", transport)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(row["attempts"]), 1)
        self.assertEqual(row["status"], "Auth Rejected")
        self.assertEqual(mgr.exception_list, [])

    def test_07_every_business_rejection_code_results_in_zero_retries(self):
        business_codes = (
            "E-DUP", "E-ITEM", "E-UOM", "E-WH", "E-BATCH", "E-STOCK",
            "E-QTY", "E-OP", "E-REASON", "E-QC", "E-CLOSED", "E-AMEND",
        )
        for code in business_codes:
            mgr, sleeps, _ = self._make_manager()
            row = mgr.run(f"J-{code}", "WO-2401", lambda c=code: {"status": "Business Rejected", "code": c})
            self.assertEqual(sleeps, [], code)
            self.assertEqual(len(row["attempts"]), 1, code)
            self.assertEqual(row["status"], "Business Rejected", code)
            self.assertEqual(mgr.exception_list, [], code)

    def test_08_business_rejections_remain_business_rejected_after_overnight_gap(self):
        mgr, sleeps, clock = self._make_manager()
        row = mgr.run("J-BIZ", "WO-2401", lambda: {"status": "Business Rejected", "code": "E-ITEM"})
        clock.now = clock.now + timedelta(hours=16)
        polled = mgr.run("J-BIZ", "WO-2401", lambda: {"status": "Business Rejected", "code": "E-ITEM"})
        self.assertEqual(polled["status"], "Business Rejected")
        self.assertEqual(len(row["attempts"]), 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(mgr.exception_list, [])

    def test_09_dropped_connections_are_retried(self):
        for exc_cls in (ConnectionResetError, TimeoutError, http.client.RemoteDisconnected):
            mgr, sleeps, _ = self._make_manager()
            calls = {"n": 0}

            def transport(cls=exc_cls, state=calls):
                state["n"] += 1
                if state["n"] == 1:
                    raise cls("dropped")
                return {"status": "Posted", "code": "", "document": "STE-0002"}

            row = mgr.run("J-CONN", "WO-2401", transport)
            self.assertEqual(row["status"], "Posted")
            self.assertEqual(len(row["attempts"]), 2)
            self.assertEqual(sleeps, [1.0])

    def test_10_unrelated_exceptions_are_not_retried(self):
        mgr, sleeps, _ = self._make_manager()

        def bad():
            raise ValueError("unexpected parse error")

        row = mgr.run("J-VAL", "WO-2401", bad)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(row["attempts"]), 1)
        self.assertEqual(row["status"], "Failed")
        self.assertEqual(mgr.exception_list, [])

    def test_11_and_12_exhausted_failures_become_dead_and_appear_in_exception_list(self):
        mgr, _, _ = self._make_manager()
        row = mgr.run("J-DEAD", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})
        self.assertEqual(row["status"], "Dead")
        self.assertEqual(len(mgr.exception_list), 1)
        self.assertEqual(mgr.exception_list[0]["id"], "J-DEAD")

    def test_13_and_14_every_attempt_has_timestamp_and_history_is_append_only(self):
        mgr, _, _ = self._make_manager()
        row = mgr.run("J-HIST", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})
        attempts = row["attempts"]
        self.assertEqual([a["attempt"] for a in attempts], [1, 2, 3, 4, 5])
        self.assertTrue(all(a["timestamp"] for a in attempts))
        self.assertEqual(
            [a["outcome"] for a in attempts],
            ["retry_scheduled", "retry_scheduled", "retry_scheduled", "retry_scheduled", "exhausted"],
        )

    def test_15_and_16_attempt_history_survives_restart_and_polling_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "retry_state.json"
            mgr1, _, _ = self._make_manager(state_path=state_path)
            mgr1.run("J-PERSIST", "WO-2401", lambda: {"status": "Technical Failure", "code": "E-TIMEOUT"})

            mgr2, sleeps2, _ = self._make_manager(state_path=state_path)
            self.assertEqual(len(mgr2.exception_list), 1)
            self.assertEqual(len(mgr2.rows["J-PERSIST"]["attempts"]), 5)

            # Polling again must not add duplicate attempts
            polled = mgr2.run("J-PERSIST", "WO-2401", lambda: {"status": "Posted", "code": "", "document": "STE-9"})
            self.assertEqual(polled["status"], "Dead")
            self.assertEqual(len(polled["attempts"]), 5)
            self.assertEqual(sleeps2, [])

    def test_17_missing_retry_configuration_uses_backward_compatible_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"mode": "test", "live_password": ""}))
            cfg = load_config(path)
        self.assertEqual(cfg["retry"], DEFAULT_RETRY)

    def test_18_invalid_retry_configuration_is_rejected(self):
        invalid_blocks = [
            "bad",
            {"max_attempts": 0},
            {"max_attempts": True},
            {"initial_delay_seconds": 0},
            {"backoff_multiplier": 1.0},
        ]
        for block in invalid_blocks:
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "config.json"
                path.write_text(json.dumps({"mode": "test", "live_password": "", "retry": block}))
                with self.assertRaises(ConfigError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
