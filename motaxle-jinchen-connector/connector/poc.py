"""Stand-in for the Jinchen practical POC.

ERPNext is represented by an in-memory ledger. Jinchen is a JSON message.
No network call is made. Field names are the POC contract, not a signed API.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path


RAW = "Raw Material Store"
WIP = "Forging WIP"
HOLD = "Final QC Hold"
FG = "Finished Goods Store"
REJECTION = "Rejection Store"
REWORK = "Rework Store"
SCRAP = "Scrap Yard"

REASONS = {
    "forge-crack": "rejection",
    "rework-bend": "rework",
    "scale": "scrap",
}
# Weight rule (US-081). The desk reads it from config.json (see
# connector.inbox.load_config); these are the values a bare Connector() uses.
DEFAULT_WEIGHT = {"kg_per_piece": 1.20, "loss_allowance": 0.05}

ORDER_QTY = 100  # WO-2401 is an order for 100 pieces of CR-100.
WORK_ORDER_KINDS = ("hold", "resume", "cancel")
CLOSED_KINDS = ("issue", "return", "confirm", "disposition", "receipt")

DECISION_WAREHOUSE = {
    "rejection": REJECTION,
    "rework": REWORK,
    "scrap": SCRAP,
}


class ConfigError(Exception):
    pass


NUMBER_FIELDS = ("qty", "completed", "rejected", "rework", "scrap")
CONFIRM_FIELDS = ("completed", "rejected", "rework", "scrap")


def _number(value):
    """A float for an int, a float, or a numeric string. None for anything else.

    True and False are not numbers here. A blank string is not a number.
    Non-finite numbers (NaN, Inf, -Inf) are refused.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            val = float(value)
            if not math.isfinite(val):
                return None
            return val
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            val = float(value.strip())
            if not math.isfinite(val):
                return None
            return val
        except (OverflowError, ValueError):
            return None
    return None


def _confirm_numbers(message):
    """Return tuple of (completed, rejected, rework, scrap) as floats, or None if invalid."""
    numbers = []
    for k in CONFIRM_FIELDS:
        raw_val = message.get(k)
        if raw_val is None:
            numbers.append(0.0)
        else:
            num = _number(raw_val)
            if num is None:
                return None
            numbers.append(float(num))
    return tuple(numbers)


def _operation(message):
    """Normalize operation to an integer if present and valid.

    Returns None if not present or None.
    Returns False if present but cannot be parsed as a route operation integer.
    """
    if "operation" not in message or message["operation"] is None:
        return None
    val = message["operation"]
    if isinstance(val, bool):
        return False
    num = _number(val)
    try:
        if num is None or num != int(num):
            return False
        return int(num)
    except (OverflowError, ValueError):
        return False


def require_test_mode(mode, live_password):
    if mode == "test" and live_password:
        raise ConfigError("Test mode refuses to start when a live password is set.")
    if mode not in ("test", "live"):
        raise ConfigError("Mode must be test or live.")


@dataclass
class Entry:
    name: str
    kind: str
    ref: str
    work_order: str
    item: str
    qty: float
    uom: str
    batch: str
    source: str
    target: str
    against: str = ""


# US-025: a log row's status is always one of these seven.
LOG_STATUSES = ("Queued", "Sent", "Posted", "Business Rejected", "Technical Failed", "Dead", "Replayed")
# Messages that go from ERPNext to Jinchen (M3). Everything else comes from Jinchen.
OUTBOUND_KINDS = ("hold", "resume", "cancel", "release")
# Fields copied from the message into "received", as they arrived.
RECEIVED_FIELDS = ("item", "qty", "uom", "batch", "serial", "operation",
                   "completed", "rejected", "rework", "scrap")
# A key holding any of these words is never stored in the log.
SECRET_WORDS = ("password", "token", "secret", "api_key", "apikey")
MASK = "***"


