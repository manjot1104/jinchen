"""POC notification rules for old failures, E-AMEND, and repeated E-STOCK.

Notifications contain safe metadata only (never raw payload text or secrets)
and are delivered to an in-memory list and optional append-only JSONL mailbox file.
"""

from datetime import datetime, time, timedelta
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

from connector.poc import ConfigError, MASK, SECRET_WORDS, _scrub

DEFAULT_NOTIFICATIONS = {
    "old_failure_working_hours": 4.0,
    "work_start_hour": 9,
    "work_end_hour": 17,
    "working_weekdays": [0, 1, 2, 3, 4],
    "estock_threshold": 3,
}

FAILURE_STATUSES = {"Business Rejected", "Technical Failed", "Dead"}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(w) for w in SECRET_WORDS) + r")\s*([:=])\s*([^\s,;]+)"
)
_FILE_LOCK = threading.Lock()


def _mask_text(value):
    """Scrub secret-looking values from strings or nested structures."""
    scrubbed = _scrub(value)
    text = str(scrubbed if scrubbed is not None else "")
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)


def validate_notifications_config(block):
    """Validate optional notifications config block and return normalized settings."""
    if block is None:
        return dict(DEFAULT_NOTIFICATIONS)
    if not isinstance(block, dict):
        raise ConfigError("config.json notifications must be a JSON object.")

    unknown = sorted(set(block) - set(DEFAULT_NOTIFICATIONS))
    if unknown:
        raise ConfigError(f"config.json notifications has unknown setting: {unknown[0]}.")

    cfg = dict(DEFAULT_NOTIFICATIONS)
    cfg.update(block)

    threshold_hours = cfg["old_failure_working_hours"]
    if (
        isinstance(threshold_hours, bool)
        or not isinstance(threshold_hours, (int, float))
        or not math.isfinite(threshold_hours)
        or threshold_hours <= 0
    ):
        raise ConfigError(
            "config.json notifications old_failure_working_hours must be a finite number above 0."
        )

    start_hour = cfg["work_start_hour"]
    end_hour = cfg["work_end_hour"]
    for hour in (start_hour, end_hour):
        if isinstance(hour, bool) or not isinstance(hour, int):
            raise ConfigError(
                "config.json notifications work_start_hour and work_end_hour must be integers from 0 to 24."
            )
    if not (0 <= start_hour < end_hour <= 24):
        raise ConfigError(
            "config.json notifications work_start_hour must be less than work_end_hour within 0..24."
        )

    weekdays = cfg["working_weekdays"]
    if not isinstance(weekdays, (list, tuple)) or not weekdays:
        raise ConfigError(
            "config.json notifications working_weekdays must be a non-empty list of integers from 0 to 6."
        )
    normalized_days = []
    for day in weekdays:
        if isinstance(day, bool) or not isinstance(day, int) or not (0 <= day <= 6):
            raise ConfigError(
                "config.json notifications working_weekdays must contain integers from 0 to 6."
            )
        if day not in normalized_days:
            normalized_days.append(day)

    estock_threshold = cfg["estock_threshold"]
    if isinstance(estock_threshold, bool) or not isinstance(estock_threshold, int) or estock_threshold < 1:
        raise ConfigError(
            "config.json notifications estock_threshold must be an integer of at least 1."
        )

    return {
        "old_failure_working_hours": float(threshold_hours),
        "work_start_hour": start_hour,
        "work_end_hour": end_hour,
        "working_weekdays": normalized_days,
        "estock_threshold": estock_threshold,
    }


def _to_local_naive(dt):
    """Normalize aware datetimes to local naive time so naive/aware comparisons never crash."""
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def parse_timestamp(value):
    """Parse an ISO-8601 string or datetime into a local naive datetime."""
    if isinstance(value, datetime):
        return _to_local_naive(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Timestamp string is empty.")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return _to_local_naive(datetime.fromisoformat(text))
    raise ValueError("Timestamp must be a datetime or ISO-8601 string.")


def _safe_parse_timestamp(value):
    """Parse a timestamp tolerantly; return None for malformed or missing values."""
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError):
        return None


def _day_overlap_seconds(day_date, start_dt, end_dt, start_hour, end_hour):
    day_start = datetime.combine(day_date, time(start_hour, 0))
    if end_hour == 24:
        day_end = datetime.combine(day_date + timedelta(days=1), time(0, 0))
    else:
        day_end = datetime.combine(day_date, time(end_hour, 0))
    overlap_start = max(start_dt, day_start)
    overlap_end = min(end_dt, day_end)
    if overlap_end > overlap_start:
        return (overlap_end - overlap_start).total_seconds()
    return 0.0


