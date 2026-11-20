"""Stand-in for the Jinchen practical POC.

ERPNext is represented by an in-memory ledger. Jinchen is a JSON message.
No network call is made. Field names are the POC contract, not a signed API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


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


@dataclass
class LogRow:
    ref: str
    work_order: str
    kind: str
    status: str
    code: str
    document: str
    quantities: dict = field(default_factory=dict)


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


class Connector:
    def __init__(self, mode="test", live_password="", weight=None):
        require_test_mode(mode, live_password)
        self.mode = mode
        self.weight = dict(DEFAULT_WEIGHT)
        if weight:
            self.weight.update(weight)
        self.order_state = {}
        self.order_events = []
        self.ledger = Ledger()
        self.log = []
        self._confirm_store = {}
        self.output_batches = {}
        self.inspection_required = True
        self._seed()

    @property
    def confirmations(self):
        return [(wo, op, nums) for (ref, wo, op), nums in self._confirm_store.items()]

    @confirmations.setter
    def confirmations(self, val):
        if not val:
            self._confirm_store.clear()

    @property
    def _confirm_quantities(self):
        return {k: dict(zip(CONFIRM_FIELDS, v)) for k, v in self._confirm_store.items()}

    def _seed(self):
        self.ledger.add(RAW, "ST-20", "H-441", 500.0)

    def _log(self, ref, work_order, kind, status, code, document, drop_log, quantities=None):
        if drop_log:
            return
        self.log.append(LogRow(ref, work_order, kind, status, code, document, dict(quantities or {})))

    def _done(self, status, code, document, message, ref, work_order, kind, drop_log, quantities=None):
        self._log(ref, work_order, kind, status, code, document, drop_log, quantities)
        return Result(status, code, document, message)

    def process(self, message, drop_log=False):
        if not isinstance(message, dict):
            # No id to log against. A list, None, or a string is not a message.
            return Result("Business Rejected", "E-ITEM", "", "Unknown message.")
        # Work on a copy. The caller's dictionary is never changed.
        message = dict(message)
        kind = message.get("kind")
        ref_raw = message.get("id")
        wo_raw = message.get("work_order")

        if ref_raw is None or wo_raw is None or ref_raw == "" or wo_raw == "":
            return self._done("Business Rejected", "E-QTY", "", "Id and work order are required.", "", "", kind if isinstance(kind, str) else "", drop_log)
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
        return handler(message, drop_log)

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
        if qty <= 0:
            return self._done("Business Rejected", "E-QTY", "", "Quantity must be greater than zero.", ref, work_order, "issue", drop_log)
        free = self.ledger.qty(RAW, "ST-20", "H-441")
        if qty > free:
            return self._done("Business Rejected", "E-STOCK", "", "Not enough free stock of H-441.", ref, work_order, "issue", drop_log)
        entry = self.ledger.submit("issue", ref, work_order, "ST-20", qty, "Kg", "H-441", RAW, WIP)
        return self._done("Posted", "", entry.name, "Issued once.", ref, work_order, "issue", drop_log)

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
        if qty <= 0 or qty > remaining:
            return self._done("Business Rejected", "E-QTY", "", "Return is above the quantity still out.", ref, work_order, "return", drop_log)
        entry = self.ledger.submit("return", ref, work_order, issue_entry.item, qty, "Kg", issue_entry.batch, WIP, RAW, against=issue_id)
        return self._done("Posted", "", entry.name, "Returned once.", ref, work_order, "return", drop_log)

    def _confirm(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        operation = int(message.get("operation") or 0)
        numbers = [float(message.get(k) or 0) for k in CONFIRM_FIELDS]
        if operation == 30:
            return self._done("Posted", "", "", "Operation 30 is vendor work and is ignored.", ref, work_order, "confirm", drop_log)
        if operation not in (10, 20):
            return self._done("Business Rejected", "E-OP", "", "Operation is not on the demo route.", ref, work_order, "confirm", drop_log)
        if any(n < 0 for n in numbers):
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
        return self._done("Posted", "", "", "Confirmation stored. Finished goods unchanged.", ref, work_order, "confirm", drop_log, quantities)

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
        if qty <= 0:
            return self._done("Business Rejected", "E-QTY", "", "Quantity must be greater than zero.", ref, work_order, "disposition", drop_log)
        target = DECISION_WAREHOUSE[decision]
        entry = self.ledger.submit("disposition", ref, work_order, "CR-100", qty, "Nos", "", "", target)
        return self._done("Posted", "", entry.name, "Disposition posted. No CAPA.", ref, work_order, "disposition", drop_log)

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
        if qty <= 0:
            return self._done("Business Rejected", "E-QTY", "", "Quantity must be greater than zero.", ref, work_order, "receipt", drop_log)
        if asked == FG:
            return self._done("Business Rejected", "E-QC", "", "Saleable receipt is blocked. Hold stays with quality.", ref, work_order, "receipt", drop_log)
        if not batch:
            return self._done("Business Rejected", "E-BATCH", "", "Output batch is required.", ref, work_order, "receipt", drop_log)
        target = HOLD if self.inspection_required else FG
        entry = self.ledger.submit("receipt", ref, work_order, "CR-100", qty, "Nos", batch, "", target)
        self.output_batches[batch] = (work_order, entry.name)
        return self._done("Posted", "", entry.name, "Received into " + target + ".", ref, work_order, "receipt", drop_log)

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
