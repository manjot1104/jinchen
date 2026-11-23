"""POC notification rules for old failures, E-AMEND, and repeated E-STOCK.

Notifications contain safe metadata only (never raw payload text or secrets)
and are delivered to an in-memory list and optional atomic JSONL mailbox file.
"""

from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading

from connector.poc import ConfigError

DEFAULT_NOTIFICATIONS = {
    "old_failure_working_hours": 4.0,
    "work_start_hour": 9,
    "work_end_hour": 17,
    "working_weekdays": [0, 1, 2, 3, 4],
    "estock_threshold": 3,
}

NOTIFICATIONS_SENTENCE = "config.json notifications settings are invalid."
_FILE_LOCK = threading.Lock()


def validate_notifications_config(block):
    """Validate optional notifications config block and return normalized settings."""
    if block is None:
        return dict(DEFAULT_NOTIFICATIONS)
    if not isinstance(block, dict):
        raise ConfigError(NOTIFICATIONS_SENTENCE)

    cfg = dict(DEFAULT_NOTIFICATIONS)
    cfg.update(block)

    threshold_hours = cfg["old_failure_working_hours"]
    if isinstance(threshold_hours, bool) or not isinstance(threshold_hours, (int, float)) or threshold_hours <= 0:
        raise ConfigError(NOTIFICATIONS_SENTENCE)

    start_hour = cfg["work_start_hour"]
    end_hour = cfg["work_end_hour"]
    for hour in (start_hour, end_hour):
        if isinstance(hour, bool) or not isinstance(hour, int):
            raise ConfigError(NOTIFICATIONS_SENTENCE)
    if not (0 <= start_hour < end_hour <= 24):
        raise ConfigError(NOTIFICATIONS_SENTENCE)

    weekdays = cfg["working_weekdays"]
    if not isinstance(weekdays, (list, tuple)) or not weekdays:
        raise ConfigError(NOTIFICATIONS_SENTENCE)
    normalized_days = []
    for day in weekdays:
        if isinstance(day, bool) or not isinstance(day, int) or not (0 <= day <= 6):
            raise ConfigError(NOTIFICATIONS_SENTENCE)
        if day not in normalized_days:
            normalized_days.append(day)

    estock_threshold = cfg["estock_threshold"]
    if isinstance(estock_threshold, bool) or not isinstance(estock_threshold, int) or estock_threshold < 1:
        raise ConfigError(NOTIFICATIONS_SENTENCE)

    return {
        "old_failure_working_hours": float(threshold_hours),
        "work_start_hour": start_hour,
        "work_end_hour": end_hour,
        "working_weekdays": normalized_days,
        "estock_threshold": estock_threshold,
    }


def parse_timestamp(value):
    """Parse an ISO-8601 string or return a datetime instance."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    raise ValueError("Timestamp must be a datetime or ISO-8601 string.")


def working_hours_between(start, end, config=None):
    """Calculate working hours elapsed between start and end."""
    cfg = validate_notifications_config(config)
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if end_dt <= start_dt:
        return 0.0

    work_days = set(cfg["working_weekdays"])
    start_hour = cfg["work_start_hour"]
    end_hour = cfg["work_end_hour"]
    tz = start_dt.tzinfo

    total_seconds = 0.0
    current_day = start_dt.date()
    last_day = end_dt.date()

    while current_day <= last_day:
        if current_day.weekday() in work_days:
            day_start = datetime.combine(current_day, time(start_hour, 0), tzinfo=tz)
            if end_hour == 24:
                day_end = datetime.combine(current_day + timedelta(days=1), time(0, 0), tzinfo=tz)
            else:
                day_end = datetime.combine(current_day, time(end_hour, 0), tzinfo=tz)

            overlap_start = max(start_dt, day_start)
            overlap_end = min(end_dt, day_end)
            if overlap_end > overlap_start:
                total_seconds += (overlap_end - overlap_start).total_seconds()
        current_day += timedelta(days=1)

    return total_seconds / 3600.0


def _atomic_write_text(path, content):
    """Write text atomically to path via a temp file in the same directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        with _FILE_LOCK:
            os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _row_field(row, name, default=""):
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


