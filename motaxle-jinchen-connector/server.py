#!/usr/bin/env python3
"""Local shop-floor site for the Jinchen practical POC.

One work order, an in-memory ledger, and the stock rules from the POC.
Nothing is sent to Jinchen or to a live ERPNext site.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connector.poc import Connector

HOST = "127.0.0.1"
PORT = 8765
LOCK = threading.Lock()

def whole(value):
    if value == int(value):
        return int(value)
    return value


class Desk:
    def __init__(self):
        self.connector = Connector()
        self.attempts = []
        self.switch_a = False
        self.switch_b = False
        self.released = False

    def reset(self):
        self.connector = Connector()
        self.attempts = []
        self.switch_a = False
        self.switch_b = False
        self.released = False

    def run(self, action_id):
        spec = ACTIONS.get(action_id)
        if spec is None:
            return None
        return self.apply(action_id, spec["message"], spec.get("drop_log", False))

    def apply(self, label, message, drop_log=False):
        kind = (message or {}).get("kind") or ""
        if self.switch_a and kind in ("issue", "return", "disposition", "receipt"):
            row = {
                "id": label or message.get("id") or kind,
                "status": "Queued",
                "code": "",
                "document": "",
                "message": "Switch A is on. Logged only. Stock was not posted. Turning Switch A off does not post the queue.",
            }
            self.attempts.append(row)
            return row
        if "qty" in message and message["qty"] != "":
            try:
                message["qty"] = float(message["qty"])
            except (TypeError, ValueError):
                message["qty"] = 0
        if "operation" in message and message["operation"] != "":
            try:
                message["operation"] = int(message["operation"])
            except (TypeError, ValueError):
                message["operation"] = 0
        if "completed" in message and message["completed"] != "":
            try:
                message["completed"] = float(message["completed"])
            except (TypeError, ValueError):
                message["completed"] = 0
        result = self.connector.process(message, drop_log=drop_log)
        text = result.message
        if drop_log and not result.code:
            text = text + " The stock entry was saved, then the log write failed."
        row = {
            "id": label or message.get("id") or kind,
            "status": result.status,
            "code": result.code,
            "document": result.document,
            "message": text,
        }
        self.attempts.append(row)
        return row

    def set_switch(self, name, on):
        if name == "A":
            self.switch_a = bool(on)
        elif name == "B":
            self.switch_b = bool(on)
        else:
            return False
        return True

    def release(self):
        if self.switch_b:
            row = {
                "id": "release",
                "status": "Stopped",
                "code": "",
                "document": "",
                "message": "Switch B is on. WO-2401 was not released.",
            }
        else:
            self.released = True
            row = {
                "id": "release",
                "status": "Sent",
                "code": "",
                "document": "",
                "message": "WO-2401 release was recorded on this desk. Jinchen was not called.",
            }
        self.attempts.append(row)
        return row

    def payload(self, result=None):
        connector = self.connector
        kg = connector.kg()
        pieces = connector.pieces()
        entries = []
        issued = 0
        returned = 0
        for entry in connector.ledger.entries:
            if entry.kind == "issue":
                issued += entry.qty
            if entry.kind == "return":
                returned += entry.qty
            entries.append({
                "name": entry.name,
                "kind": entry.kind,
                "ref": entry.ref,
                "item": entry.item,
                "qty": whole(entry.qty),
                "uom": entry.uom,
                "batch": entry.batch,
                "source": entry.source or "",
                "target": entry.target or "",
            })
        done_ops = set()
        for work_order, operation, _numbers in connector.confirmations:
            if work_order == "WO-2401":
                done_ops.add(operation)
        operations = [
            {"op": 10, "name": "Cutting", "where": "On this floor", "state": "Confirmed" if 10 in done_ops else "Waiting"},
            {"op": 20, "name": "Forging", "where": "On this floor", "state": "Confirmed" if 20 in done_ops else "Waiting"},
            {"op": 30, "name": "Heat treatment", "where": "Vendor", "state": "Not issued from this desk"},
        ]
        posted_refs = {entry["ref"] for entry in entries}
        waiting = [
            ("issue", "J-100" not in posted_refs),
            ("return", "R-1" not in posted_refs),
            ("confirm-10", 10 not in done_ops),
            ("confirm-20", 20 not in done_ops),
            ("reject", "D-REJ" not in posted_refs),
            ("rework", "D-RW" not in posted_refs),
            ("scrap", "D-SC" not in posted_refs),
            ("c1", "C-1" not in posted_refs),
            ("c2", "C-2" not in posted_refs),
        ]
        suggested = ""
        for action_id, still in waiting:
            if still:
                suggested = action_id
                break
        bins = {
            "raw": whole(kg["raw"]),
            "wip": whole(kg["wip"]),
            "hold": whole(pieces["hold"]),
            "finished": whole(pieces["finished"]),
            "rejection": whole(pieces["rejection"]),
            "rework": whole(pieces["rework"]),
            "scrap": whole(pieces["scrap"]),
        }
        accounted = bins["hold"] + bins["rejection"] + bins["rework"] + bins["scrap"]
        return {
            "bins": bins,
            "entries": entries,
            "capa": connector.ledger.capa,
            "attempts": self.attempts,
            "catalog": CATALOG,
            "suggested": suggested,
            "issued": whole(issued),
            "returned": whole(returned),
            "accounted": accounted,
            "operations": operations,
            "switch_a": self.switch_a,
            "switch_b": self.switch_b,
            "released": self.released,
            "result": result,
        }


DESK = Desk()


def msg_issue(ref, item, batch):
    return {
        "kind": "issue", "id": ref, "work_order": "WO-2401",
        "item": item, "qty": 120 if ref == "J-100" else 1, "uom": "Kg", "batch": batch,
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }


ACTIONS = {
    "issue": {"drop_log": True, "message": msg_issue("J-100", "ST-20", "H-441")},
    "issue-again": {"message": msg_issue("J-100", "ST-20", "H-441")},
    "bad-item": {"message": msg_issue("J-BAD", "ST20", "H-441")},
    "bad-heat": {"message": msg_issue("J-HEAT", "ST-20", "H441")},
    "over-issue": {"message": {
        "kind": "issue", "id": "J-BIG", "work_order": "WO-2401",
        "item": "ST-20", "qty": 999, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }},
    "bad-uom": {"message": {
        "kind": "issue", "id": "J-UOM", "work_order": "WO-2401",
        "item": "ST-20", "qty": 1, "uom": "Nos", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }},
    "bad-wh": {"message": {
        "kind": "issue", "id": "J-WH", "work_order": "WO-2401",
        "item": "ST-20", "qty": 1, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Finished Goods Store", "to_warehouse": "Forging WIP",
    }},
    "scrap-return": {"message": {
        "kind": "return", "id": "R-SCRAP", "work_order": "WO-2401", "against": "J-100",
        "item": "ST-20", "qty": 1, "uom": "Kg", "batch": "H-441", "as_scrap": True,
    }},
    "return": {"message": {
        "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-100",
        "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
    }},
    "return-again": {"message": {
        "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-100",
        "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
    }},
    "confirm-10": {"message": {
        "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
        "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
    }},
    "confirm-20": {"message": {
        "kind": "confirm", "id": "CF-20", "work_order": "WO-2401",
        "operation": 20, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
    }},
    "confirm-30": {"message": {
        "kind": "confirm", "id": "CF-30", "work_order": "WO-2401",
        "operation": 30, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
    }},
    "confirm-40": {"message": {
        "kind": "confirm", "id": "CF-40", "work_order": "WO-2401",
        "operation": 40, "completed": 10, "rejected": 0, "rework": 0, "scrap": 0,
    }},
    "over-confirm": {"message": {
        "kind": "confirm", "id": "CF-BIG", "work_order": "WO-2401",
        "operation": 10, "completed": 150, "rejected": 0, "rework": 0, "scrap": 0,
    }},
    "reject": {"message": {
        "kind": "disposition", "id": "D-REJ", "work_order": "WO-2401",
        "item": "CR-100", "qty": 2, "decision": "rejection", "reason": "forge-crack",
    }},
    "rework": {"message": {
        "kind": "disposition", "id": "D-RW", "work_order": "WO-2401",
        "item": "CR-100", "qty": 1, "decision": "rework", "reason": "rework-bend",
    }},
    "scrap": {"message": {
        "kind": "disposition", "id": "D-SC", "work_order": "WO-2401",
        "item": "CR-100", "qty": 1, "decision": "scrap", "reason": "scale",
    }},
    "c1": {"message": {
        "kind": "receipt", "id": "C-1", "work_order": "WO-2401",
        "item": "CR-100", "qty": 40, "uom": "Nos", "batch": "B-1",
    }},
    "c2": {"message": {
        "kind": "receipt", "id": "C-2", "work_order": "WO-2401",
        "item": "CR-100", "qty": 56, "uom": "Nos", "batch": "B-2",
    }},
    "c1-again": {"message": {
        "kind": "receipt", "id": "C-1", "work_order": "WO-2401",
        "item": "CR-100", "qty": 40, "uom": "Nos", "batch": "B-1",
    }},
    "c-bad": {"message": {
        "kind": "receipt", "id": "C-BAD", "work_order": "WO-2401",
        "item": "CR-100", "qty": 2, "uom": "Nos", "batch": "B-X",
        "to_warehouse": "Finished Goods Store",
    }},
    "no-batch": {"message": {
        "kind": "receipt", "id": "C-NONE", "work_order": "WO-2401",
        "item": "CR-100", "qty": 1, "uom": "Nos", "batch": "",
    }},
}

CATALOG = [
    {"id": "issue", "screen": "material", "group": "Issue to the floor", "title": "Issue J-100", "lines": [["Item", "ST-20"], ["Qty", "120 kg"], ["Heat", "H-441"], ["From", "Raw Material Store"], ["To", "Forging WIP"]]},
    {"id": "issue-again", "screen": "material", "group": "Checks", "title": "Send J-100 again", "lines": [["Same id", "J-100"], ["Expect", "No second 120 kg"]]},
    {"id": "bad-item", "screen": "material", "group": "Checks", "title": "Wrong item code", "lines": [["Typed", "ST20"], ["Real code", "ST-20"]]},
    {"id": "bad-heat", "screen": "material", "group": "Checks", "title": "Wrong heat", "lines": [["Typed", "H441"], ["Real heat", "H-441"]]},
    {"id": "over-issue", "screen": "material", "group": "Checks", "title": "Issue 999 kg", "lines": [["Qty", "999 kg"], ["Expect", "Not enough free stock"]]},
    {"id": "bad-uom", "screen": "material", "group": "Checks", "title": "Issue in pieces", "lines": [["Unit", "Nos"], ["Expect", "Kilograms only"]]},
    {"id": "bad-wh", "screen": "material", "group": "Checks", "title": "Issue from finished goods", "lines": [["From", "Finished Goods Store"], ["Expect", "Wrong warehouse"]]},
    {"id": "scrap-return", "screen": "material", "group": "Checks", "title": "Return it as scrap", "lines": [["Flag", "Scrap"], ["Expect", "Not a return"]]},
    {"id": "return", "screen": "material", "group": "Unused steel", "title": "Return R-1", "lines": [["Against", "J-100"], ["Qty", "4 kg"], ["Heat", "H-441"]]},
    {"id": "return-again", "screen": "material", "group": "Checks", "title": "Send R-1 again", "lines": [["Same id", "R-1"], ["Expect", "Not 8 kg"]]},
    {"id": "confirm-10", "screen": "operations", "group": "This floor", "title": "Confirm cutting", "lines": [["Operation", "10"], ["Completed", "100"], ["Effect on finished goods", "None"]]},
    {"id": "confirm-20", "screen": "operations", "group": "This floor", "title": "Confirm forging", "lines": [["Operation", "20"], ["Completed", "100"], ["Effect on finished goods", "None"]]},
    {"id": "confirm-30", "screen": "operations", "group": "Outside this desk", "title": "Confirm heat treatment", "lines": [["Operation", "30"], ["Where", "Vendor"], ["Expect", "Ignored"]]},
    {"id": "confirm-40", "screen": "operations", "group": "Outside this desk", "title": "Confirm an unknown operation", "lines": [["Operation", "40"], ["Expect", "Not on the route"]]},
    {"id": "over-confirm", "screen": "operations", "group": "Outside this desk", "title": "Confirm 150 pieces", "lines": [["Completed", "150"], ["Order", "100"], ["Expect", "Too many"]]},
    {"id": "reject", "screen": "quality", "group": "Split the 100 pieces", "title": "Reject 2", "lines": [["Reason", "Forge crack"], ["Warehouse", "Rejection"], ["CAPA", "Not opened"]]},
    {"id": "rework", "screen": "quality", "group": "Split the 100 pieces", "title": "Rework 1", "lines": [["Reason", "Bend"], ["Warehouse", "Rework"]]},
    {"id": "scrap", "screen": "quality", "group": "Split the 100 pieces", "title": "Scrap 1", "lines": [["Reason", "Scale"], ["Unit", "1 piece, not kg"]]},
    {"id": "c1", "screen": "quality", "group": "Good pieces", "title": "Receive batch B-1", "lines": [["Message", "C-1"], ["Qty", "40"], ["To", "Final QC Hold"]]},
    {"id": "c2", "screen": "quality", "group": "Good pieces", "title": "Receive batch B-2", "lines": [["Message", "C-2"], ["Qty", "56"], ["Hold after this", "96"]]},
    {"id": "c1-again", "screen": "quality", "group": "Checks", "title": "Send C-1 again", "lines": [["Same id", "C-1"], ["Expect", "Hold stays 96"]]},
    {"id": "c-bad", "screen": "quality", "group": "Checks", "title": "Put rejects in finished goods", "lines": [["Qty", "2"], ["To", "Finished Goods Store"], ["Expect", "Blocked"]]},
    {"id": "no-batch", "screen": "quality", "group": "Checks", "title": "Receive with no batch", "lines": [["Batch", "Blank"], ["Expect", "Batch required"]]},
]

SCREENS = {
    "today": "Today",
    "order": "Work order",
    "material": "Material",
    "operations": "Operations",
    "quality": "Quality",
    "trace": "Trace",
    "log": "Message log",
}


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Motaxle floor</title>
<style>
  :root { --navy:#132f3e; --ink:#1c2630; --muted:#5e6c78; --line:#d7e1e8; --paper:#eef3f6; --ok:#0c6a42; --wait:#8a5a10; --stop:#8d3b12; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: Helvetica, Arial, sans-serif; color: var(--ink); background: var(--paper); }
  .app { display:grid; grid-template-columns: 210px 1fr; min-height: 100vh; }
  aside { background: var(--navy); color:#fff; padding: 20px 14px; }
  .brand { font-size: 18px; font-weight: 700; }
  .brand span { display:block; margin-top: 4px; color:#b7c8d2; font-size: 12px; font-weight: 400; line-height: 1.4; }
  nav { margin-top: 22px; }
  nav button { display:block; width:100%; text-align:left; background:transparent; color:#d5e3eb; border:0; border-radius:8px; padding:9px 10px; margin-bottom:2px; font-size:15px; cursor:pointer; }
  nav button.on { background:#21485c; color:#fff; }
  main { padding: 18px 22px 36px; }
  .top { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
  h1 { margin:0; font-size:26px; }
  .job { margin:4px 0 0; color: var(--muted); font-size:14px; }
  button.go, button.send { background: var(--navy); color:#fff; border:0; border-radius:8px; padding:8px 12px; cursor:pointer; font-size:14px; }
  button.ghost { background:#fff; color: var(--navy); border:1px solid var(--navy); border-radius:8px; padding:8px 12px; cursor:pointer; }
  .strip { display:grid; grid-template-columns: repeat(7, 1fr); gap:8px; margin:14px 0; }
  .bin { background:#fff; border:1px solid var(--line); border-radius:10px; padding:8px 10px; }
  .bin span { display:block; color: var(--muted); font-size:11px; }
  .bin b { display:block; margin-top:2px; font-size:22px; font-variant-numeric: tabular-nums; }
  .bin em { font-style:normal; color: var(--muted); font-size:11px; }
  .flash { background:#fff; border:1px solid var(--line); border-left:5px solid var(--navy); border-radius:8px; padding:10px 12px; margin-bottom:12px; }
  .flash.stop { border-left-color: var(--stop); }
  .flash.ok { border-left-color: var(--ok); }
  .grid2 { display:grid; grid-template-columns: 1.2fr .8fr; gap:12px; }
  .card, .panel { background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  .card h2, .panel h2 { margin:0 0 8px; font-size:16px; }
  .route { display:grid; grid-template-columns: repeat(5, 1fr); gap:8px; margin-top:8px; }
  .step { border:1px solid var(--line); border-radius:10px; padding:10px; background:#f8fbfc; }
  .step b { display:block; }
  .step span { color: var(--muted); font-size:12px; }
  .pill { display:inline-block; margin-top:6px; font-size:12px; font-weight:700; padding:2px 7px; border-radius:999px; background:#eef2f5; }
  .pill.ok { background:#e5f6ee; color: var(--ok); }
  .pill.wait { background:#fff4e2; color: var(--wait); }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th, td { text-align:left; padding:7px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color: var(--muted); font-size:12px; font-weight:600; }
  .msg { border:1px solid var(--line); border-radius:10px; padding:12px; margin-top:10px; background:#fff; }
  .msg.next { border-color:#1d6b62; box-shadow: 0 0 0 2px rgba(29,107,98,.15); }
  .msg h3 { margin:0 0 6px; font-size:16px; }
  .msg .row { display:flex; justify-content:space-between; gap:12px; font-size:14px; padding:3px 0; }
  .msg .row span { color: var(--muted); }
  .msg .foot { display:flex; justify-content:space-between; align-items:center; margin-top:8px; }
  .group { margin:16px 0 0; color: var(--muted); font-size:12px; letter-spacing:.04em; text-transform:uppercase; }
  .badge { font-size:12px; font-weight:700; }
  .badge.ok { color: var(--ok); }
  .badge.stop { color: var(--stop); }
  .lookup { display:flex; gap:8px; }
  .lookup input { flex:1; border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:16px; }
  .answer { margin-top:12px; padding:12px; border-radius:10px; background:#f6fafb; }
  .muted { color: var(--muted); font-size:13px; }
  .hidden { display:none; }
  .bar { display:flex; height:12px; background:#e6eef2; border-radius:99px; overflow:hidden; margin-top:10px; }
  .bar i { display:block; height:12px; }
  .bar .hold { background:#1d6b62; }
  .bar .rej { background:#8d3b12; }
  .bar .rw { background:#c4841d; }
  .bar .sc { background:#7d8b96; }
  .fields { display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; margin:10px 0; }
  .fields label { display:flex; flex-direction:column; gap:4px; font-size:12px; color: var(--muted); }
  .fields input, .fields select { border:1px solid var(--line); border-radius:8px; padding:8px; font-size:14px; color: var(--ink); }
  .badge.wait, .flash.wait { color: var(--wait); }
  .flash.wait { border-left-color: var(--wait); }
  #log-filter { width:100%; border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:15px; }
  ul.plain { margin:8px 0 0; padding-left:18px; }
  ul.plain li { margin:4px 0; }
  @media (max-width: 980px) {
    .app { grid-template-columns: 1fr; }
    .strip, .route, .grid2 { grid-template-columns: 1fr 1fr; }
    nav { display:flex; flex-wrap:wrap; gap:4px; }
    nav button { width:auto; }
  }
</style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand">Motaxle floor<span>Local stand-in for one job. Not the Jinchen product.</span></div>
    <nav id="nav"></nav>
  </aside>
  <main>
    <div class="top">
      <div>
        <h1 id="heading">Today</h1>
        <p class="job">Motaxle Demo Company | WO-2401 | Cylinder rod CR-100 | Heat H-441</p>
      </div>
      <button class="ghost" id="reset" type="button">Reset job</button>
    </div>
    <div class="strip" id="strip"></div>
    <div id="flash"></div>
    <section id="composer" class="panel hidden">
      <h2>Send your own message</h2>
      <p class="muted">Type a different id, quantity, heat, or warehouse. The same checks run. An id still posts only once.</p>
      <form id="own">
        <div class="fields">
          <label>Kind
            <select id="m-kind">
              <option value="issue">Issue</option>
              <option value="return">Return</option>
              <option value="confirm">Confirm operation</option>
              <option value="disposition">Disposition</option>
              <option value="receipt">Receipt</option>
            </select>
          </label>
          <label>Id <input id="m-id" value="J-200"></label>
          <label data-for="issue return receipt disposition">Item <input id="m-item" value="ST-20"></label>
          <label data-for="issue return receipt disposition">Qty <input id="m-qty" value="10"></label>
          <label data-for="issue return receipt">Unit <input id="m-uom" value="Kg"></label>
          <label data-for="issue return receipt">Heat or batch <input id="m-batch" value="H-441"></label>
          <label data-for="issue">From <input id="m-from" value="Raw Material Store"></label>
          <label data-for="issue">To <input id="m-to" value="Forging WIP"></label>
          <label data-for="return">Against <input id="m-against" value="J-100"></label>
          <label data-for="return">Mark as scrap <input id="m-scrap" type="checkbox"></label>
          <label data-for="confirm">Operation <input id="m-op" value="10"></label>
          <label data-for="confirm">Completed pieces <input id="m-done" value="100"></label>
          <label data-for="disposition">Decision
            <select id="m-decision">
              <option value="rejection">rejection</option>
              <option value="rework">rework</option>
              <option value="scrap">scrap</option>
            </select>
          </label>
          <label data-for="disposition">Reason
            <select id="m-reason">
              <option value="forge-crack">forge-crack</option>
              <option value="rework-bend">rework-bend</option>
              <option value="scale">scale</option>
              <option value="wrong">wrong</option>
            </select>
          </label>
          <label data-for="receipt">Destination
            <select id="m-dest">
              <option value="">Final QC Hold</option>
              <option value="Finished Goods Store">Finished Goods Store</option>
            </select>
          </label>
        </div>
        <button class="go" type="submit">Send message</button>
      </form>
    </section>
    <section id="view-today"></section>
    <section id="view-order" class="hidden"></section>
    <section id="view-material" class="hidden"></section>
    <section id="view-operations" class="hidden"></section>
    <section id="view-quality" class="hidden"></section>
    <section id="view-trace" class="hidden"></section>
    <section id="logtools" class="panel hidden">
      <h2>Find in the log</h2>
      <input id="log-filter" placeholder="E-DUP, H-441, STE-0001">
    </section>
    <section id="view-log" class="hidden"></section>
  </main>
</div>
<script>
const screens = ["today","order","material","operations","quality","trace","log"];
const titles = {today:"Today", order:"Work order", material:"Material", operations:"Operations", quality:"Quality", trace:"Trace", log:"Message log"};
const bins = [["raw","Raw store","kg"],["wip","Forging WIP","kg"],["hold","QC hold","pcs"],["finished","Finished","pcs"],["rejection","Rejection","pcs"],["rework","Rework","pcs"],["scrap","Scrap","pcs"]];
let state = null;
let view = "today";
let lastLookup = null;

document.getElementById("nav").innerHTML = screens.map(function (name) {
  return '<button type="button" data-tab="' + name + '">' + titles[name] + "</button>";
}).join("");

function latest(id) {
  let found = null;
  state.attempts.forEach(function (row) { if (row.id === id) found = row; });
  return found;
}

function toneOf(row) {
  if (row.code) return "stop";
  if (row.status === "Posted" || row.status === "Sent") return "ok";
  return "wait";
}

function badge(row) {
  if (!row) return "";
  const label = (row.code || row.status) + (row.document ? " " + row.document : "");
  return '<span class="badge ' + toneOf(row) + '">' + label + "</span>";
}

function cards(screen) {
  let group = "";
  return state.catalog.filter(function (item) { return item.screen === screen; }).map(function (item) {
    let head = "";
    if (item.group !== group) {
      group = item.group;
      head = '<div class="group">' + group + "</div>";
    }
    const cls = item.id === state.suggested ? "msg next" : "msg";
    const lines = item.lines.map(function (line) {
      return '<div class="row"><span>' + line[0] + "</span><b>" + line[1] + "</b></div>";
    }).join("");
    return head + '<article class="' + cls + '"><h3>' + item.title + '</h3>' + lines +
      '<div class="foot">' + (badge(latest(item.id)) || '<span class="muted">Not sent</span>') +
      '<button class="send" type="button" data-act="' + item.id + '">Send</button></div></article>';
  }).join("");
}

function paint() {
  document.getElementById("strip").innerHTML = bins.map(function (spec) {
    return '<div class="bin"><span>' + spec[1] + '</span><b>' + state.bins[spec[0]] + '</b><em>' + spec[2] + "</em></div>";
  }).join("");
  const flash = document.getElementById("flash");
  if (state.result) {
    const tone = toneOf(state.result);
    flash.innerHTML = '<div class="flash ' + tone + '">' + state.result.message + "</div>";
  } else {
    flash.innerHTML = "";
  }
  const next = state.catalog.filter(function (item) { return item.id === state.suggested; })[0];
  const route = state.operations.map(function (op) {
    const tone = op.state === "Confirmed" ? "ok" : "wait";
    return '<div class="step"><span>' + op.op + '</span><b>' + op.name + '</b><div class="pill ' + tone + '">' + op.state + "</div></div>";
  }).join("") + '<div class="step"><span>QC</span><b>Final hold</b><div class="pill ' + (state.bins.hold ? "ok" : "wait") + '">' + state.bins.hold + " pieces</div></div>";
  document.getElementById("view-today").innerHTML =
    '<div class="grid2"><section class="card"><h2>WO-2401 is the only job on this desk</h2>' +
    '<p>100 cylinder rods. Each rod needs 1.20 kg of ST-20. The heat on the bar is H-441. Opening stock is 500 kg, already received in ERPNext.</p>' +
    '<div class="route">' + route + "</div></section>" +
    '<section class="card"><h2>On the ledger</h2><table>' +
    "<tr><td>Steel issued</td><td>" + state.issued + " kg</td></tr>" +
    "<tr><td>Steel returned</td><td>" + state.returned + " kg</td></tr>" +
    "<tr><td>Pieces accounted</td><td>" + state.accounted + " of 100</td></tr>" +
    "<tr><td>Finished goods</td><td>" + state.bins.finished + "</td></tr>" +
    "<tr><td>CAPA</td><td>" + state.capa + "</td></tr>" +
    "<tr><td>Connector documents</td><td>" + state.entries.length + "</td></tr></table>" +
    '<div class="bar"><i class="hold" style="width:' + state.bins.hold + '%"></i><i class="rej" style="width:' + state.bins.rejection + '%"></i><i class="rw" style="width:' + state.bins.rework + '%"></i><i class="sc" style="width:' + state.bins.scrap + '%"></i></div>' +
    '<p class="muted">The bar is the 100 pieces: hold, rejection, rework, scrap.</p>' +
    (next ? '<p><button class="go" type="button" data-tab="' + next.screen + '">Next: ' + next.title + "</button></p>" : '<p class="muted">The walk for this job is done. Open Trace and look up B-1.</p>') +
    "</section></div>" +
    '<section class="panel" style="margin-top:12px"><h2>Switches</h2>' +
    '<p class="muted">Switch A stops stock posts and still writes the log. Turning it off does not post the queue. Switch B stops the release. Neither one calls Jinchen.</p>' +
    '<p><button class="ghost" type="button" data-switch="A">Switch A is ' + (state.switch_a ? "on" : "off") + '</button> ' +
    '<button class="ghost" type="button" data-switch="B">Switch B is ' + (state.switch_b ? "on" : "off") + '</button> ' +
    '<button class="send" type="button" data-release="1">Release WO-2401</button></p>' +
    '<p class="muted">' + (state.released ? "Release is recorded on this desk." : "Not released.") + "</p></section>" +
    '<section class="panel" style="margin-top:12px"><h2>Not on this desk</h2><ul class="plain">' +
    "<li>No call to Jinchen. The API document is not agreed, so these buttons are the local stand-in.</li>" +
    "<li>No purchase, invoice, dispatch, packing, or tool life.</li>" +
    "<li>No CAPA. Quality release of the hold stays a click inside ERPNext.</li></ul></section>";

  document.getElementById("view-order").innerHTML =
    '<section class="panel"><h2>WO-2401</h2><table>' +
    "<tr><th>Field</th><th>Value</th></tr>" +
    "<tr><td>Company</td><td>Motaxle Demo Company</td></tr>" +
    "<tr><td>Item</td><td>CR-100 Cylinder rod</td></tr>" +
    "<tr><td>Order qty</td><td>100 pieces</td></tr>" +
    "<tr><td>BOM</td><td>ST-20, 1.20 kg each, 120 kg for the order</td></tr>" +
    "<tr><td>Heat</td><td>H-441, batch tracked</td></tr>" +
    "<tr><td>Issued</td><td>" + state.issued + " kg</td></tr>" +
    "<tr><td>Returned</td><td>" + state.returned + " kg</td></tr>" +
    "<tr><td>Still in forging WIP</td><td>" + state.bins.wip + " kg</td></tr></table></section>" +
    '<section class="panel" style="margin-top:12px"><h2>Route</h2><table><tr><th>Op</th><th>Name</th><th>Where</th><th>Status</th></tr>' +
    state.operations.map(function (op) {
      return "<tr><td>" + op.op + "</td><td>" + op.name + "</td><td>" + op.where + "</td><td>" + op.state + "</td></tr>";
    }).join("") + '</table><p class="muted">Confirming an operation does not receive the rod into stock.</p></section>';

  document.getElementById("view-material").innerHTML = '<section class="panel"><h2>Steel for heat H-441</h2><p class="muted">Raw store and forging WIP are kilograms. A repeated message must not move the metal twice.</p>' + cards("material") + "</section>";
  document.getElementById("view-operations").innerHTML = '<section class="panel"><h2>Report what the floor finished</h2><p class="muted">Cutting and forging can be confirmed. Heat treatment is vendor work. Finished goods does not change on this screen.</p>' + cards("operations") + "</section>";
  document.getElementById("view-quality").innerHTML = '<section class="panel"><h2>Where the 100 pieces go</h2><p class="muted">Good pieces wait in Final QC Hold. Rejected pieces must not become finished goods. Pieces are not added to kilograms.</p>' + cards("quality") + "</section>";

  const batches = state.entries.filter(function (entry) { return entry.kind === "receipt"; });
  document.getElementById("view-trace").innerHTML =
    '<section class="panel"><h2>Read the heat from a rod batch</h2>' +
    '<form class="lookup" id="find"><input id="batch" placeholder="B-1" autocomplete="off"><button class="go" type="submit">Look up</button></form>' +
    '<div id="answer"></div>' +
    '<p><button class="ghost" type="button" data-batch="B-1">Look up B-1</button> <button class="ghost" type="button" data-batch="B-2">Look up B-2</button> <button class="ghost" type="button" data-batch="H441">Look up H441</button></p>' +
    '<p class="muted">Received batches: ' + (batches.map(function (entry) { return entry.batch + " (" + entry.qty + ")"; }).join(", ") || "none yet") + ".</p></section>";
  const find = document.getElementById("find");
  if (find) find.onsubmit = lookup;
  if (lastLookup) document.getElementById("answer").innerHTML = answerHtml(lastLookup);

  const query = (document.getElementById("log-filter").value || "").toLowerCase();
  const shown = state.attempts.filter(function (row) {
    const blob = (row.id + " " + row.code + " " + row.status + " " + row.document + " " + row.message).toLowerCase();
    return !query || blob.indexOf(query) >= 0;
  });
  document.getElementById("view-log").innerHTML = '<section class="panel"><h2>Messages this desk has handled</h2>' +
    (shown.length ? '<table><tr><th>Message</th><th>Result</th><th>Document</th><th>What happened</th></tr>' +
      shown.map(function (row) {
        return "<tr><td>" + row.id + "</td><td>" + (row.code || row.status) + "</td><td>" + (row.document || "-") + "</td><td>" + row.message + "</td></tr>";
      }).join("") + "</table>" : '<p class="muted">Nothing in this filter.</p>') +
    (state.entries.length ? '<h2 style="margin-top:16px">Stock documents</h2><table><tr><th>Document</th><th>Message</th><th>From</th><th>To</th><th>Qty</th></tr>' +
      state.entries.map(function (entry) {
        return "<tr><td>" + entry.name + "</td><td>" + entry.ref + "</td><td>" + (entry.source || "-") + "</td><td>" + (entry.target || "-") + "</td><td>" + entry.qty + " " + entry.uom + "</td></tr>";
      }).join("") + "</table>" : "") +
    "</section>";

  document.querySelectorAll("#nav button").forEach(function (button) {
    button.className = button.getAttribute("data-tab") === view ? "on" : "";
  });
  screens.forEach(function (name) {
    document.getElementById("view-" + name).className = name === view ? "" : "hidden";
  });
  document.getElementById("heading").textContent = titles[view];
  const own = view === "material" || view === "operations" || view === "quality";
  document.getElementById("composer").className = own ? "panel" : "panel hidden";
  document.getElementById("logtools").className = view === "log" ? "panel" : "panel hidden";
}

function show(name) {
  view = name;
  const kindFor = {material: "issue", operations: "confirm", quality: "receipt"};
  if (kindFor[name]) {
    document.getElementById("m-kind").value = kindFor[name];
    syncComposer();
  }
  paint();
}

function syncComposer() {
  const kind = document.getElementById("m-kind").value;
  document.querySelectorAll("#composer [data-for]").forEach(function (el) {
    const ok = el.getAttribute("data-for").split(" ").indexOf(kind) >= 0;
    el.className = ok ? "" : "hidden";
  });
}

async function load() {
  state = await (await fetch("/api/state")).json();
  paint();
}

async function send(id) {
  state = await (await fetch("/api/act", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({id:id})})).json();
  paint();
}

function answerHtml(data) {
  if (!data) return "";
  if (data.result === "found") {
    return '<div class="answer"><b>Found ' + data.heat + '</b><p>Batch ' + data.batch + ' belongs to ' + data.work_order + '. Stock document ' + data.document + '.</p></div>';
  }
  return '<div class="answer"><b>Missing</b><p>' + (data.batch || "") + ' does not match a received rod. The desk does not invent a heat.</p></div>';
}

async function lookup(event) {
  event.preventDefault();
  const batch = document.getElementById("batch").value.trim();
  lastLookup = await (await fetch("/api/lookup?batch=" + encodeURIComponent(batch))).json();
  const box = document.getElementById("answer");
  if (box) box.innerHTML = answerHtml(lastLookup);
}

async function toggleSwitch(name) {
  const on = name === "A" ? !state.switch_a : !state.switch_b;
  state = await (await fetch("/api/switch", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({name: name, on: on})})).json();
  paint();
}

async function releaseJob() {
  state = await (await fetch("/api/release", {method:"POST"})).json();
  paint();
}

document.body.addEventListener("click", function (event) {
  const act = event.target.closest("[data-act]");
  if (act) { send(act.getAttribute("data-act")); return; }
  const tab = event.target.closest("[data-tab]");
  if (tab) { show(tab.getAttribute("data-tab")); return; }
  const sw = event.target.closest("[data-switch]");
  if (sw) { toggleSwitch(sw.getAttribute("data-switch")); return; }
  if (event.target.closest("[data-release]")) { releaseJob(); return; }
  const chip = event.target.closest("[data-batch]");
  if (chip) {
    const input = document.getElementById("batch");
    if (input) input.value = chip.getAttribute("data-batch");
    lookup({preventDefault: function () {}});
  }
});
document.getElementById("reset").onclick = async function () {
  if (!window.confirm("Start WO-2401 again from 500 kg?")) return;
  lastLookup = null;
  state = await (await fetch("/api/reset", {method:"POST"})).json();
  show("today");
};
document.getElementById("m-kind").onchange = syncComposer;
document.getElementById("log-filter").oninput = function () { if (state) paint(); };
document.getElementById("own").onsubmit = async function (event) {
  event.preventDefault();
  const kind = document.getElementById("m-kind").value;
  const message = {kind: kind, work_order: "WO-2401", id: document.getElementById("m-id").value.trim()};
  if (kind === "issue" || kind === "return" || kind === "receipt" || kind === "disposition") {
    message.item = document.getElementById("m-item").value.trim();
    message.qty = Number(document.getElementById("m-qty").value);
  }
  if (kind === "issue" || kind === "return" || kind === "receipt") {
    message.uom = document.getElementById("m-uom").value.trim();
    message.batch = document.getElementById("m-batch").value.trim();
  }
  if (kind === "issue") {
    message.from_warehouse = document.getElementById("m-from").value.trim();
    message.to_warehouse = document.getElementById("m-to").value.trim();
  }
  if (kind === "return") {
    message.against = document.getElementById("m-against").value.trim();
    message.as_scrap = document.getElementById("m-scrap").checked;
  }
  if (kind === "confirm") {
    message.operation = Number(document.getElementById("m-op").value);
    message.completed = Number(document.getElementById("m-done").value);
    message.rejected = 0;
    message.rework = 0;
    message.scrap = 0;
  }
  if (kind === "disposition") {
    message.decision = document.getElementById("m-decision").value;
    message.reason = document.getElementById("m-reason").value;
  }
  if (kind === "receipt" && document.getElementById("m-dest").value) {
    message.to_warehouse = document.getElementById("m-dest").value;
  }
  state = await (await fetch("/api/message", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({message: message})})).json();
  paint();
};
syncComposer();
load();
</script>
</body>
</html>
"""


