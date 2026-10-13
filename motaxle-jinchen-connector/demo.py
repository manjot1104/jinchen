#!/usr/bin/env python3
"""Run the Jinchen practical POC story and print the ledger."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connector.poc import ConfigError, Connector, require_test_mode


def say(title):
    print("")
    print(title)
    print("-" * len(title))


def show(connector, label):
    kg = connector.kg()
    pc = connector.pieces()
    print("%s" % label)
    print("  H-441 kg    raw %g    WIP %g" % (kg["raw"], kg["wip"]))
    print(
        "  CR-100 pcs  hold %g  finished %g  rejection %g  rework %g  scrap %g"
        % (pc["hold"], pc["finished"], pc["rejection"], pc["rework"], pc["scrap"])
    )
    print("  stock entries %d    CAPA %d" % (len(connector.ledger.entries), connector.ledger.capa))


def main():
    try:
        require_test_mode("test", "live-secret")
        print("FAIL: live password was accepted")
        return 1
    except ConfigError as exc:
        say("Startup")
        print(str(exc))

    c = Connector()
    show(c, "Opening receipt is already in ERPNext. Jinchen did not receive it.")

    first = c.process({
        "kind": "issue", "id": "J-100", "work_order": "WO-2401",
        "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }, drop_log=True)
    say("J-100 posted, then the log write failed")
    print("  document %s" % first.document)
    show(c, "Ledger after the saved stock entry")

    again = c.process({
        "kind": "issue", "id": "J-100", "work_order": "WO-2401",
        "item": "ST-20", "qty": 120, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    })
    say("Same J-100 sent again")
    print("  code %s  document %s" % (again.code, again.document))
    show(c, "Raw store stays 380. Still one issue entry.")

    for bad, label in (
        ({"kind": "issue", "id": "J-BAD", "work_order": "WO-2401", "item": "ST20", "qty": 1, "uom": "Kg", "batch": "H-441", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP"}, "ST20"),
        ({"kind": "issue", "id": "J-HEAT", "work_order": "WO-2401", "item": "ST-20", "qty": 1, "uom": "Kg", "batch": "H441", "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP"}, "H441"),
    ):
        refused = c.process(bad)
        print("  %s refused  %s  raw still %g" % (label, refused.code, c.kg()["raw"]))

    back = c.process({
        "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-100",
        "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
    })
    say("Return R-1, 4 kg")
    print("  document %s" % back.document)
    c.process({
        "kind": "return", "id": "R-1", "work_order": "WO-2401", "against": "J-100",
        "item": "ST-20", "qty": 4, "uom": "Kg", "batch": "H-441",
    })
    show(c, "Same return again does not make 8 kg")

    c.process({
        "kind": "confirm", "id": "CF-10", "work_order": "WO-2401",
        "operation": 10, "completed": 100, "rejected": 0, "rework": 0, "scrap": 0,
    })
    say("Cutting confirmed")
    show(c, "Finished goods stays 0. Confirmation is not a receipt.")

    c.process({"kind": "disposition", "id": "D-REJ", "work_order": "WO-2401", "item": "CR-100", "qty": 2, "decision": "rejection", "reason": "forge-crack"})
    c.process({"kind": "disposition", "id": "D-RW", "work_order": "WO-2401", "item": "CR-100", "qty": 1, "decision": "rework", "reason": "rework-bend"})
    c.process({"kind": "disposition", "id": "D-SC", "work_order": "WO-2401", "item": "CR-100", "qty": 1, "decision": "scrap", "reason": "scale"})
    say("Rejection, rework, scrap")
    show(c, "No CAPA was opened")

    c.process({"kind": "receipt", "id": "C-1", "work_order": "WO-2401", "item": "CR-100", "qty": 40, "uom": "Nos", "batch": "B-1"})
    c.process({"kind": "receipt", "id": "C-2", "work_order": "WO-2401", "item": "CR-100", "qty": 56, "uom": "Nos", "batch": "B-2"})
    replay = c.process({"kind": "receipt", "id": "C-1", "work_order": "WO-2401", "item": "CR-100", "qty": 40, "uom": "Nos", "batch": "B-1"})
    blocked = c.process({"kind": "receipt", "id": "C-BAD", "work_order": "WO-2401", "item": "CR-100", "qty": 2, "uom": "Nos", "batch": "B-X", "to_warehouse": "Finished Goods Store"})
    say("Receipts")
    print("  C-1 replay %s %s" % (replay.code, replay.document))
    print("  rejected pieces to finished goods %s" % blocked.code)
    show(c, "Hold is 96, not 136. Finished goods stays 0.")

    say("Heat lookup")
    print("  " + json.dumps(c.lookup("B-1")))
    print("  " + json.dumps(c.lookup("H441")))
    print("")
    print("Done. This is the local stand-in from the POC. It did not call Jinchen and it did not call a live ERPNext site.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