def _working_hours_between_validated(start_dt, end_dt, cfg):
    """Calculate working hours in O(1) by counting whole weeks plus boundary days."""
    if end_dt <= start_dt:
        return 0.0

    work_days = set(cfg["working_weekdays"])
    start_hour = cfg["work_start_hour"]
    end_hour = cfg["work_end_hour"]
    hours_per_day = end_hour - start_hour

    first_day = start_dt.date()
    last_day = end_dt.date()

    if first_day == last_day:
        if first_day.weekday() not in work_days:
            return 0.0
        return _day_overlap_seconds(first_day, start_dt, end_dt, start_hour, end_hour) / 3600.0

    total_seconds = 0.0
    if first_day.weekday() in work_days:
        total_seconds += _day_overlap_seconds(first_day, start_dt, end_dt, start_hour, end_hour)
    if last_day.weekday() in work_days:
        total_seconds += _day_overlap_seconds(last_day, start_dt, end_dt, start_hour, end_hour)

    mid_start = first_day + timedelta(days=1)
    mid_end = last_day - timedelta(days=1)
    if mid_end >= mid_start:
        mid_days = (mid_end - mid_start).days + 1
        full_weeks, rem_days = divmod(mid_days, 7)
        total_seconds += full_weeks * len(work_days) * hours_per_day * 3600.0
        for offset in range(rem_days):
            if (mid_start + timedelta(days=offset)).weekday() in work_days:
                total_seconds += hours_per_day * 3600.0

    return total_seconds / 3600.0


def working_hours_between(start, end, config=None):
    """Calculate working hours elapsed between start and end."""
    cfg = validate_notifications_config(config)
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    return _working_hours_between_validated(start_dt, end_dt, cfg)


