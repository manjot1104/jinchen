"""Regression tests for Notification Rules (AC-1 through AC-5)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from connector.inbox import load_config
from connector.notifications import NotificationStore, working_hours_between
from connector.poc import ConfigError


class NotificationRulesTests(unittest.TestCase):
    def _row(
        self,
        ref="J-100",
        work_order="WO-2401",
        kind="issue",
        status="Business Rejected",
        code="E-ITEM",
        timestamp="2026-11-20T09:00:00",
        **extra,
    ):
        data = {
            "ref": ref,
            "work_order": work_order,
            "kind": kind,
            "status": status,
            "code": code,
            "timestamp": timestamp,
        }
        data.update(extra)
        return data

    # --- AC-1: Old failure notifications ---
    def test_failure_younger_than_4_working_hours_generates_zero_notices(self):
        store = NotificationStore()
        rows = [self._row(timestamp="2026-11-20T09:00:00")]
        emitted = store.evaluate(rows, now="2026-11-20T12:59:00")
        self.assertEqual(emitted, [])
        self.assertEqual(store.notices, [])

    def test_failure_reaching_4_working_hours_notifies_support_and_stores_manager(self):
        store = NotificationStore()
        rows = [self._row(timestamp="2026-11-20T09:00:00")]
        emitted = store.evaluate(rows, now="2026-11-20T13:00:00")
        self.assertEqual(len(emitted), 2)
        self.assertEqual({n["recipient"] for n in emitted}, {"Support", "Stores Manager"})
        self.assertTrue(all(n["type"] == "old_failure" for n in emitted))

    def test_working_hours_handles_outside_working_hours_and_weekends(self):
        # Fri 16:00 -> Mon 11:00 = 1h (Fri 16-17) + 0h (Sat/Sun) + 2h (Mon 9-11) = 3h
        self.assertEqual(
            working_hours_between("2026-11-20T16:00:00", "2026-11-23T11:00:00"),
            3.0,
        )
        store = NotificationStore()
        rows = [self._row(timestamp="2026-11-20T16:00:00")]
        self.assertEqual(store.evaluate(rows, now="2026-11-23T11:00:00"), [])
        # Mon 12:00 reaches 4.0 working hours
        emitted = store.evaluate(rows, now="2026-11-23T12:00:00")
        self.assertEqual(len(emitted), 2)

    def test_repeated_polling_generates_only_one_old_failure_notice_per_recipient(self):
        store = NotificationStore()
        rows = [self._row(timestamp="2026-11-20T09:00:00")]
        first = store.evaluate(rows, now="2026-11-20T13:30:00")
        second = store.evaluate(rows, now="2026-11-20T15:00:00")
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        self.assertEqual(len(store.notices), 2)

    def test_resolved_or_posted_rows_generate_zero_old_failure_notices(self):
        store = NotificationStore()
        rows = [
            self._row(ref="J-1", status="Business Rejected", code="E-ITEM", timestamp="2026-11-20T09:00:00"),
            self._row(ref="J-1", status="Posted", code="", timestamp="2026-11-20T09:30:00"),
        ]
        self.assertEqual(store.evaluate(rows, now="2026-11-20T16:00:00"), [])
        self.assertEqual(store.notices, [])

    # --- AC-2: E-AMEND notifications ---
    def test_e_amend_notifies_planner_once_with_safe_metadata(self):
        store = NotificationStore()
        rows = [self._row(ref="J-AM", code="E-AMEND", timestamp="2026-11-20T10:00:00")]
        first = store.evaluate(rows, now="2026-11-20T10:05:00")
        second = store.evaluate(rows, now="2026-11-20T10:10:00")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(first[0]["type"], "e_amend")
        self.assertEqual(first[0]["recipient"], "Planner")
        self.assertEqual(first[0]["code"], "E-AMEND")
        self.assertEqual(first[0]["row_id"], "J-AM")
        self.assertEqual(first[0]["work_order"], "WO-2401")

    # --- AC-3: Repeated E-STOCK notifications ---
    def test_estock_triggers_only_on_third_distinct_failure_same_order_and_day(self):
        store = NotificationStore()
        r1 = self._row(ref="J-1", code="E-STOCK", timestamp="2026-11-20T09:00:00")
        r2 = self._row(ref="J-2", code="E-STOCK", timestamp="2026-11-20T09:10:00")
        r3 = self._row(ref="J-3", code="E-STOCK", timestamp="2026-11-20T09:20:00")
        r4 = self._row(ref="J-4", code="E-STOCK", timestamp="2026-11-20T09:30:00")

        self.assertEqual(store.evaluate([r1], now="2026-11-20T09:05:00"), [])
        self.assertEqual(store.evaluate([r1, r2], now="2026-11-20T09:15:00"), [])
        third = store.evaluate([r1, r2, r3], now="2026-11-20T09:25:00")
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["type"], "e_stock_repeated")
        self.assertEqual(third[0]["recipient"], "Stores")
        self.assertEqual(third[0]["work_order"], "WO-2401")
        # 4th failure and repeated polling do not duplicate
        self.assertEqual(store.evaluate([r1, r2, r3, r4], now="2026-11-20T09:35:00"), [])

    def test_estock_does_not_combine_different_orders_or_different_days(self):
        store = NotificationStore()
        rows = [
            self._row(ref="A-1", work_order="WO-1", code="E-STOCK", timestamp="2026-11-20T09:00:00"),
            self._row(ref="A-2", work_order="WO-1", code="E-STOCK", timestamp="2026-11-20T09:05:00"),
            self._row(ref="B-1", work_order="WO-2", code="E-STOCK", timestamp="2026-11-20T09:10:00"),
            self._row(ref="A-3", work_order="WO-1", code="E-STOCK", timestamp="2026-11-21T09:00:00"),
        ]
        emitted = store.evaluate(rows, now="2026-11-21T09:05:00")
        self.assertEqual([n for n in emitted if n["type"] == "e_stock_repeated"], [])

    def test_estock_polling_or_retrying_same_event_does_not_inflate_count(self):
        store = NotificationStore()
        same_event = self._row(
            ref="J-1", event_id="EVT-1", code="E-STOCK", timestamp="2026-11-20T09:00:00"
        )
        for _ in range(5):
            self.assertEqual(store.evaluate([same_event], now="2026-11-20T09:05:00"), [])
        self.assertEqual(store.notices, [])

    def test_estock_ignores_unrelated_error_codes(self):
        store = NotificationStore()
        rows = [
            self._row(ref="J-1", code="E-ITEM", timestamp="2026-11-20T09:00:00"),
            self._row(ref="J-2", code="E-QTY", timestamp="2026-11-20T09:05:00"),
            self._row(ref="J-3", code="E-STOCK", timestamp="2026-11-20T09:10:00"),
        ]
        self.assertEqual(store.evaluate(rows, now="2026-11-20T09:15:00"), [])

    # --- AC-4: Payload confidentiality ---
    def test_notifications_never_contain_payload_fields_secrets_or_exception_text(self):
        store = NotificationStore()
        secret_payload = '{"item": "ST-20", "batch": "H-441", "live_password": "secret-xyz"}'
        rows = [
            self._row(
                ref="J-SEC-1",
                code="E-AMEND",
                timestamp="2026-11-20T09:00:00",
                payload=secret_payload,
                raw_body=secret_payload,
                exception="secret-xyz leaked here",
                live_password="secret-xyz",
            ),
            self._row(ref="J-SEC-2", code="E-STOCK", timestamp="2026-11-20T09:01:00", payload=secret_payload),
            self._row(ref="J-SEC-3", code="E-STOCK", timestamp="2026-11-20T09:02:00", payload=secret_payload),
            self._row(ref="J-SEC-4", code="E-STOCK", timestamp="2026-11-20T09:03:00", payload=secret_payload),
        ]
        emitted = store.evaluate(rows, now="2026-11-20T15:00:00")
        self.assertGreater(len(emitted), 0)
        serialized = json.dumps(emitted)
        for forbidden in ("secret-xyz", "ST-20", "H-441", "live_password", "leaked here"):
            self.assertNotIn(forbidden, serialized)

    # --- AC-5: Posted rows generate zero notices ---
    def test_posted_rows_generate_zero_notices_for_all_types(self):
        store = NotificationStore()
        rows = [
            self._row(ref="P-1", status="Posted", code="E-AMEND", timestamp="2026-11-20T08:00:00"),
            self._row(ref="P-2", status="Posted", code="E-STOCK", timestamp="2026-11-20T08:05:00"),
            self._row(ref="P-3", status="Posted", code="E-STOCK", timestamp="2026-11-20T08:10:00"),
            self._row(ref="P-4", status="Posted", code="E-STOCK", timestamp="2026-11-20T08:15:00"),
        ]
        self.assertEqual(store.evaluate(rows, now="2026-11-20T18:00:00"), [])
        self.assertEqual(store.notices, [])

    # --- Persistence, partial failure safety & config ---
    def test_persistence_across_restarts_and_atomic_failure_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mailbox = root / "mailbox.jsonl"
            state = root / "state.json"

            store1 = NotificationStore(mailbox_path=mailbox, state_path=state)
            rows = [self._row(ref="J-AM", code="E-AMEND", timestamp="2026-11-20T10:00:00")]
            self.assertEqual(len(store1.evaluate(rows, now="2026-11-20T10:05:00")), 1)

            # New instance loads state and mailbox -> no duplicate on re-evaluation
            store2 = NotificationStore(mailbox_path=mailbox, state_path=state)
            self.assertEqual(store2.evaluate(rows, now="2026-11-20T10:10:00"), [])
            self.assertEqual(len(store2.notices), 1)

            # Simulated mailbox write failure must not mark notification as delivered
            rows2 = [self._row(ref="J-AM-2", code="E-AMEND", timestamp="2026-11-20T10:15:00")]
            with mock.patch("connector.notifications._atomic_write_text", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store2.evaluate(rows2, now="2026-11-20T10:20:00")
            self.assertNotIn("e_amend|J-AM-2|WO-2401|Planner", store2.sent_keys)

    def test_notifications_config_defaults_and_invalid_values(self):
        cfg = load_config()
        self.assertEqual(cfg["notifications"]["old_failure_working_hours"], 4.0)
        self.assertEqual(cfg["notifications"]["estock_threshold"], 3)

        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "config.json"
            bad.write_text(json.dumps({
                "mode": "test",
                "live_password": "",
                "notifications": {"old_failure_working_hours": 0},
            }))
            with self.assertRaises(ConfigError):
                load_config(bad)


if __name__ == "__main__":
    unittest.main()