def read_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw:
        return {}
    return json.loads(raw)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_html(PAGE)
            return
        if path == "/api/state":
            with LOCK:
                payload = DESK.payload()
            self.send_json(payload)
            return
        if path == "/api/lookup":
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            batch = ""
            for part in query.split("&"):
                if part.startswith("batch="):
                    batch = unquote(part.split("=", 1)[1])
            with LOCK:
                found = DESK.connector.lookup(batch)
            self.send_json(found)
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/reset":
            with LOCK:
                DESK.reset()
                payload = DESK.payload()
            self.send_json(payload)
            return
        if path == "/api/act":
            try:
                body = read_body(self)
            except json.JSONDecodeError:
                self.send_json({"error": "Bad JSON"}, 400)
                return
            with LOCK:
                result = DESK.run(body.get("id") or "")
                if result is None:
                    self.send_json({"error": "Unknown message"}, 400)
                    return
                payload = DESK.payload(result)
            self.send_json(payload)
            return
        if path == "/api/message":
            try:
                body = read_body(self)
            except json.JSONDecodeError:
                self.send_json({"error": "Bad JSON"}, 400)
                return
            message = body.get("message") or {}
            if not isinstance(message, dict):
                self.send_json({"error": "Message must be an object"}, 400)
                return
            with LOCK:
                result = DESK.apply(message.get("id") or "custom", message, False)
                payload = DESK.payload(result)
            self.send_json(payload)
            return
        if path == "/api/switch":
            try:
                body = read_body(self)
            except json.JSONDecodeError:
                self.send_json({"error": "Bad JSON"}, 400)
                return
            with LOCK:
                if not DESK.set_switch(body.get("name") or "", bool(body.get("on"))):
                    self.send_json({"error": "Unknown switch"}, 400)
                    return
                payload = DESK.payload()
            self.send_json(payload)
            return
        if path == "/api/release":
            with LOCK:
                result = DESK.release()
                payload = DESK.payload(result)
            self.send_json(payload)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Open http://%s:%d" % (HOST, PORT), flush=True)
    print("Ctrl+C stops it.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