class NotificationStore:
    """Evaluate connector rows against notification rules with atomic persistence."""

    def __init__(self, mailbox_path=None, state_path=None, config=None):
        self.config = validate_notifications_config(config)
        self.mailbox_path = Path(mailbox_path) if mailbox_path is not None else None
        self.state_path = Path(state_path) if state_path is not None else None
        self.notices = []
        self.sent_keys = set()
        self.estock_events = {}  # (work_order, business_date) -> list of unique event keys
        self._load_state()

    def _load_state(self):
        if self.state_path is not None and self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.sent_keys = set(data.get("sent_keys", []))
            self.estock_events = {
                tuple(k.split("|", 1)): list(v)
                for k, v in data.get("estock_events", {}).items()
            }
        if self.mailbox_path is not None and self.mailbox_path.exists():
            loaded = []
            for line in self.mailbox_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    loaded.append(json.loads(line))
            self.notices = loaded

    def _save_state(self):
        if self.state_path is None:
            return
        payload = {
            "sent_keys": sorted(self.sent_keys),
            "estock_events": {
                f"{wo}|{day}": events for (wo, day), events in sorted(self.estock_events.items())
            },
        }
        _atomic_write_text(self.state_path, json.dumps(payload, indent=2) + "\n")

    def _append_mailbox(self, notice):
        if self.mailbox_path is None:
            return
        existing = ""
        if self.mailbox_path.exists():
            existing = self.mailbox_path.read_text(encoding="utf-8")
        line = json.dumps(notice) + "\n"
        _atomic_write_text(self.mailbox_path, existing + line)

    def _emit(self, dedup_key, notice):
        if dedup_key in self.sent_keys:
            return None
        # Deliver to mailbox first; only mark sent after delivery succeeds.
        self._append_mailbox(notice)
        self.sent_keys.add(dedup_key)
        self.notices.append(notice)
        self._save_state()
        return notice

    def evaluate(self, rows, now=None):
        """Evaluate rows and emit any new notifications without duplicates."""
        if not rows:
            return []
        current_time = parse_timestamp(now) if now is not None else datetime.now()
        emitted = []

        # Track rows resolved by a later Posted row for the same (ref, work_order, kind).
        posted_keys = set()
        for row in rows:
            status = str(_row_field(row, "status", "")).strip()
            if status.lower() == "posted":
                ref = str(_row_field(row, "ref", "") or _row_field(row, "id", ""))
                wo = str(_row_field(row, "work_order", ""))
                kind = str(_row_field(row, "kind", ""))
                posted_keys.add((ref, wo, kind))

        for row in rows:
            status = str(_row_field(row, "status", "")).strip()
            if status.lower() == "posted":
                continue

            ref = str(_row_field(row, "ref", "") or _row_field(row, "id", ""))
            wo = str(_row_field(row, "work_order", ""))
            kind = str(_row_field(row, "kind", ""))
            code = str(_row_field(row, "code", "")).strip()
            ts_raw = _row_field(row, "timestamp", "")
            if not ts_raw:
                continue
            row_dt = parse_timestamp(ts_raw)
            ts_iso = row_dt.isoformat()

            # AC-2: E-AMEND notifies Planner once per row.
            if code == "E-AMEND":
                key = f"e_amend|{ref}|{wo}|Planner"
                notice = {
                    "notification_id": key,
                    "type": "e_amend",
                    "recipient": "Planner",
                    "code": "E-AMEND",
                    "row_id": ref,
                    "work_order": wo,
                    "timestamp": ts_iso,
                    "message": "Amendment failure requires planner review.",
                }
                sent = self._emit(key, notice)
                if sent is not None:
                    emitted.append(sent)

            # AC-3: 3 distinct E-STOCK failures per (work_order, business_date) notify Stores.
            if code == "E-STOCK" and wo:
                day = row_dt.date().isoformat()
                event_id = str(_row_field(row, "event_id", "") or ref or ts_iso)
                bucket = self.estock_events.setdefault((wo, day), [])
                if event_id not in bucket:
                    bucket.append(event_id)
                    self._save_state()
                if len(bucket) >= self.config["estock_threshold"]:
                    key = f"e_stock_repeated|{wo}|{day}|Stores"
                    notice = {
                        "notification_id": key,
                        "type": "e_stock_repeated",
                        "recipient": "Stores",
                        "code": "E-STOCK",
                        "row_id": ref,
                        "work_order": wo,
                        "timestamp": ts_iso,
                        "message": "Repeated short-stock failures reached daily order threshold.",
                    }
                    sent = self._emit(key, notice)
                    if sent is not None:
                        emitted.append(sent)

            # AC-1: Unresolved failed row older than configured working hours notifies Support and Stores Manager.
            if (ref, wo, kind) in posted_keys:
                continue
            elapsed = working_hours_between(row_dt, current_time, self.config)
            if elapsed >= self.config["old_failure_working_hours"]:
                for recipient in ("Support", "Stores Manager"):
                    key = f"old_failure|{ref}|{wo}|{recipient}"
                    notice = {
                        "notification_id": key,
                        "type": "old_failure",
                        "recipient": recipient,
                        "code": code,
                        "row_id": ref,
                        "work_order": wo,
                        "timestamp": ts_iso,
                        "message": "Failed message exceeded working-hour threshold.",
                    }
                    sent = self._emit(key, notice)
                    if sent is not None:
                        emitted.append(sent)

        return emitted