def _atomic_write_text(path, content):
    """Write text atomically to path via a temp file in the same directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(tmp_fd, "w", encoding="utf-8")
        except BaseException:
            os.close(tmp_fd)
            raise
        with handle:
            handle.write(content)
        with _FILE_LOCK:
            os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def _row_field(row, name, default=""):
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _row_identity(row, ts_iso=""):
    """Build a stable key including kind/operation, falling back to attempt_id when ref is empty."""
    repeat_key = str(_row_field(row, "repeat_key", "") or "")
    if repeat_key:
        return repeat_key
    ref = str(_row_field(row, "ref", "") or _row_field(row, "id", ""))
    wo = str(_row_field(row, "work_order", ""))
    kind = str(_row_field(row, "kind", ""))
    if ref:
        if kind == "confirm":
            op = _row_field(row, "operation", None)
            if op is None:
                received = _row_field(row, "received", {})
                if isinstance(received, dict):
                    op = received.get("operation")
            if op not in (None, False, ""):
                return f"{ref}|{wo}|op{op}"
        return f"{ref}|{wo}|{kind}"
    attempt_id = str(_row_field(row, "attempt_id", "") or "")
    if attempt_id:
        return f"attempt:{attempt_id}|{wo}|{kind}"
    return f"ts:{ts_iso}|{wo}|{kind}"


def _estock_event_key(row, row_key, ts_iso, idx):
    """Identify a distinct E-STOCK business operation; return None for replays."""
    explicit_id = _row_field(row, "event_id", "")
    if explicit_id:
        return str(explicit_id)
    replay_of = _row_field(row, "replay_of", -1)
    if isinstance(replay_of, int) and replay_of >= 0:
        return None
    return f"op:{row_key}|{ts_iso}|{idx}"


class NotificationStore:
    """Evaluate connector rows against notification rules with atomic persistence."""

    def __init__(self, mailbox_path=None, state_path=None, config=None):
        self.config = validate_notifications_config(config)
        self.mailbox_path = Path(mailbox_path) if mailbox_path is not None else None
        self.state_path = Path(state_path) if state_path is not None else None
        self.notices = []
        self.sent_keys = set()
        self.estock_events = {}  # (work_order, business_date) -> list of unique event keys
        self.warnings = []
        self._load_state()

    def _load_state(self):
        if self.state_path is not None and self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("State must be a JSON object.")

                raw_sent = data.get("sent_keys", [])
                if not isinstance(raw_sent, list):
                    raise ValueError("sent_keys must be a list.")
                self.sent_keys = {str(k) for k in raw_sent if isinstance(k, str) and k}

                raw_estock = data.get("estock_events", {})
                if not isinstance(raw_estock, dict):
                    raise ValueError("estock_events must be a JSON object.")
                parsed_estock = {}
                for raw_key, raw_events in raw_estock.items():
                    if not isinstance(raw_key, str) or "|" not in raw_key:
                        self.warnings.append(
                            f"Skipped malformed E-STOCK state key in {self.state_path.name}: {raw_key!r}"
                        )
                        continue
                    wo, day = raw_key.rsplit("|", 1)
                    if not wo or not day or not isinstance(raw_events, list):
                        self.warnings.append(
                            f"Skipped malformed E-STOCK state entry in {self.state_path.name}: {raw_key!r}"
                        )
                        continue
                    valid_events = [
                        str(e)
                        for e in raw_events
                        if isinstance(e, (str, int, float)) and not isinstance(e, bool) and str(e)
                    ]
                    if len(valid_events) != len(raw_events):
                        self.warnings.append(
                            f"Skipped malformed E-STOCK event value in {self.state_path.name} for {raw_key!r}"
                        )
                    parsed_estock[(wo, day)] = valid_events
                self.estock_events = parsed_estock
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, AttributeError, OSError) as exc:
                self.sent_keys = set()
                self.estock_events = {}
                self.warnings.append(
                    f"Recovered from damaged notification state file {self.state_path.name}: {exc}"
                )

        if self.mailbox_path is not None and self.mailbox_path.exists():
            loaded = []
            try:
                lines = self.mailbox_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError) as exc:
                self.warnings.append(
                    f"Could not read notification mailbox {self.mailbox_path.name}: {exc}"
                )
                lines = []
            for idx, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError("Mailbox line must be a JSON object.")
                    loaded.append(item)
                    nid = item.get("notification_id")
                    if nid:
                        self.sent_keys.add(nid)
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    self.warnings.append(
                        f"Skipped damaged line {idx} in notification mailbox {self.mailbox_path.name}: {exc}"
                    )
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
        self.mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(notice) + "\n"
        with _FILE_LOCK:
            with open(self.mailbox_path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()

    def _emit(self, dedup_key, notice):
        if dedup_key in self.sent_keys:
            return None
        # Deliver to mailbox first; only mark sent after delivery succeeds.
        self._append_mailbox(notice)
        self.sent_keys.add(dedup_key)
        self.notices.append(notice)
        return notice

    def evaluate(self, rows, now=None):
        """Evaluate rows and emit any new notifications without duplicates."""
        if not rows:
            return []
        current_time = parse_timestamp(now) if now is not None else datetime.now()
        emitted = []
        state_dirty = False

        # Track the last Posted log index per identity so only later Posted rows suppress failures.
        last_posted_idx = {}
        for idx, row in enumerate(rows):
            status = str(_row_field(row, "status", "")).strip()
            if status == "Posted":
                last_posted_idx[_row_identity(row)] = idx

        try:
            for idx, row in enumerate(rows):
                status = str(_row_field(row, "status", "")).strip()
                if status not in FAILURE_STATUSES:
                    continue

                ts_raw = _row_field(row, "timestamp", "")
                row_dt = _safe_parse_timestamp(ts_raw)
                if row_dt is None:
                    continue
                ts_iso = row_dt.isoformat()

                row_key = _row_identity(row, ts_iso=ts_iso)
                if last_posted_idx.get(row_key, -1) > idx:
                    continue

                ref = _mask_text(_row_field(row, "ref", "") or _row_field(row, "id", ""))
                wo = _mask_text(_row_field(row, "work_order", ""))
                code = str(_row_field(row, "code", "")).strip()

                # AC-2: E-AMEND notifies Planner once per row.
                if code == "E-AMEND":
                    key = f"e_amend|{row_key}|Planner"
                    if key not in self.sent_keys:
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
                            state_dirty = True

                # AC-3: Repeated E-STOCK failures per (work_order, business_date) notify Stores.
                if code == "E-STOCK" and wo:
                    day = row_dt.date().isoformat()
                    event_id = _estock_event_key(row, row_key, ts_iso, idx)
                    bucket = self.estock_events.setdefault((wo, day), [])
                    if event_id is not None and event_id not in bucket:
                        bucket.append(event_id)
                        state_dirty = True
                    if len(bucket) >= self.config["estock_threshold"]:
                        key = f"e_stock_repeated|{wo}|{day}|Stores"
                        if key not in self.sent_keys:
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
                                state_dirty = True

                # AC-1: Unresolved failed row older than working hours notifies Support & Stores Manager.
                old_keys = (
                    f"old_failure|{row_key}|Support",
                    f"old_failure|{row_key}|Stores Manager",
                )
                if not (old_keys[0] in self.sent_keys and old_keys[1] in self.sent_keys):
                    elapsed = _working_hours_between_validated(row_dt, current_time, self.config)
                    if elapsed >= self.config["old_failure_working_hours"]:
                        for recipient, key in zip(("Support", "Stores Manager"), old_keys):
                            if key in self.sent_keys:
                                continue
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
                                state_dirty = True
        finally:
            if state_dirty:
                self._save_state()

        return emitted
