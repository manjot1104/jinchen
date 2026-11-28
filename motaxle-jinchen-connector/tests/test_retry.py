"""Integration and regression tests for technical failure retries and Dead status (US-027)."""

from datetime import datetime, timedelta
import http.client
import json
import re
import tempfile
import unittest
from pathlib import Path

from connector import exceptions as exception_view
from connector.inbox import load_config
from connector.poc import ConfigError, Connector, LOG_STATUSES, Result
from connector.retry import DEFAULT_RETRY, classify_failure


class FakeClock:
    def __init__(self, start=None):
        self.now = start or datetime(2026, 11, 20, 9, 0, 0)

    def __call__(self):
        current = self.now
        self.now = self.now + timedelta(seconds=1)
        return current


def _issue_msg(ref="J-100", work_order="WO-2401"):
    return {
        "kind": "issue",
        "id": ref,
        "work_order": work_order,
        "item": "ST-20",
        "qty": 120,
        "uom": "Kg",
        "batch": "H-441",
        "from_warehouse": "Raw Material Store",
        "to_warehouse": "Forging WIP",
    }


class RetryIntegrationTests(unittest.TestCase):
    def _make_connector(self, retry_config=None, log_path=None):
        sleeps = []
        clock = FakeClock()
        connector = Connector(
            retry_config=retry_config,
            sleeper=sleeps.append,
            log_path=log_path,
        )
        connector.clock = clock
        return connector, sleeps, clock

    def test_01_e_timeout_succeeds_after_retry_with_connector_process(self):
        connector, sleeps, _ = self._make_connector()
        calls = {"n": 0}

        def transport(_msg):
            calls["n"] += 1
            if calls["n"] == 1:
                return Result("Technical Failed", "E-TIMEOUT", "", "Timed out.")
            return Result("Posted", "", "STE-0001", "Posted.")

        result = connector.process(_issue_msg(), transport=transport)
        self.assertEqual(result.status, "Posted")
        self.assertEqual(result.document, "STE-0001")
        self.assertEqual(sleeps, [1.0])
        self.assertEqual([r.status for r in connector.log], ["Technical Failed", "Posted"])
        self.assertEqual(exception_view.exception_list(connector), [])

    def test_02_e_timeout_exhausts_default_5_retries_and_logs_dead_to_exceptions_and_health(self):
        connector, sleeps, _ = self._make_connector()
        result = connector.process(
            _issue_msg(),
            transport=lambda _m: Result("Technical Failed", "E-TIMEOUT", "", "Timed out."),
        )
        # 1 initial attempt + 5 retries = 6 attempts; 5 sleeps: [1, 2, 4, 8, 16]
        self.assertEqual(result.status, "Dead")
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0, 16.0])
        self.assertEqual(len(connector.log), 6)
        self.assertEqual([r.status for r in connector.log[:5]], ["Technical Failed"] * 5)
        self.assertEqual(connector.log[-1].status, "Dead")
        self.assertEqual(connector.log[-1].attempts, 6)
        self.assertTrue(all(r.status in LOG_STATUSES for r in connector.log))

        health = exception_view.health(connector)
        ex_list = exception_view.exception_list(connector)
        self.assertEqual(health["dead"], 1)
        self.assertEqual(len(ex_list), 1)
        self.assertEqual(ex_list[0]["status"], "Dead")
        self.assertEqual(ex_list[0]["code"], "E-TIMEOUT")

    def test_03_custom_retry_count_and_backoff_control_behavior(self):
        connector, sleeps, _ = self._make_connector(
            retry_config={"max_retries": 3, "initial_delay_seconds": 0.5, "backoff_multiplier": 3.0}
        )
        result = connector.process(
            _issue_msg(),
            transport=lambda _m: {"status": "Technical Failed", "code": "E-TIMEOUT"},
        )
        self.assertEqual(result.status, "Dead")
        self.assertEqual(sleeps, [0.5, 1.5, 4.5])
        self.assertEqual(len(connector.log), 4)

    def test_04_e_auth_never_retried_and_logged_as_technical_failed(self):
        connector, sleeps, _ = self._make_connector()
        calls = {"n": 0}

        def transport(_msg):
            calls["n"] += 1
            return Result("Technical Failed", "E-AUTH", "", "Bad credentials.")

        result = connector.process(_issue_msg(), transport=transport)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(result.status, "Technical Failed")
        self.assertEqual(result.code, "E-AUTH")
        self.assertEqual([r.status for r in connector.log], ["Technical Failed"])
        self.assertEqual(exception_view.health(connector)["dead"], 0)
        self.assertEqual(len(exception_view.exception_list(connector)), 1)

    def test_05_business_rejection_codes_never_retried(self):
        business_codes = (
            "E-DUP", "E-ITEM", "E-UOM", "E-WH", "E-BATCH", "E-STOCK",
            "E-QTY", "E-OP", "E-REASON", "E-QC", "E-CLOSED", "E-AMEND",
        )
        for code in business_codes:
            connector, sleeps, _ = self._make_connector()
            result = connector.process(
                _issue_msg(ref=f"J-{code}"),
                transport=lambda _m, c=code: {"status": "Business Rejected", "code": c},
            )
            self.assertEqual(sleeps, [], code)
            self.assertEqual(result.status, "Business Rejected", code)
            self.assertEqual([r.status for r in connector.log], ["Business Rejected"], code)

    def test_06_empty_and_unknown_codes_on_technical_failed_stay_technical_failed(self):
        for payload in (
            {"status": "Technical Failed", "code": ""},
            {"status": "Technical Failed", "code": "E-503"},
            {"status": "", "code": "E-UNKNOWN"},
        ):
            with self.subTest(payload=payload):
                connector, sleeps, _ = self._make_connector()
                result = connector.process(_issue_msg(), transport=lambda _m, p=payload: p)
                self.assertEqual(sleeps, [])
                self.assertEqual(result.status, "Technical Failed")
                self.assertEqual(connector.log[-1].status, "Technical Failed")

        self.assertEqual(classify_failure(status="Business Rejected", code="E-ITEM"), "business")
        self.assertEqual(classify_failure(status="Technical Failed", code=""), "technical")
        self.assertEqual(classify_failure(status="Technical Failed", code="E-503"), "technical")

    def test_07_dropped_connections_retried_and_non_transient_errors_not_retried(self):
        for exc_cls in (ConnectionResetError, TimeoutError, http.client.RemoteDisconnected):
            connector, sleeps, _ = self._make_connector()
            calls = {"n": 0}

            def transport(_m, cls=exc_cls, state=calls):
                state["n"] += 1
                if state["n"] == 1:
                    raise cls("dropped")
                return Result("Posted", "", "STE-0002", "Posted.")

            result = connector.process(_issue_msg(), transport=transport)
            self.assertEqual(result.status, "Posted")
            self.assertEqual(sleeps, [1.0])
            self.assertEqual([r.status for r in connector.log], ["Technical Failed", "Posted"])

        connector2, sleeps2, _ = self._make_connector()

        def bad(_m):
            raise ValueError("unexpected parse error")

        result2 = connector2.process(_issue_msg(ref="J-VAL"), transport=bad)
        self.assertEqual(sleeps2, [])
        self.assertEqual(result2.status, "Technical Failed")
        self.assertEqual(connector2.log[-1].status, "Technical Failed")

    def test_08_lost_response_after_remote_post_is_caught_by_repeat_rule(self):
        connector, sleeps, _ = self._make_connector()
        remote_posts = {"n": 0}

        def transport_posted_then_timeout(msg):
            # Simulate remote ERPNext posting on attempt 1, while responses time out.
            if remote_posts["n"] == 0:
                connector.ledger.submit(
                    "issue", msg["id"], msg["work_order"], "ST-20", 120, "Kg", "H-441",
                    "Raw Material Store", "Forging WIP",
                )
            remote_posts["n"] += 1
            return Result("Technical Failed", "E-TIMEOUT", "", "Response timed out.")

        first = connector.process(_issue_msg(), transport=transport_posted_then_timeout)
        self.assertEqual(first.status, "Dead")
        calls_after_first = remote_posts["n"]
        # Re-sending the same message hits the repeat rule (E-DUP) before calling transport again.
        second = connector.process(_issue_msg(), transport=transport_posted_then_timeout)
        self.assertEqual(second.status, "Posted")
        self.assertEqual(second.code, "E-DUP")
        self.assertEqual(remote_posts["n"], calls_after_first)
        self.assertEqual(len(connector.ledger.entries), 1)

    def test_09_terminal_rows_are_not_resent_on_repeated_process_calls(self):
        connector, sleeps, _ = self._make_connector()
        calls = {"n": 0}

        def auth_fail(_m):
            calls["n"] += 1
            return Result("Technical Failed", "E-AUTH", "", "Bad credentials.")

        connector.process(_issue_msg(), transport=auth_fail)
        second = connector.process(_issue_msg(), transport=auth_fail)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(second.status, "Technical Failed")

    def test_10_timestamps_match_connector_clock_seconds_format_and_append_only_log(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "log.jsonl"
            c1, _, _ = self._make_connector(log_path=str(log_path))
            c1.process(
                _issue_msg(ref="J-TS"),
                transport=lambda _m: Result("Technical Failed", "E-TIMEOUT", "", "Timeout."),
            )
            c1.process(
                _issue_msg(ref="J-BIZ"),
                transport=lambda _m: Result("Business Rejected", "E-ITEM", "", "Bad item."),
            )
            for row in c1.log:
                self.assertRegex(row.timestamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

            # Restart from append-only log.jsonl preserves all 7 rows without loss
            c2, _, _ = self._make_connector(log_path=str(log_path))
            self.assertEqual(len(c2.log), 7)
            self.assertEqual(exception_view.health(c2)["dead"], 1)

    def test_11_reloaded_retryable_row_not_marked_dead_without_new_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "log.jsonl"
            c1, _, _ = self._make_connector(retry_config={"max_retries": 1}, log_path=str(log_path))
            # Manually record 1 Technical Failed E-TIMEOUT row (non-final)
            c1._log("J-REL", "WO-2401", "issue", "Technical Failed", "E-TIMEOUT", "")
            c2, _, _ = self._make_connector(retry_config={"max_retries": 1}, log_path=str(log_path))
            # Reloading alone must never mark it Dead
            self.assertEqual(c2.log[-1].status, "Technical Failed")
            self.assertEqual(exception_view.health(c2)["dead"], 0)
            # Only after a real failing attempt does it transition to Dead
            res = c2.process(
                _issue_msg(ref="J-REL"),
                transport=lambda _m: Result("Posted", "", "STE-0001", "Recovered."),
            )
            self.assertEqual(res.status, "Posted")
            self.assertEqual(c2.log[-1].status, "Posted")

    def test_12_strict_retry_config_validation(self):
        cfg = load_config()
        self.assertEqual(cfg["retry"], DEFAULT_RETRY)

        invalid_cases = [
            ("bad", "retry"),
            ({"max_attemps": 1}, "unknown"),
            ({"max_retries": 0}, "max_retries"),
            ({"max_retries": -1}, "max_retries"),
            ({"max_retries": 11}, "max_retries"),
            ({"max_retries": True}, "max_retries"),
            ({"max_retries": "5"}, "max_retries"),
            ({"initial_delay_seconds": 0}, "initial_delay_seconds"),
            ({"initial_delay_seconds": -1}, "initial_delay_seconds"),
            ({"initial_delay_seconds": 301}, "initial_delay_seconds"),
            ({"initial_delay_seconds": float("nan")}, "initial_delay_seconds"),
            ({"initial_delay_seconds": float("inf")}, "initial_delay_seconds"),
            ({"initial_delay_seconds": float("-inf")}, "initial_delay_seconds"),
            ({"initial_delay_seconds": True}, "initial_delay_seconds"),
            ({"initial_delay_seconds": "1"}, "initial_delay_seconds"),
            ({"backoff_multiplier": 1.0}, "backoff_multiplier"),
            ({"backoff_multiplier": float("nan")}, "backoff_multiplier"),
            ({"backoff_multiplier": float("inf")}, "backoff_multiplier"),
            ({"backoff_multiplier": True}, "backoff_multiplier"),
        ]
        for block, expected_field in invalid_cases:
            with self.subTest(block=block):
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "config.json"
                    path.write_text(json.dumps({"mode": "test", "live_password": "", "retry": block}))
                    with self.assertRaises(ConfigError) as caught:
                        load_config(path)
                    self.assertIn(expected_field, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
