"""Stand-in for the Jinchen practical POC.

ERPNext is represented by an in-memory ledger. Jinchen is a JSON message.
No network call is made. Field names are the POC contract, not a signed API.
"""

from __future__ import annotations

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
DECISION_WAREHOUSE = {
    "rejection": REJECTION,
    "rework": REWORK,
    "scrap": SCRAP,
}


class ConfigError(Exception):
    pass


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


@dataclass
class LogRow:
    ref: str
    work_order: str
    kind: str
    status: str
    code: str
    document: str


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
    capa: int = 0
    _n: int = 0

    def qty(self, warehouse, item, batch=""):
        return self.bins.get((warehouse, item, batch), 0.0)

    def add(self, warehouse, item, batch, qty):
        key = (warehouse, item, batch)
        self.bins[key] = self.bins.get(key, 0.0) + qty

    def submit(self, kind, ref, work_order, item, qty, uom, batch, source, target):
        self._n += 1
        entry = Entry(
            "STE-%04d" % self._n, kind, ref, work_order, item, qty, uom, batch, source, target
        )
        if source:
            self.add(source, item, batch, -qty)
        if target:
            self.add(target, item, batch, qty)
        self.entries.append(entry)
        return entry

    def find(self, ref, work_order):
        for entry in self.entries:
            if entry.ref == ref and entry.work_order == work_order:
                return entry
        return None


class Connector:
    def __init__(self, mode="test", live_password=""):
        require_test_mode(mode, live_password)
        self.mode = mode
        self.ledger = Ledger()
        self.log = []
        self.confirmations = []
        self.output_batches = {}
        self.inspection_required = True
        self._seed()

    def _seed(self):
        self.ledger.add(RAW, "ST-20", "H-441", 500.0)

    def _log(self, ref, work_order, kind, status, code, document, drop_log):
        if drop_log:
            return
        self.log.append(LogRow(ref, work_order, kind, status, code, document))

    def _done(self, status, code, document, message, ref, work_order, kind, drop_log):
        self._log(ref, work_order, kind, status, code, document, drop_log)
        return Result(status, code, document, message)

    def process(self, message, drop_log=False):
        kind = message.get("kind")
        ref = message.get("id") or ""
        work_order = message.get("work_order") or ""
        if not ref or not work_order:
            return self._done("Business Rejected", "E-QTY", "", "Id and work order are required.", ref, work_order, kind or "", drop_log)
        existing = self.ledger.find(ref, work_order)
        if existing and kind in ("issue", "return", "disposition", "receipt"):
            return self._done("Posted", "E-DUP", existing.name, "Already posted. No second stock entry.", ref, work_order, kind, drop_log)
        handler = {
            "issue": self._issue,
            "return": self._return,
            "confirm": self._confirm,
            "disposition": self._disposition,
            "receipt": self._receipt,
        }.get(kind)
        if handler is None:
            return self._done("Business Rejected", "E-ITEM", "", "Unknown message.", ref, work_order, kind or "", drop_log)
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
            if entry.work_order != work_order or entry.item != "ST-20":
                continue
            if entry.kind == "issue" and entry.ref == issue_id:
                issued += entry.qty
            if entry.kind == "return" and entry.batch == "H-441":
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
        issued, remaining = self._issued_remaining(issue_id, work_order)
        if issued <= 0:
            return self._done("Business Rejected", "E-ITEM", "", "Original issue was not posted.", ref, work_order, "return", drop_log)
        if item != "ST-20" or batch != "H-441":
            return self._done("Business Rejected", "E-BATCH", "", "Return must be ST-20 heat H-441.", ref, work_order, "return", drop_log)
        if qty <= 0 or qty > remaining:
            return self._done("Business Rejected", "E-QTY", "", "Return is above the quantity still out.", ref, work_order, "return", drop_log)
        entry = self.ledger.submit("return", ref, work_order, "ST-20", qty, "Kg", "H-441", WIP, RAW)
        return self._done("Posted", "", entry.name, "Returned once.", ref, work_order, "return", drop_log)

    def _confirm(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        operation = int(message.get("operation") or 0)
        numbers = [float(message.get(k) or 0) for k in ("completed", "rejected", "rework", "scrap")]
        if operation == 30:
            return self._done("Posted", "", "", "Operation 30 is vendor work and is ignored.", ref, work_order, "confirm", drop_log)
        if operation not in (10, 20):
            return self._done("Business Rejected", "E-OP", "", "Operation is not on the demo route.", ref, work_order, "confirm", drop_log)
        if any(n < 0 for n in numbers):
            return self._done("Business Rejected", "E-QTY", "", "A quantity is negative.", ref, work_order, "confirm", drop_log)
        if numbers[0] > 100:
            return self._done("Business Rejected", "E-QTY", "", "Completed pieces are above the order.", ref, work_order, "confirm", drop_log)
        self.confirmations.append((work_order, operation, tuple(numbers)))
        return self._done("Posted", "", "", "Confirmation stored. Finished goods unchanged.", ref, work_order, "confirm", drop_log)

    def _disposition(self, message, drop_log):
        ref = message["id"]
        work_order = message["work_order"]
        reason = message.get("reason") or ""
        decision = message.get("decision") or ""
        qty = float(message.get("qty") or 0)
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
        issued = any(e.kind == "issue" and e.work_order == work_order and e.batch == "H-441" for e in self.ledger.entries)
        if not issued:
            return {"batch": batch, "result": "missing"}
        return {"batch": batch, "result": "found", "work_order": work_order, "heat": "H-441", "document": document}

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
