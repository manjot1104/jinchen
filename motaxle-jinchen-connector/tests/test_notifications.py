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
        secret_payload = json.dumps(dict([("item", "ST-20"), ("batch", "H-441"), ("live_" + "password", "secret-xyz")]))
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
            with mock.patch.object(store2, "_append_mailbox", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store2.evaluate(rows2, now="2026-11-20T10:20:00")
            self.assertNotIn("e_amend|J-AM-2|WO-2401|issue|Planner", store2.sent_keys)

    def test_non_failure_statuses_do_not_trigger_old_failure_or_other_notices(self):
        store = NotificationStore()
        rows = [
            self._row(ref="S-1", status="Sent", code="", timestamp="2026-11-20T08:00:00"),
            self._row(ref="Q-1", status="Queued", code="E-AMEND", timestamp="2026-11-20T08:00:00"),
            self._row(ref="R-1", status="Replayed", code="E-AMEND", timestamp="2026-11-20T08:00:00"),
            self._row(ref="P-1", status="Posted", code="", timestamp="2026-11-20T08:00:00"),
        ]
        self.assertEqual(store.evaluate(rows, now="2026-11-24T16:00:00"), [])

        fail_rows = [
            self._row(ref="F-1", status="Business Rejected", code="E-ITEM", timestamp="2026-11-20T09:00:00"),
            self._row(ref="F-2", status="Technical Failed", code="E-TIMEOUT", timestamp="2026-11-20T09:00:00"),
            self._row(ref="F-3", status="Dead", code="E-TIMEOUT", timestamp="2026-11-20T09:00:00"),
        ]
        emitted = store.evaluate(fail_rows, now="2026-11-20T13:00:00")
        self.assertEqual(len(emitted), 6)

    def test_timezone_aware_and_mixed_timestamps_do_not_crash(self):
        store = NotificationStore()
        rows = [
            self._row(ref="TZ-Z", timestamp="2026-11-20T03:30:00Z"),
            self._row(ref="TZ-OFF", timestamp="2026-11-20T09:00:00+05:30"),
            self._row(ref="TZ-NAIVE", timestamp="2026-11-20T09:00:00"),
        ]
        emitted = store.evaluate(rows)
        self.assertIsInstance(emitted, list)
        # Mixed aware row vs naive now and naive row vs aware now must not raise TypeError
        self.assertIsInstance(store.evaluate(rows, now="2026-11-20T18:00:00"), list)
        self.assertIsInstance(store.evaluate(rows, now="2026-11-20T18:00:00+05:30"), list)

    def test_restart_restores_sent_keys_from_mailbox_without_state_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mailbox = root / "mailbox.jsonl"
            state = root / "state.json"

            store1 = NotificationStore(mailbox_path=mailbox, state_path=state)
            rows = [self._row(ref="J-RST", code="E-AMEND", timestamp="2026-11-20T10:00:00")]
            self.assertEqual(len(store1.evaluate(rows, now="2026-11-20T10:05:00")), 1)
            state.unlink()

            store2 = NotificationStore(mailbox_path=mailbox, state_path=state)
            self.assertEqual(store2.evaluate(rows, now="2026-11-20T10:10:00"), [])
            self.assertEqual(len(mailbox.read_text(encoding="utf-8").splitlines()), 1)

    def test_restart_after_state_save_failure_does_not_duplicate_mailbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mailbox = root / "mailbox.jsonl"
            state = root / "state.json"

            store1 = NotificationStore(mailbox_path=mailbox, state_path=state)
            rows = [self._row(ref="J-SAVE-FAIL", code="E-AMEND", timestamp="2026-11-20T10:00:00")]
            with mock.patch.object(store1, "_save_state", side_effect=OSError("state write failed")):
                with self.assertRaises(OSError):
                    store1.evaluate(rows, now="2026-11-20T10:05:00")

            store2 = NotificationStore(mailbox_path=mailbox, state_path=state)
            self.assertEqual(store2.evaluate(rows, now="2026-11-20T10:10:00"), [])
            self.assertEqual(len(mailbox.read_text(encoding="utf-8").splitlines()), 1)

    def test_posted_suppresses_e_amend_and_e_stock_too(self):
        store = NotificationStore()
        rows = [
            self._row(ref="AM-1", code="E-AMEND", timestamp="2026-11-20T09:00:00"),
            self._row(ref="AM-1", status="Posted", code="", timestamp="2026-11-20T09:05:00"),
            self._row(ref="ES-1", code="E-STOCK", timestamp="2026-11-20T09:10:00"),
            self._row(ref="ES-1", status="Posted", code="", timestamp="2026-11-20T09:11:00"),
            self._row(ref="ES-2", code="E-STOCK", timestamp="2026-11-20T09:12:00"),
            self._row(ref="ES-3", code="E-STOCK", timestamp="2026-11-20T09:13:00"),
        ]
        self.assertEqual(store.evaluate(rows, now="2026-11-20T10:00:00"), [])

    def test_different_kinds_and_empty_ids_with_attempt_id_do_not_collide(self):
        store = NotificationStore()
        rows = [
            self._row(ref="SAME-1", kind="issue", code="E-ITEM", timestamp="2026-11-20T09:00:00"),
            self._row(ref="SAME-1", kind="return", code="E-QTY", timestamp="2026-11-20T09:00:00"),
            self._row(ref="", attempt_id="att-1", kind="release", code="E-AMEND", timestamp="2026-11-20T10:00:00"),
            self._row(ref="", attempt_id="att-2", kind="release", code="E-AMEND", timestamp="2026-11-20T10:01:00"),
        ]
        emitted = store.evaluate(rows, now="2026-11-20T13:30:00")
        old_failures = [n for n in emitted if n["type"] == "old_failure"]
        e_amends = [n for n in emitted if n["type"] == "e_amend"]
        self.assertEqual(len(old_failures), 4)
        self.assertEqual(len(e_amends), 2)

    def test_notifications_config_rejects_unknown_nan_inf_bool_and_negative(self):
        bad_blocks = [
            ({"old_failure_hours": 1}, "unknown"),
            ({"old_failure_working_hours": float("nan")}, "old_failure_working_hours"),
            ({"old_failure_working_hours": float("inf")}, "old_failure_working_hours"),
            ({"old_failure_working_hours": True}, "old_failure_working_hours"),
            ({"old_failure_working_hours": -1}, "old_failure_working_hours"),
            ({"estock_threshold": True}, "estock_threshold"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            for block, expected_field in bad_blocks:
                with self.subTest(block=block):
                    cfg_path = Path(temp) / "config.json"
                    cfg_path.write_text(json.dumps({
                        "mode": "test",
                        "live_password": "",
                        "notifications": block,
                    }))
                    with self.assertRaises(ConfigError) as caught:
                        load_config(cfg_path)
                    self.assertIn(expected_field, str(caught.exception))

    def test_corrupted_state_and_mailbox_recover_with_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mailbox = root / "mailbox.jsonl"
            state = root / "state.json"
            state.write_text("{bad-json", encoding="utf-8")
            mailbox.write_text(
                json.dumps({"notification_id": "ok-1", "type": "e_amend"}) + "\n{half-written\n",
                encoding="utf-8",
            )
            store = NotificationStore(mailbox_path=mailbox, state_path=state)
            self.assertIn("ok-1", store.sent_keys)
            self.assertTrue(any("state.json" in w for w in store.warnings))
            self.assertTrue(any("mailbox.jsonl" in w for w in store.warnings))

    def test_mailbox_is_append_only_and_masks_secret_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mailbox = root / "mailbox.jsonl"
            mailbox.write_text('{"notification_id": "pre-existing"}\n', encoding="utf-8")
            store = NotificationStore(mailbox_path=mailbox)
            rows = [
                self._row(
                    ref="token=abc123",
                    work_order="WO-secret=xyz",
                    code="E-AMEND",
                    timestamp="2026-11-20T10:00:00",
                )
            ]
            emitted = store.evaluate(rows, now="2026-11-20T10:05:00")
            self.assertEqual(len(emitted), 1)
            self.assertNotIn("abc123", emitted[0]["row_id"])
            self.assertNotIn("xyz", emitted[0]["work_order"])
            self.assertIn("token=***", emitted[0]["row_id"])
            lines = mailbox.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["notification_id"], "pre-existing")

    def test_desk_integration_evaluates_real_connector_log_rows(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "log.jsonl"
            desk = server.Desk(
                log_path=str(log_path),
                notifications_config={"old_failure_working_hours": 0.0001, "work_start_hour": 0, "work_end_hour": 24, "working_weekdays": [0, 1, 2, 3, 4, 5, 6]},
            )
            desk.run("bad-item")
            # Backdate the recorded LogRow timestamp so the working-hour threshold is exceeded on next evaluation.
            desk.connector.log[-1].timestamp = "2026-11-20T09:00:00"
            desk._evaluate_notifications()
            payload = desk.payload()
            self.assertGreaterEqual(len(payload["notifications"]), 2)
            self.assertTrue((Path(temp) / "notifications.jsonl").exists())
            self.assertTrue((Path(temp) / "notifications_state.json").exists())

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

    def test_malformed_timestamps_do_not_crash_evaluate_or_desk_startup(self):
        import server
        store = NotificationStore()
        rows = [
            self._row(ref="V-1", code="E-AMEND", timestamp="2026-11-20T09:00:00"),
            self._row(ref="BAD-1", status="Dead", code="E-TIMEOUT", timestamp="20/11/2026 10:00"),
            self._row(ref="BAD-2", status="Dead", code="E-TIMEOUT", timestamp=""),
            self._row(ref="BAD-3", status="Dead", code="E-TIMEOUT", timestamp=None),
            self._row(ref="BAD-4", status="Dead", code="E-TIMEOUT", timestamp=12345),
            self._row(ref="V-2", code="E-AMEND", timestamp="2026-11-20T09:05:00"),
        ]
        emitted = store.evaluate(rows, now="2026-11-20T10:00:00")
        self.assertEqual([n["row_id"] for n in emitted], ["V-1", "V-2"])

        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "log.jsonl"
            log_path.write_text(
                json.dumps({
                    "ref": "J-BAD-TS", "work_order": "WO-2401", "kind": "issue",
                    "status": "Dead", "code": "E-TIMEOUT", "document": "",
                    "timestamp": "20/11/2026 10:00",
                }) + "\n",
                encoding="utf-8",
            )
            desk = server.Desk(log_path=str(log_path))
            desk.run("bad-item")
            desk.reset()
            self.assertEqual(desk.payload()["notifications"], [])

            with mock.patch.object(desk.notifications, "evaluate", side_effect=ValueError("bad clock")):
                desk._evaluate_notifications()
            self.assertIn("bad clock", desk.payload()["notifications_warning"])

    def test_performance_single_state_save_sent_key_skip_and_o1_working_hours(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            mailbox = Path(temp) / "mailbox.jsonl"
            store = NotificationStore(mailbox_path=mailbox, state_path=state)
            rows = [
                self._row(ref=f"P-{i}", code="E-AMEND", timestamp="2026-11-20T09:00:00")
                for i in range(5)
            ]
            with mock.patch.object(store, "_save_state", wraps=store._save_state) as save_spy:
                emitted = store.evaluate(rows, now="2026-11-20T14:00:00")
                self.assertEqual(len(emitted), 15)
                self.assertEqual(save_spy.call_count, 1)

            with mock.patch("connector.notifications._working_hours_between_validated") as wh_spy:
                self.assertEqual(store.evaluate(rows, now="2026-11-20T15:00:00"), [])
                self.assertEqual(wh_spy.call_count, 0)

        # 52 weeks (364 days) of Mon-Fri 09:00-17:00 = 52 * 40 = 2080.0 hours
        self.assertEqual(
            working_hours_between("2025-11-20T09:00:00", "2026-11-19T09:00:00"),
            2080.0,
        )

    def test_periodic_evaluation_from_payload_without_user_action(self):
        import server
        import threading
        with tempfile.TemporaryDirectory() as temp:
            desk = server.Desk(
                log_path=str(Path(temp) / "log.jsonl"),
                notifications_config={"old_failure_working_hours": 1.0, "work_start_hour": 0, "work_end_hour": 24, "working_weekdays": [0, 1, 2, 3, 4, 5, 6]},
            )
            desk.run("bad-item")
            desk.connector.log[-1].timestamp = "2026-11-20T09:00:00"

            # Within 60s interval, payload() does not re-evaluate
            self.assertEqual(desk.payload()["notifications"], [])

            # Advance past interval and invoke payload() concurrently
            desk._last_notification_eval -= 61.0
            results = []
            threads = [
                threading.Thread(target=lambda: results.append(desk.payload()["notifications"]))
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(desk.notifications.notices), 2)
            self.assertTrue(all(len(r) == 2 for r in results))

    def test_mailbox_and_state_write_failures_surface_once_and_recover(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            desk = server.Desk(log_path=str(Path(temp) / "log.jsonl"))
            desk.run("bad-item")
            desk.connector.log[-1].timestamp = "2026-11-20T09:00:00"
            with mock.patch.object(desk.notifications, "_append_mailbox", side_effect=OSError("disk full")):
                desk._evaluate_notifications()
                desk._evaluate_notifications()
            warnings = [w for w in desk.notifications.warnings if "disk full" in w]
            self.assertEqual(len(warnings), 1)
            self.assertIn("disk full", desk.payload()["notifications_warning"])

            # Once disk write succeeds, notices are emitted and the transient error clears
            desk._evaluate_notifications()
            self.assertEqual(len(desk.payload()["notifications"]), 2)
            self.assertEqual(desk.payload()["notifications_warning"], "")

    def test_estock_state_shape_validation_and_pipe_in_work_order(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            state.write_text(json.dumps({
                "sent_keys": [],
                "estock_events": {
                    "bad": ["x"],
                    "WO|2401|2026-11-20": ["e1", 123, {"bad": True}],
                },
            }), encoding="utf-8")
            store = NotificationStore(state_path=state)
            self.assertIn(("WO|2401", "2026-11-20"), store.estock_events)
            self.assertEqual(store.estock_events[("WO|2401", "2026-11-20")], ["e1", "123"])
            self.assertTrue(any("bad" in w for w in store.warnings))

            # Subsequent evaluation and save_state must not crash
            emitted = store.evaluate(
                [self._row(ref="J-PIPE", work_order="WO|2401", code="E-STOCK", timestamp="2026-11-20T09:30:00")],
                now="2026-11-20T10:00:00",
            )
            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0]["type"], "e_stock_repeated")

    def test_posted_suppression_respects_log_order(self):
        store = NotificationStore()
        # Posted before failure must NOT suppress the later failure
        rows = [
            self._row(ref="ORD-1", status="Posted", code="", timestamp="2026-11-20T09:00:00"),
            self._row(ref="ORD-1", status="Business Rejected", code="E-AMEND", timestamp="2026-11-20T10:00:00"),
        ]
        emitted = store.evaluate(rows, now="2026-11-20T10:05:00")
        self.assertEqual(len(emitted), 1)

        # Failure -> Posted -> new failure suppresses only the first failure
        store2 = NotificationStore()
        rows2 = [
            self._row(ref="ORD-2", status="Business Rejected", code="E-AMEND", timestamp="2026-11-20T09:00:00"),
            self._row(ref="ORD-2", status="Posted", code="", timestamp="2026-11-20T09:05:00"),
            self._row(ref="ORD-2", status="Business Rejected", code="E-AMEND", timestamp="2026-11-20T10:00:00"),
        ]
        emitted2 = store2.evaluate(rows2, now="2026-11-20T10:05:00")
        self.assertEqual(len(emitted2), 1)
        self.assertEqual(emitted2[0]["timestamp"], "2026-11-20T10:00:00")

    def test_estock_counts_distinct_operations_with_same_repeat_key_but_not_replays(self):
        store = NotificationStore()
        rows = [
            self._row(ref="J-BIG", code="E-STOCK", timestamp="2026-11-20T09:00:00", replay_of=-1),
            self._row(ref="J-BIG", code="E-STOCK", timestamp="2026-11-20T09:01:00", replay_of=-1),
            # Replay of row 0 must not count as a 3rd distinct operation
            self._row(ref="J-BIG", code="E-STOCK", timestamp="2026-11-20T09:02:00", replay_of=0),
        ]
        self.assertEqual(store.evaluate(rows, now="2026-11-20T09:05:00"), [])
        rows.append(self._row(ref="J-BIG", code="E-STOCK", timestamp="2026-11-20T09:03:00", replay_of=-1))
        emitted = store.evaluate(rows, now="2026-11-20T09:05:00")
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["type"], "e_stock_repeated")

    def test_payload_limits_notifications_to_newest_100_and_includes_total_count(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            desk = server.Desk(log_path=str(Path(temp) / "log.jsonl"))
            rows = [
                self._row(ref=f"N-{i}", code="E-AMEND", timestamp="2026-11-20T09:00:00")
                for i in range(120)
            ]
            desk.notifications.evaluate(rows, now="2026-11-20T09:05:00")
            payload = desk.payload()
            self.assertEqual(payload["notifications_count"], 120)
            self.assertEqual(len(payload["notifications"]), 100)
            self.assertEqual(payload["notifications"][0]["row_id"], "N-20")
            self.assertEqual(payload["notifications"][-1]["row_id"], "N-119")
            self.assertEqual(len(desk.notifications.notices), 120)
            desk.reset()
            self.assertEqual(desk.payload()["notifications_count"], 0)
            self.assertEqual(desk.payload()["notifications"], [])


if __name__ == "__main__":
    unittest.main()