def _scrub(value):
    """A copy of value with every secret-looking key's value replaced by ***."""
    if isinstance(value, dict):
        return {
            key: (MASK if any(word in str(key).lower() for word in SECRET_WORDS) else _scrub(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


def _repeat_key(ref, work_order, kind, message):
    """The key a repeat is checked on: id, work order and kind (operation for a confirmation)."""
    if not ref:
        return ""
    if kind == "confirm":
        operation = _operation(message or {})
        if operation not in (None, False):
            return "%s|%s|op%s" % (ref, work_order, operation)
    return "%s|%s|%s" % (ref, work_order, kind)


@dataclass
class LogRow:
    ref: str
    work_order: str
    kind: str
    status: str
    code: str
    document: str
    quantities: dict = field(default_factory=dict)
    attempt_id: str = ""
    entry: dict = field(default_factory=dict)
    # US-025: the rest of the twelve log fields (see docs/message-log.md).
    direction: str = ""
    repeat_key: str = ""
    received: dict = field(default_factory=dict)
    posted: dict = field(default_factory=dict)
    attempts: int = 1
    payload: dict = field(default_factory=dict)
    # US-029: when the attempt happened (local time, ISO, to the second), and
    # for a replay, the number of the log row it replays (-1 when it is not one).
    timestamp: str = ""
    replay_of: int = -1


@dataclass
class Result:
    status: str
    code: str
    document: str
    message: str


@dataclass
class Ledger:
    bins: dict = field(default_factory=dict)
    entries: list = field(default_factory=list)
    capa_rows: list = field(default_factory=list)
    _n: int = 0
    on_submit: object = None

    def open_capa(self, ref, work_order, reason):
        self.capa_rows.append((ref, work_order, reason))

    @property
    def capa(self):
        return len(self.capa_rows)

    def qty(self, warehouse, item, batch=""):
        return self.bins.get((warehouse, item, batch), 0.0)

    def add(self, warehouse, item, batch, qty):
        key = (warehouse, item, batch)
        self.bins[key] = self.bins.get(key, 0.0) + qty

    def submit(self, kind, ref, work_order, item, qty, uom, batch, source, target, against=""):
        self._n += 1
        entry = Entry(
            "STE-%04d" % self._n, kind, ref, work_order, item, qty, uom, batch, source, target,
            against=against,
        )
        if source:
            self.add(source, item, batch, -qty)
        if target:
            self.add(target, item, batch, qty)
        self.entries.append(entry)
        if callable(self.on_submit):
            try:
                self.on_submit(entry)
            except TypeError:
                try:
                    self.on_submit(self._n)
                except Exception:
                    pass
            except Exception:
                pass
        return entry

    def find(self, ref, work_order, kind):
        """The repeat key is the message id, the work order, and the kind.

        A return may reuse an issue id. Hold, return, scrap, and receipt do
        not share one another's keys.
        """
        for entry in self.entries:
            if entry.ref == ref and entry.work_order == work_order and entry.kind == kind:
                return entry
        return None


LOG_FILE_ERROR_MESSAGE = "The log file could not be written."


CONNECTOR_WEIGHT_SENTENCE = (
    "Connector weight needs kg_per_piece above 0 and loss_allowance from 0 to 1."
)


def _validate_connector_weight(block):
    if block is None:
        return dict(DEFAULT_WEIGHT)
    if not isinstance(block, dict):
        raise ConfigError(CONNECTOR_WEIGHT_SENTENCE)
    weight = dict(DEFAULT_WEIGHT)
    weight.update(block)
    per_piece = weight["kg_per_piece"]
    allowance = weight["loss_allowance"]
    for value in (per_piece, allowance):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(CONNECTOR_WEIGHT_SENTENCE)
    if per_piece <= 0 or not 0 <= allowance <= 1:
        raise ConfigError(CONNECTOR_WEIGHT_SENTENCE)
    return {"kg_per_piece": float(per_piece), "loss_allowance": float(allowance)}


class Connector:
    """Connector processes Jinchen messages against ERPNext stock.
    Note: not thread-safe; use external synchronization for concurrent calls.
    """
    def __init__(self, mode="test", live_password="", weight=None, log_path=None, results_dir=None, retry_config=None, sleeper=None):
        from connector.retry import validate_retry_config
        require_test_mode(mode, live_password)
        self.mode = mode
        self.weight = _validate_connector_weight(weight)
        self.retry_config = validate_retry_config(retry_config)
        self.sleeper = sleeper or time.sleep
        self.log_path = log_path
        self.ledger_path = Path(log_path).with_name("ledger.jsonl") if log_path else None
        self.counter_path = Path(log_path).with_name(".ste_counter") if log_path else None
        self.results_dir = results_dir
        if self.log_path:
            Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        self.order_state = {}
        self.order_events = []
        self.ledger = Ledger()
        self.ledger.on_submit = self._on_ledger_submit
        self.log = []
        self._confirm_store = {}
        self.output_batches = {}
        self.inspection_required = True
        self.log_file_error = False
        self._attempt_id = ""
        self._message = None
        self._last_queued_attempts = {}
        # US-029: the clock the log rows are stamped with. Tests replace it.
        self.clock = datetime.now
        self._replay_of = -1
        self._seed()
        self._load_ste_counter()
        self._load_ledger()
        self._load_log()
        self._scan_results()
        if self.ledger._n > 0:
            self._persist_ste_counter(self.ledger._n)

    @property
    def confirmations(self):
        """Stored confirmations as (work_order, operation, numbers), in arrival order.

        Read-only: a confirmation is stored only by _confirm, keyed by
        (id, work_order, operation) in _confirm_store.
        """
        return [(wo, op, nums) for (ref, wo, op), nums in self._confirm_store.items()]

    @property
    def _confirm_quantities(self):
        return {k: dict(zip(CONFIRM_FIELDS, v)) for k, v in self._confirm_store.items()}

    def _seed(self):
        self.ledger.add(RAW, "ST-20", "H-441", 500.0)

    def _on_ledger_submit(self, entry_or_n):
        if isinstance(entry_or_n, Entry):
            entry = entry_or_n
            if self.ledger_path:
                try:
                    self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.ledger_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(asdict(entry)) + "\n")
                except OSError:
                    pass
            self._persist_ste_counter(self.ledger._n)
        else:
            self._persist_ste_counter(entry_or_n)

    def _persist_ste_counter(self, n=None):
        if not self.counter_path:
            return
        if n is None:
            n = self.ledger._n
        if n <= 0:
            return
        try:
            self.counter_path.parent.mkdir(parents=True, exist_ok=True)
            self.counter_path.write_text(str(n), encoding="utf-8")
        except OSError:
            pass

    def _load_ste_counter(self):
        if not self.counter_path or not self.counter_path.is_file():
            return
        try:
            content = self.counter_path.read_text(encoding="utf-8").strip()
            if content:
                n = int(content)
                if n > self.ledger._n:
                    self.ledger._n = n
        except (ValueError, OSError):
            pass

    def _load_ledger(self):
        if not self.ledger_path:
            return
        p = Path(self.ledger_path)
        if not p.is_file():
            return
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    name = obj.get("name")
                    kind = obj.get("kind")
                    ref = obj.get("ref")
                    work_order = obj.get("work_order")
                    item = obj.get("item")
                    qty_val = obj.get("qty")
                    uom = obj.get("uom") or ""
                    batch = obj.get("batch") or ""
                    source = obj.get("source") or ""
                    target = obj.get("target") or ""
                    against = obj.get("against") or ""

                    # Validate types per row (Item 2)
                    if not (isinstance(name, str) and isinstance(ref, str) and isinstance(work_order, str)):
                        continue
                    if not (isinstance(kind, str) and isinstance(item, str)):
                        continue
                    if not (isinstance(uom, str) and isinstance(batch, str)):
                        continue
                    if not (isinstance(source, str) and isinstance(target, str)):
                        continue

                    # Validate qty (finite, positive) (Item 3)
                    if isinstance(qty_val, bool) or not isinstance(qty_val, (int, float, str)):
                        continue
                    try:
                        qty = float(qty_val)
                    except (ValueError, TypeError):
                        continue
                    if not math.isfinite(qty) or qty <= 0:
                        continue

                    # Item 13: Skip if entry name already in ledger.entries
                    if any(e.name == name for e in self.ledger.entries):
                        continue

                    # Item 5: Raise _n
                    if name.startswith("STE-"):
                        try:
                            n = int(name.split("-")[1])
                            if n > self.ledger._n:
                                self.ledger._n = n
                        except (ValueError, IndexError):
                            pass

                    entry = Entry(
                        name=name, kind=kind, ref=ref, work_order=work_order,
                        item=item, qty=qty, uom=uom, batch=batch,
                        source=source, target=target, against=against
                    )
                    if source:
                        self.ledger.add(source, item, batch, -qty)
                    if target:
                        self.ledger.add(target, item, batch, qty)
                    self.ledger.entries.append(entry)

                    if kind == "receipt" and batch:
                        self.output_batches[batch] = (work_order, name)
                except (ValueError, TypeError, AttributeError, KeyError):
                    continue
        except OSError:
            pass

    def _scan_results(self):
        directory = None
        if self.results_dir:
            directory = Path(self.results_dir)
        elif self.log_path:
            p = Path(self.log_path).parent / "results"
            if p.is_dir():
                directory = p
        if not directory or not directory.is_dir():
            return
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    doc = (data.get("document") or "").strip()
                    if doc.startswith("STE-"):
                        n = int(doc.split("-")[1])
                        if n > self.ledger._n:
                            self.ledger._n = n
            except Exception:
                pass

    def _load_log(self):
        if not self.log_path:
            return
        p = Path(self.log_path)
        if not p.is_file():
            return

        # Item 7: Read first
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
        except OSError:
            self.log_file_error = True
            return

        # Trailing newline repair in its own try/except (Item 7)
        try:
            if p.stat().st_size > 0:
                with open(self.log_path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    last_byte = f.read(1)
                if last_byte != b"\n":
                    with open(self.log_path, "a", encoding="utf-8") as f:
                        f.write("\n")
        except OSError:
            self.log_file_error = True

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                q = obj.get("quantities")
                if "quantities" in obj and q is not None and not isinstance(q, dict):
                    continue
                row_kwargs = {f.name: obj.get(f.name, "") for f in fields(LogRow)}
                row_kwargs["quantities"] = q if isinstance(q, dict) else {}
                row_kwargs["entry"] = obj.get("entry") if isinstance(obj.get("entry"), dict) else {}
                # US-025 fields. A row written before them loads with empty values.
                for dict_field in ("received", "posted", "payload"):
                    value = obj.get(dict_field)
                    row_kwargs[dict_field] = value if isinstance(value, dict) else {}
                attempts = obj.get("attempts")
                row_kwargs["attempts"] = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 0 else 1
                replay_of = obj.get("replay_of")
                row_kwargs["replay_of"] = replay_of if isinstance(replay_of, int) and not isinstance(replay_of, bool) and replay_of >= 0 else -1
                for str_field in ("direction", "repeat_key", "timestamp"):
                    if not isinstance(row_kwargs[str_field], str):
                        row_kwargs[str_field] = ""

                # Validate types per row (Item 2)
                for str_field in ("ref", "work_order", "kind", "status", "code", "document", "attempt_id"):
                    if not isinstance(row_kwargs[str_field], str):
                        raise TypeError(f"{str_field} must be str")

                if not any(row_kwargs[f] for f in ("ref", "work_order", "kind", "status", "code", "document")):
                    continue

                row = LogRow(**row_kwargs)
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError):
                continue

            self.log.append(row)

            # Item 5: Raise _n from document of every Posted row, whether applied or not
            if row.status == "Posted" and row.document and row.document.startswith("STE-"):
                try:
                    n = int(row.document.split("-")[1])
                    if n > self.ledger._n:
                        self.ledger._n = n
                except (ValueError, IndexError):
                    pass

            # Populate _last_queued_attempts for Switch A (Item 24)
            q_key = (row.ref, row.work_order, row.kind) if row.ref else (row.attempt_id, row.work_order, row.kind)
            if row.status == "Queued":
                self._last_queued_attempts[q_key] = True
            elif row.status in ("Posted", "Business Rejected"):
                self._last_queued_attempts[q_key] = False

            if row.code == "E-DUP":
                continue

            # Fallback for stock entries ONLY if ledger.jsonl was not present
            if (not self.ledger_path or not self.ledger_path.exists()) and (
                row.status == "Posted" and row.kind in ("issue", "return", "disposition", "receipt") and row.entry
            ):
                entry_data = dict(row.entry)
                entry_data.pop("operation", None)
                if "name" in entry_data and "kind" in entry_data and "item" in entry_data:
                    try:
                        qty = float(entry_data.get("qty", 0.0))
                        if not math.isfinite(qty) or qty <= 0:
                            entry = None
                        else:
                            entry_data["qty"] = qty
                            entry = Entry(**entry_data)
                            if not isinstance(entry.name, str) or not isinstance(entry.ref, str) or not isinstance(entry.work_order, str):
                                entry = None
                            elif not isinstance(entry.item, str) or not isinstance(entry.batch, str):
                                entry = None
                            elif not isinstance(entry.source, str) or not isinstance(entry.target, str):
                                entry = None
                    except (TypeError, ValueError, AttributeError, KeyError):
                        entry = None

                    if entry is not None and not any(e.name == entry.name for e in self.ledger.entries):
                        can_apply = True
                        if entry.kind == "return":
                            has_source = any(
                                e.kind == "issue" and e.work_order == entry.work_order and e.ref == entry.against
                                for e in self.ledger.entries
                            )
                            if not has_source:
                                can_apply = False

                        if can_apply:
                            try:
                                if entry.source:
                                    self.ledger.add(entry.source, entry.item, entry.batch, -entry.qty)
                                if entry.target:
                                    self.ledger.add(entry.target, entry.item, entry.batch, entry.qty)
                                self.ledger.entries.append(entry)
                                if entry.kind == "receipt" and entry.batch:
                                    self.output_batches[entry.batch] = (entry.work_order, entry.name)
                            except (TypeError, ValueError, IndexError):
                                pass

            # Item 6: Confirmations - do NOT re-validate against weight! Applied as stored facts.
            elif row.status == "Posted" and row.kind == "confirm":
                if self._kg_out(row.work_order) <= 0:
                    continue
                operation = row.entry.get("operation") if isinstance(row.entry, dict) else None
                if operation is None:
                    operation = getattr(row, "operation", None)
                if operation is not None:
                    try:
                        op_int = int(operation)
                        if op_int in (10, 20):
                            q = row.quantities if isinstance(row.quantities, dict) else {}
                            numbers = (
                                float(q.get("completed", getattr(row, "completed", 0.0))),
                                float(q.get("rejected", getattr(row, "rejected", 0.0))),
                                float(q.get("rework", getattr(row, "rework", 0.0))),
                                float(q.get("scrap", getattr(row, "scrap", 0.0))),
                            )
                            if all(math.isfinite(n) for n in numbers) and not any(n < 0 for n in numbers):
                                self._confirm_store[(row.ref, row.work_order, op_int)] = numbers
                    except (ValueError, TypeError):
                        pass

            elif row.status == "Posted" and row.kind in WORK_ORDER_KINDS:
                key = (row.ref, row.work_order, row.kind)
                if row.kind == "cancel" and self._kg_out(row.work_order) > 0:
                    continue
                if row.kind == "resume" and self.order_state.get(row.work_order) != "on hold":
                    continue
                self.order_events.append(key)
                if row.kind == "hold":
                    self.order_state[row.work_order] = "on hold"
                elif row.kind == "cancel":
                    self.order_state[row.work_order] = "cancelled"
                elif row.kind == "resume":
                    self.order_state.pop(row.work_order, None)

    def record_attempt(self, ref, work_order, kind, status, code, document, drop_log=False, quantities=None, attempt_id="", entry=None, message=None):
        self._log(ref, work_order, kind, status, code, document, drop_log=drop_log, quantities=quantities, attempt_id=attempt_id, entry=entry, message=message)

    def record_queued(self, attempt_id, message=None):
        """Record a Queued attempt for Switch A, skipping duplicate log rows."""
        kind = (message or {}).get("kind") or ""
        ref = (message or {}).get("id") or ""
        work_order = (message or {}).get("work_order") or ""

        # Item 10: key repeat check on (id, work_order, kind), falling back to attempt_id
        q_key = (ref, work_order, kind) if ref else (attempt_id, work_order, kind)

        # Item 24: In-memory check without scanning log in reverse
        if self._last_queued_attempts.get(q_key) is True:
            return False

        self._last_queued_attempts[q_key] = True
        self.record_attempt(ref or attempt_id, work_order, kind, "Queued", "", "", drop_log=False, quantities=None, attempt_id=attempt_id, message=message if isinstance(message, dict) else None)
        return True

    def _log(self, ref, work_order, kind, status, code, document, drop_log=False, quantities=None, attempt_id="", entry=None, message=None):
        if status not in LOG_STATUSES:
            raise ValueError("Log status must be one of %s." % ", ".join(LOG_STATUSES))
        if drop_log:
            return
        aid = attempt_id or getattr(self, "_attempt_id", "")
        if message is None:
            message = getattr(self, "_message", None) or {}
        entry = dict(entry or {})
        key = _repeat_key(ref, work_order, kind, message)
        if entry.get("name"):
            posted = {name: entry.get(name) for name in ("item", "qty", "uom", "batch")}
        elif status == "Posted" and quantities:
            posted = dict(quantities)
        else:
            posted = {}
        row = LogRow(
            ref, work_order, kind, status, code, document, dict(quantities or {}), aid, entry,
            direction="ERPNext to Jinchen" if kind in OUTBOUND_KINDS else "Jinchen to ERPNext",
            repeat_key=key,
            received=_scrub({name: message[name] for name in RECEIVED_FIELDS if name in message}),
            posted=posted,
            # Every earlier row with the same key is an earlier attempt. None is ever deleted.
            attempts=1 + sum(1 for old in self.log if key and old.repeat_key == key),
            payload=_scrub(message),
            timestamp=self.clock().isoformat(timespec="seconds"),
            replay_of=self._replay_of,
        )
        self.log.append(row)
        q_key = (ref, work_order, kind) if ref else (aid, work_order, kind)
        if status == "Queued":
            self._last_queued_attempts[q_key] = True
        else:
            self._last_queued_attempts[q_key] = False

        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(row)) + "\n")
            except OSError:
                self.log_file_error = True

    def _done(self, status, code, document, message, ref, work_order, kind, drop_log=False, quantities=None, attempt_id="", entry=None):
        self._log(ref, work_order, kind, status, code, document, drop_log=drop_log, quantities=quantities, attempt_id=attempt_id, entry=entry)
        return Result(status, code, document, message)

    def process(self, message, drop_log=False, attempt_id="", transport=None):
        self._attempt_id = attempt_id
        self._message = None
        try:
            if not isinstance(message, dict):
                # No id to log against. A list, None, or a string is not a message.
                return Result("Business Rejected", "E-ITEM", "", "Unknown message.")
            # Work on a copy. The caller's dictionary is never changed.
            message = dict(message)
            # The log keeps the message as it was received (US-025).
            self._message = dict(message)
            kind = message.get("kind")
            ref_raw = message.get("id")
            wo_raw = message.get("work_order")

            if ref_raw is None or wo_raw is None or ref_raw == "" or wo_raw == "":
                # Log whichever of the two is present, so the row can still be traced.
                ref_logged = str(ref_raw) if isinstance(ref_raw, (str, int, float)) and not isinstance(ref_raw, bool) else ""
                wo_logged = str(wo_raw) if isinstance(wo_raw, (str, int, float)) and not isinstance(wo_raw, bool) else ""
                return self._done("Business Rejected", "E-QTY", "", "Id and work order are required.", ref_logged, wo_logged, kind if isinstance(kind, str) else "", drop_log)
            if (
                isinstance(ref_raw, (bool, list, dict, set, tuple))
                or isinstance(wo_raw, (bool, list, dict, set, tuple))
                or not isinstance(ref_raw, (str, int, float))
                or not isinstance(wo_raw, (str, int, float))
            ):
                return self._done("Business Rejected", "E-QTY", "", "Id and work order must be strings or numbers.", "", "", kind if isinstance(kind, str) else "", drop_log)

            ref = str(ref_raw)
            work_order = str(wo_raw)
            message["id"] = ref
            message["work_order"] = work_order

            if not isinstance(kind, str):
                return self._done("Business Rejected", "E-ITEM", "", "Unknown message.", ref, work_order, "", drop_log)

            existing = self.ledger.find(ref, work_order, kind)
            if existing and kind in ("issue", "return", "disposition", "receipt"):
                return self._done("Posted", "E-DUP", existing.name, "Already posted. No second stock entry.", ref, work_order, kind, drop_log)

            op = _operation(message)
            differing_confirm = False
            if kind == "confirm":
                if op is not False and op is not None and op in (10, 20):
                    key = (ref, work_order, op)
                    if key in self._confirm_store:
                        stored_numbers = self._confirm_store[key]
                        current_numbers = _confirm_numbers(message)
                        if current_numbers == stored_numbers:
                            return self._done("Posted", "E-DUP", "", "Already confirmed. Not stored again.", ref, work_order, "confirm", drop_log, quantities=None)
                        else:
                            differing_confirm = True

            if kind in WORK_ORDER_KINDS:
                return self._work_order(message, kind, drop_log)

            state = self.order_state.get(work_order)
            if state and kind in CLOSED_KINDS:
                return self._done("Business Rejected", "E-CLOSED", "", "Work order is " + state + ".", ref, work_order, kind, drop_log)

            if differing_confirm:
                return self._done("Business Rejected", "E-QTY", "", "Confirmation differs from the stored one. Send it with a new id.", ref, work_order, "confirm", drop_log)

            handler = {
                "issue": self._issue,
                "return": self._return,
                "confirm": self._confirm,
                "disposition": self._disposition,
                "receipt": self._receipt,
            }.get(kind)
            if handler is None:
                return self._done("Business Rejected", "E-ITEM", "", "Unknown message.", ref, work_order, kind, drop_log)

            for key in NUMBER_FIELDS:
                if key in message and message[key] is not None:
                    number = _number(message[key])
                    if number is None:
                        return self._done("Business Rejected", "E-QTY", "", "Quantity is not a number.", ref, work_order, kind, drop_log)
                    message[key] = number

            if op is False:
                return self._done("Business Rejected", "E-OP", "", "Operation is not on the demo route.", ref, work_order, kind, drop_log)
            if op is not None:
                message["operation"] = op
            if transport is not None:
                return self._process_with_transport(
                    message,
                    ref,
                    work_order,
                    kind,
                    transport,
                    drop_log=drop_log,
                    attempt_id=attempt_id,
                )
            return handler(message, drop_log)
        finally:
            self._attempt_id = ""
            self._message = None

    def _process_with_transport(
        self,
        message,
        ref,
        work_order,
        kind,
        transport,
        drop_log=False,
        attempt_id="",
    ):
        from connector.retry import TIMEOUT_CODE, TRANSIENT_EXCEPTIONS, classify_failure, next_retry_delay

        key = _repeat_key(ref, work_order, kind, message)
        if self._replay_of == -1 and key:
            for old in reversed(self.log):
                if old.repeat_key == key:
                    if old.status in ("Dead", "Business Rejected"):
                        return Result(
                            old.status,
                            old.code,
                            old.document,
                            "Already in terminal status; not resent.",
                        )
                    if old.status == "Technical Failed" and classify_failure(status=old.status, code=old.code) != "retryable":
                        return Result(
                            old.status,
                            old.code,
                            old.document,
                            "Already in terminal status; not resent.",
                        )
                    break

        max_retries = self.retry_config["max_retries"]
        for attempt_index in range(max_retries + 1):
            if attempt_index > 0:
                self.sleeper(next_retry_delay(self.retry_config, attempt_index - 1))
            try:
                raw = transport(message)
            except Exception as exc:
                code = TIMEOUT_CODE if isinstance(exc, TRANSIENT_EXCEPTIONS) else ""
                kind_cls = classify_failure(code=code, exc=exc)
                msg_text = (
                    "Transient connection failure."
                    if kind_cls == "retryable"
                    else "Transport error."
                )
                if kind_cls == "retryable" and attempt_index < max_retries:
                    self._log(
                        ref,
                        work_order,
                        kind,
                        "Technical Failed",
                        code,
                        "",
                        drop_log=drop_log,
                        attempt_id=attempt_id,
                    )
                    continue
                final_status = "Dead" if kind_cls == "retryable" else "Technical Failed"
                return self._done(
                    final_status,
                    code,
                    "",
                    msg_text,
                    ref,
                    work_order,
                    kind,
                    drop_log=drop_log,
                    attempt_id=attempt_id,
                )

            if isinstance(raw, Result):
                res_status = raw.status
                res_code = raw.code
                res_doc = raw.document
                res_msg = raw.message
            elif isinstance(raw, dict):
                res_status = str(raw.get("status", ""))
                res_code = str(raw.get("code", ""))
                res_doc = str(raw.get("document", ""))
                res_msg = str(raw.get("message", ""))
            else:
                return self._done(
                    "Technical Failed",
                    "",
                    "",
                    "Invalid transport response.",
                    ref,
                    work_order,
                    kind,
                    drop_log=drop_log,
                    attempt_id=attempt_id,
                )

            if res_status == "Posted":
                return self._done(
                    "Posted",
                    res_code,
                    res_doc,
                    res_msg or "Posted.",
                    ref,
                    work_order,
                    kind,
                    drop_log=drop_log,
                    attempt_id=attempt_id,
                )

            kind_cls = classify_failure(status=res_status, code=res_code)
            if kind_cls == "retryable":
                msg_text = res_msg or "Technical timeout."
                if attempt_index < max_retries:
                    self._log(
                        ref,
                        work_order,
                        kind,
                        "Technical Failed",
                        res_code,
                        "",
                        drop_log=drop_log,
                        attempt_id=attempt_id,
                    )
                    continue
                return self._done(
                    "Dead",
                    res_code,
                    "",
                    msg_text,
                    ref,
                    work_order,
                    kind,
                    drop_log=drop_log,
                    attempt_id=attempt_id,
                )
            if kind_cls == "business":
                return self._done(
                    "Business Rejected",
                    res_code,
                    "",
                    res_msg or "Business rejected.",
                    ref,
                    work_order,
                    kind,
                    drop_log=drop_log,
                    attempt_id=attempt_id,
                )
            return self._done(
                "Technical Failed",
                res_code,
                "",
                res_msg or "Technical failure.",
                ref,
                work_order,
                kind,
                drop_log=drop_log,
                attempt_id=attempt_id,
            )

    def _issue(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        item = message.get("item") or ""
        batch = message.get("batch") or ""
        qty = float(message.get("qty") or 0)
        source = message.get("from_warehouse") or ""
        target = message.get("to_warehouse") or ""
        if item != "ST-20":
            return self._done("Business Rejected", "E-ITEM", "", "Item does not match ST-20.", ref, work_order, "issue", drop_log)
        if message.get("uom") != "Kg":
            return self._done("Business Rejected", "E-UOM", "", "Issue unit must be Kg.", ref, work_order, "issue", drop_log)
        if source != RAW or target != WIP:
            return self._done("Business Rejected", "E-WH", "", "Issue must move from Raw Material Store to Forging WIP.", ref, work_order, "issue", drop_log)
        if batch != "H-441":
            return self._done("Business Rejected", "E-BATCH", "", "Heat does not match H-441.", ref, work_order, "issue", drop_log)
        if not math.isfinite(qty) or qty <= 0:
            return self._done("Business Rejected", "E-QTY", "", "Quantity must be greater than zero.", ref, work_order, "issue", drop_log)
        free = self.ledger.qty(RAW, "ST-20", "H-441")
        if qty > free:
            return self._done("Business Rejected", "E-STOCK", "", "Not enough free stock of H-441.", ref, work_order, "issue", drop_log)
        entry = self.ledger.submit("issue", ref, work_order, "ST-20", qty, "Kg", "H-441", RAW, WIP)
        return self._done("Posted", "", entry.name, "Issued once.", ref, work_order, "issue", drop_log, entry=asdict(entry))

    def _issued_remaining(self, issue_id, work_order):
        issued = 0.0
        returned = 0.0
        for entry in self.ledger.entries:
            if entry.work_order != work_order:
                continue
            if entry.kind == "issue" and entry.ref == issue_id:
                issued += entry.qty
            if entry.kind == "return" and entry.against == issue_id:
                returned += entry.qty
        return issued, issued - returned

    def _return(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        issue_id = message.get("against") or ""
        item = message.get("item") or ""
        batch = message.get("batch") or ""
        qty = float(message.get("qty") or 0)
        if message.get("as_scrap"):
            return self._done("Business Rejected", "E-REASON", "", "Scrap is not a return.", ref, work_order, "return", drop_log)
        uom = message.get("uom") or ""
        if uom and uom != "Kg":
            return self._done("Business Rejected", "E-UOM", "", "Return unit must be Kg.", ref, work_order, "return", drop_log)
        issued, remaining = self._issued_remaining(issue_id, work_order)
        issue_entry = next(
            (entry for entry in self.ledger.entries
             if entry.kind == "issue" and entry.ref == issue_id
             and entry.work_order == work_order),
            None,
        )
        if issued <= 0 or issue_entry is None:
            return self._done("Business Rejected", "E-ITEM", "", "Original issue was not posted.", ref, work_order, "return", drop_log)
        if item != issue_entry.item or batch != issue_entry.batch:
            return self._done("Business Rejected", "E-BATCH", "", "Return must be ST-20 heat H-441.", ref, work_order, "return", drop_log)
        if not math.isfinite(qty) or qty <= 0 or qty > remaining:
            return self._done("Business Rejected", "E-QTY", "", "Return is above the quantity still out.", ref, work_order, "return", drop_log)
        entry = self.ledger.submit("return", ref, work_order, issue_entry.item, qty, "Kg", issue_entry.batch, WIP, RAW, against=issue_id)
        return self._done("Posted", "", entry.name, "Returned once.", ref, work_order, "return", drop_log, entry=asdict(entry))

    def _confirm(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        operation = int(message.get("operation") or 0)
        numbers = [float(message.get(k) or 0) for k in CONFIRM_FIELDS]
        if operation == 30:
            return self._done("Posted", "", "", "Operation 30 is vendor work and is ignored.", ref, work_order, "confirm", drop_log)
        if operation not in (10, 20):
            return self._done("Business Rejected", "E-OP", "", "Operation is not on the demo route.", ref, work_order, "confirm", drop_log)
        if any((not math.isfinite(n) or n < 0) for n in numbers):
            return self._done("Business Rejected", "E-QTY", "", "A quantity is negative.", ref, work_order, "confirm", drop_log)
        # US-079: good, rejected, rework and scrap are pieces of the same order.
        # Kilograms never enter this sum.
        if sum(numbers) > ORDER_QTY:
            return self._done("Business Rejected", "E-QTY", "", "Completed pieces are above the order.", ref, work_order, "confirm", drop_log)
        # US-080: no progress on an order that has no metal on the floor.
        out_kg = self._kg_out(work_order)
        if out_kg <= 0:
            return self._done("Business Rejected", "E-QTY", "", "No metal was issued on this work order.", ref, work_order, "confirm", drop_log)
        # US-081: the pieces must fit the steel still out, plus the loss allowance.
        needed = sum(numbers) * self.weight["kg_per_piece"]
        allowed = out_kg * (1 + self.weight["loss_allowance"])
        if needed > allowed + 1e-9:
            return self._done("Business Rejected", "E-QTY", "", "Pieces need more steel than was issued.", ref, work_order, "confirm", drop_log)
        quantities = dict(zip(CONFIRM_FIELDS, numbers))
        key = (ref, work_order, operation)
        self._confirm_store[key] = tuple(numbers)
        return self._done("Posted", "", "", "Confirmation stored. Finished goods unchanged.", ref, work_order, "confirm", drop_log, quantities=quantities, entry={"operation": operation})

    def _kg_out(self, work_order):
        """Steel issued to this work order and not returned, in kilograms."""
        out = 0.0
        for entry in self.ledger.entries:
            if entry.work_order != work_order:
                continue
            if entry.kind == "issue":
                out += entry.qty
            elif entry.kind == "return":
                out -= entry.qty
        return out

    def _work_order(self, message, kind, drop_log):
        """US-082: hold, resume and cancel. No stock moves.

        Each has its own repeat key (id, work order, kind). Cancel is final,
        and is refused while steel is still out on the order.
        """
        ref = message["id"]
        work_order = message["work_order"]
        key = (ref, work_order, kind)
        if key in self.order_events:
            return self._done("Posted", "E-DUP", "", "Already applied.", ref, work_order, kind, drop_log)
        state = self.order_state.get(work_order)
        if state == "cancelled":
            return self._done("Business Rejected", "E-CLOSED", "", "Work order is cancelled.", ref, work_order, kind, drop_log)
        if kind == "cancel" and self._kg_out(work_order) > 0:
            return self._done("Business Rejected", "E-QTY", "", "Steel is still out. Return or scrap it before cancel.", ref, work_order, kind, drop_log)
        if kind == "resume" and state != "on hold":
            return self._done("Business Rejected", "E-OP", "", "Work order is not on hold.", ref, work_order, kind, drop_log)
        if kind == "hold":
            self.order_state[work_order] = "on hold"
            sentence = "Work order is on hold."
        elif kind == "cancel":
            self.order_state[work_order] = "cancelled"
            sentence = "Work order is cancelled."
        else:
            self.order_state.pop(work_order, None)
            sentence = "Work order resumed."
        self.order_events.append(key)
        return self._done("Posted", "", "", sentence, ref, work_order, kind, drop_log)

    def _disposition(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        item = message.get("item") or ""
        reason = message.get("reason") or ""
        decision = message.get("decision") or ""
        qty = float(message.get("qty") or 0)
        if item != "CR-100":
            return self._done("Business Rejected", "E-ITEM", "", "Disposition item is not CR-100.", ref, work_order, "disposition", drop_log)
        if reason not in REASONS or REASONS[reason] != decision:
            return self._done("Business Rejected", "E-REASON", "", "Reason is blank or does not match the decision.", ref, work_order, "disposition", drop_log)
        if not math.isfinite(qty) or qty <= 0:
            return self._done("Business Rejected", "E-QTY", "", "Quantity must be greater than zero.", ref, work_order, "disposition", drop_log)
        target = DECISION_WAREHOUSE[decision]
        entry = self.ledger.submit("disposition", ref, work_order, "CR-100", qty, "Nos", "", "", target)
        return self._done("Posted", "", entry.name, "Disposition posted. No CAPA.", ref, work_order, "disposition", drop_log, entry=asdict(entry))

    def _receipt(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        item = message.get("item") or ""
        qty = float(message.get("qty") or 0)
        batch = message.get("batch") or ""
        asked = message.get("to_warehouse") or HOLD
        if item != "CR-100":
            return self._done("Business Rejected", "E-ITEM", "", "Receipt item is not CR-100.", ref, work_order, "receipt", drop_log)
        uom = message.get("uom") or ""
        if uom and uom != "Nos":
            return self._done("Business Rejected", "E-UOM", "", "Receipt unit must be Nos.", ref, work_order, "receipt", drop_log)
        if not math.isfinite(qty) or qty <= 0:
            return self._done("Business Rejected", "E-QTY", "", "Quantity must be greater than zero.", ref, work_order, "receipt", drop_log)
        if asked == FG:
            return self._done("Business Rejected", "E-QC", "", "Saleable receipt is blocked. Hold stays with quality.", ref, work_order, "receipt", drop_log)
        if not batch:
            return self._done("Business Rejected", "E-BATCH", "", "Output batch is required.", ref, work_order, "receipt", drop_log)
        target = HOLD if self.inspection_required else FG
        entry = self.ledger.submit("receipt", ref, work_order, "CR-100", qty, "Nos", batch, "", target)
        self.output_batches[batch] = (work_order, entry.name)
        return self._done("Posted", "", entry.name, "Received into " + target + ".", ref, work_order, "receipt", drop_log, entry=asdict(entry))

    def lookup(self, batch):
        if batch not in self.output_batches:
            return {"batch": batch, "result": "missing"}
        work_order, document = self.output_batches[batch]
        issue_entry = next(
            (entry for entry in self.ledger.entries
             if entry.kind == "issue" and entry.work_order == work_order),
            None,
        )
        if issue_entry is None:
            return {"batch": batch, "result": "missing"}
        return {"batch": batch, "result": "found", "work_order": work_order, "heat": issue_entry.batch, "document": document}

    def kg(self):
        return {
            "raw": self.ledger.qty(RAW, "ST-20", "H-441"),
            "wip": self.ledger.qty(WIP, "ST-20", "H-441"),
        }

    def pieces(self):
        return {
            "hold": self.ledger.qty(HOLD, "CR-100", "") + self._batched_pieces(HOLD),
            "finished": self.ledger.qty(FG, "CR-100", "") + self._batched_pieces(FG),
            "rejection": self.ledger.qty(REJECTION, "CR-100", ""),
            "rework": self.ledger.qty(REWORK, "CR-100", ""),
            "scrap": self.ledger.qty(SCRAP, "CR-100", ""),
        }

    def _batched_pieces(self, warehouse):
        total = 0.0
        for (wh, item, batch), qty in self.ledger.bins.items():
            if wh == warehouse and item == "CR-100" and batch:
                total += qty
        return total
